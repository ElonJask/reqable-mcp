import gzip
import json
import socket
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from reqable_mcp.config import Config
from reqable_mcp.ingest_server import IngestServerManager
from reqable_mcp.storage import RequestStorage


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ingest_server_roundtrip(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token=None,
        max_body_size=102400,
        max_report_size=10 * 1024 * 1024,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    manager.start()
    try:
        payload = {
            "request": {"method": "GET", "url": "https://example.com/ping"},
            "response": {"status": 200},
        }
        raw = json.dumps(payload).encode("utf-8")
        req = Request(
            url=f"http://127.0.0.1:{port}/report",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert storage.total_requests() == 1
    finally:
        manager.stop()


def test_ingest_server_reports_http_listener_and_websocket_capture(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token=None,
        max_body_size=102400,
        max_report_size=10 * 1024 * 1024,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    storage.ingest_payload(
        {
            "log": {
                "entries": [
                    {
                        "_resourceType": "websocket",
                        "startedDateTime": "2026-02-28T09:01:00.000Z",
                        "request": {
                            "method": "GET",
                            "url": "wss://ws.example.com/socket",
                            "headers": [
                                {"name": "Connection", "value": "Upgrade"},
                                {"name": "Upgrade", "value": "websocket"},
                            ],
                        },
                        "response": {
                            "status": 101,
                            "headers": [
                                {"name": "Connection", "value": "Upgrade"},
                                {"name": "Upgrade", "value": "websocket"},
                            ],
                        },
                        "_webSocketMessages": [
                            {"type": "send", "opcode": 1, "data": '{"ping":true}'},
                            {"type": "receive", "opcode": 1, "data": '{"pong":true}'},
                        ],
                    }
                ]
            }
        },
        source="har_import",
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    status = manager.status(include_events=False)

    assert status["ingest_transport"] == "http"
    assert status["supports_raw_websocket_listener"] is False
    assert status["supports_websocket_capture"] is True
    assert status["supports_incremental_websocket_events"] is True
    assert status["ws_events_path"] == "/ws/events"
    assert status["total_requests"] == 1
    assert status["total_websocket_sessions"] == 1
    assert status["total_websocket_messages"] == 2


def test_ingest_server_websocket_events_endpoint_roundtrip(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token=None,
        max_body_size=102400,
        max_report_size=10 * 1024 * 1024,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    manager.start()
    try:
        payload = {
            "events": [
                {
                    "session_id": "ws-live-1",
                    "event_type": "open",
                    "request": {
                        "method": "GET",
                        "url": "wss://ws.example.com/live",
                    },
                    "response": {"status": 101},
                },
                {
                    "session_id": "ws-live-1",
                    "event_type": "message",
                    "seq": 1,
                    "direction": "inbound",
                    "opcode": 1,
                    "payload_text": '{"event":"hello"}',
                },
            ]
        }
        raw = json.dumps(payload).encode("utf-8")
        req = Request(
            url=f"http://127.0.0.1:{port}/ws/events",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["ingest_mode"] == "ws_events"
        assert body["accepted_events"] == 2
        assert storage.total_websocket_sessions() == 1
        assert storage.total_websocket_messages() == 1
    finally:
        manager.stop()


def test_ingest_server_websocket_events_supports_top_level_defaults(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token=None,
        max_body_size=102400,
        max_report_size=10 * 1024 * 1024,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    manager.start()
    try:
        payload = {
            "session_id": "ws-default-2",
            "request": {"method": "GET", "url": "wss://ws.example.com/default-2"},
            "response": {"status": 101},
            "events": [
                {"event_type": "open"},
                {
                    "event_type": "message",
                    "seq": 1,
                    "direction": "inbound",
                    "opcode": 1,
                    "payload_text": '{"event":"ok"}',
                },
            ],
        }
        raw = json.dumps(payload).encode("utf-8")
        req = Request(
            url=f"http://127.0.0.1:{port}/ws/events",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert body["accepted_events"] == 2
        assert body["rejected_events"] == 0
        assert storage.total_websocket_sessions() == 1
        assert storage.total_websocket_messages() == 1
    finally:
        manager.stop()


def test_ingest_server_health_endpoint_is_redacted(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token="token",
        max_body_size=102400,
        max_report_size=10 * 1024 * 1024,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    manager.start()
    try:
        req = Request(
            url=f"http://127.0.0.1:{port}/health",
            method="GET",
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert "db_path" not in body
        assert "last_error" not in body
    finally:
        manager.stop()


def test_ingest_server_rejects_large_decoded_payload(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token=None,
        max_body_size=102400,
        max_report_size=256,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    manager.start()
    try:
        payload = {
            "request": {"method": "POST", "url": "https://example.com/ping"},
            "response": {"status": 200},
            "padding": "a" * 2000,
        }
        raw = json.dumps(payload).encode("utf-8")
        compressed = gzip.compress(raw)
        assert len(compressed) < cfg.max_report_size
        assert len(raw) > cfg.max_report_size

        req = Request(
            url=f"http://127.0.0.1:{port}/report",
            data=compressed,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        )
        try:
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                status_code = resp.status
        except HTTPError as exc:
            status_code = exc.code
            body = json.loads(exc.read().decode("utf-8"))
    finally:
        manager.stop()

    assert status_code == 413
    assert body["ok"] is False
    assert body["error"] == "payload_too_large_after_decode"
