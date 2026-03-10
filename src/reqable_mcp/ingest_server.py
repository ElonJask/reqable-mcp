"""Local HTTP ingest server for Reqable Report Server payloads."""

from __future__ import annotations

import gzip
import hmac
import json
import logging
import threading
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .config import Config
from .storage import RequestStorage

LOGGER = logging.getLogger(__name__)


class IngestServerManager:
    def __init__(self, config: Config, storage: RequestStorage):
        self.config = config
        self.storage = storage
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started_at: datetime | None = None
        self._accepted_payloads = 0
        self._failed_payloads = 0
        self._last_error: str | None = None

    def _decode_body(self, raw: bytes, content_encoding: str | None) -> bytes:
        if not content_encoding:
            return raw

        data = raw
        encodings = [item.strip().lower() for item in content_encoding.split(",") if item.strip()]
        for encoding in reversed(encodings):
            if encoding in ("identity", ""):
                continue
            if encoding in ("gzip", "x-gzip"):
                data = gzip.decompress(data)
                continue
            if encoding == "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
                continue
            if encoding == "br":
                try:
                    import brotli  # type: ignore
                except ImportError as exc:
                    raise ValueError(
                        "brotli payload received but 'brotli' is not installed"
                    ) from exc
                data = brotli.decompress(data)
                continue
            if encoding in ("zstd", "zstandard"):
                try:
                    import zstandard  # type: ignore
                except ImportError as exc:
                    raise ValueError(
                        "zstd payload received but 'zstandard' is not installed"
                    ) from exc
                data = zstandard.ZstdDecompressor().decompress(data)
                continue
            raise ValueError(f"unsupported content-encoding: {encoding}")
        return data

    def _build_handler(self):
        manager = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: Any) -> None:
                LOGGER.debug("ingest_http: " + fmt, *args)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/health":
                    self._send_json(404, {"ok": False, "error": "not_found"})
                    return
                self._send_json(200, manager.public_health_status())

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                route = parsed.path
                if route not in {manager.config.ingest_path, manager.config.ws_events_path}:
                    self._send_json(404, {"ok": False, "error": "not_found"})
                    return

                if manager.config.ingest_token:
                    token = self.headers.get("X-Reqable-Token", "")
                    if not hmac.compare_digest(token, manager.config.ingest_token):
                        manager._failed_payloads += 1
                        self._send_json(403, {"ok": False, "error": "forbidden"})
                        return

                length_header = self.headers.get("Content-Length")
                if not length_header:
                    manager._failed_payloads += 1
                    self._send_json(411, {"ok": False, "error": "content_length_required"})
                    return

                try:
                    content_length = int(length_header)
                except ValueError:
                    manager._failed_payloads += 1
                    self._send_json(400, {"ok": False, "error": "invalid_content_length"})
                    return
                if content_length > manager.config.max_report_size:
                    manager._failed_payloads += 1
                    self._send_json(
                        413,
                        {
                            "ok": False,
                            "error": "payload_too_large",
                            "max_report_size": manager.config.max_report_size,
                        },
                    )
                    return

                try:
                    raw = self.rfile.read(content_length)
                    if len(raw) != content_length:
                        raise ValueError(
                            "incomplete body: "
                            f"expected {content_length} bytes, got {len(raw)} bytes"
                        )
                    decoded = manager._decode_body(raw, self.headers.get("Content-Encoding"))
                    if len(decoded) > manager.config.max_report_size:
                        manager._failed_payloads += 1
                        self._send_json(
                            413,
                            {
                                "ok": False,
                                "error": "payload_too_large_after_decode",
                                "max_report_size": manager.config.max_report_size,
                            },
                        )
                        return
                    payload = json.loads(decoded.decode("utf-8", errors="replace"))
                except Exception as exc:  # broad for protocol hardening
                    manager._failed_payloads += 1
                    manager._last_error = str(exc)
                    manager.storage.add_event(
                        "error",
                        "Decode report payload failed",
                        {"error": str(exc)},
                    )
                    self._send_json(
                        400,
                        {"ok": False, "error": "invalid_payload", "detail": str(exc)},
                    )
                    return

                try:
                    if route == manager.config.ws_events_path:
                        result = manager.storage.ingest_websocket_events(
                            payload=payload,
                            source="report_server_ws_events",
                            platform=self.headers.get("x-reqable-platform"),
                            reporter_host=self.headers.get("x-reporter-host"),
                        )
                    else:
                        result = manager.storage.ingest_payload(
                            payload=payload,
                            source="report_server",
                            platform=self.headers.get("x-reqable-platform"),
                            reporter_host=self.headers.get("x-reporter-host"),
                        )
                except Exception as exc:  # broad for robustness
                    manager._failed_payloads += 1
                    manager._last_error = str(exc)
                    manager.storage.add_event(
                        "error",
                        "Ingest payload failed",
                        {
                            "error": str(exc),
                            "route": route,
                        },
                    )
                    self._send_json(
                        500,
                        {"ok": False, "error": "ingest_failed", "detail": str(exc)},
                    )
                    return

                manager._accepted_payloads += 1
                manager._last_error = None
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "ingest_mode": (
                            "ws_events"
                            if route == manager.config.ws_events_path
                            else "report"
                        ),
                        **result,
                    },
                )

        return Handler

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            server = ThreadingHTTPServer(
                (self.config.ingest_host, self.config.ingest_port),
                self._build_handler(),
            )
            thread = threading.Thread(
                target=server.serve_forever,
                name="reqable-ingest-server",
                daemon=True,
            )
            thread.start()
            self._server = server
            self._thread = thread
            self._started_at = datetime.now(timezone.utc)
            self.storage.add_event(
                "info",
                "Ingest server started",
                {"host": self.config.ingest_host, "port": self.config.ingest_port},
            )

    def stop(self) -> None:
        with self._lock:
            if not self._server:
                return
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            self._thread = None
            self.storage.add_event("info", "Ingest server stopped")

    def ensure_started(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.start()

    def status(self, include_events: bool = True) -> dict[str, Any]:
        listening = bool(self._thread and self._thread.is_alive())
        result: dict[str, Any] = {
            "ok": True,
            "mode": "local",
            "listening": listening,
            "ingest_transport": "http",
            "supports_raw_websocket_listener": False,
            "supports_websocket_capture": True,
            "supports_incremental_websocket_events": True,
            "ingest_url": self.config.ingest_url,
            "ws_events_url": self.config.ws_events_url,
            "host": self.config.ingest_host,
            "port": self.config.ingest_port,
            "path": self.config.ingest_path,
            "ws_events_path": self.config.ws_events_path,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "accepted_payloads": self._accepted_payloads,
            "failed_payloads": self._failed_payloads,
            "last_error": self._last_error,
            "db_path": str(self.storage.db_path),
            "total_requests": self.storage.total_requests(),
            "total_websocket_sessions": self.storage.total_websocket_sessions(),
            "total_websocket_messages": self.storage.total_websocket_messages(),
        }
        if include_events:
            result["recent_events"] = self.storage.recent_events(limit=10)
        return result

    def public_health_status(self) -> dict[str, Any]:
        status = self.status(include_events=False)
        status.pop("db_path", None)
        status.pop("last_error", None)
        return status
