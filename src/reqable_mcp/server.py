"""Reqable MCP server (local mode first)."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import config
from .ingest_server import IngestServerManager
from .models import DetailLevel, RequestFull, WebSocketMessage, extract_json_structure
from .storage import RequestStorage

LOGGER = logging.getLogger(__name__)

mcp = FastMCP("reqable")
storage = RequestStorage(
    db_path=config.db_path,
    max_body_size=config.max_body_size,
    summary_body_preview_length=config.summary_body_preview_length,
    key_body_preview_length=config.key_body_preview_length,
    retention_days=config.retention_days,
)
ingest_server = IngestServerManager(config=config, storage=storage)

VALID_DETAILS = {item.value for item in DetailLevel}
VALID_SEARCH_AREAS = {"all", "url", "request_body", "response_body", "raw_entry", "raw"}
VALID_LANGUAGES = {"python", "javascript", "typescript", "curl"}
VALID_PYTHON_FRAMEWORKS = {"requests", "httpx"}
VALID_JS_FRAMEWORKS = {"fetch", "axios"}
VALID_WS_DIRECTIONS = {"inbound", "outbound", "unknown"}
VALID_WS_MESSAGE_TYPES = {"text", "binary", "close", "ping", "pong"}
START_ERROR_COOLDOWN_SECONDS = 10.0
_last_start_error: tuple[str, float] | None = None


def _json_response(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _bounded_limit(value: int, default: int, maximum: int) -> int:
    if value <= 0:
        return default
    return min(value, maximum)


def _ensure_ingest_started() -> None:
    global _last_start_error
    try:
        ingest_server.ensure_started()
    except OSError as exc:
        message = str(exc)
        now = time.monotonic()
        if _last_start_error:
            last_message, last_ts = _last_start_error
            if message == last_message and (now - last_ts) < START_ERROR_COOLDOWN_SECONDS:
                return
        _last_start_error = (message, now)
        LOGGER.warning("Failed to start ingest server: %s", exc)
        storage.add_event("error", "Failed to start ingest server", {"error": message})


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ws_close_info(message: WebSocketMessage) -> tuple[int | None, str | None]:
    candidates: list[Any] = []
    if isinstance(message.raw, dict):
        candidates.append(message.raw.get("payload"))
        candidates.append(message.raw)
    candidates.append(message.data_json)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        code = _safe_int(candidate.get("code"))
        reason_raw = candidate.get("reason")
        reason = None
        if reason_raw is not None:
            reason_text = str(reason_raw).strip()
            reason = reason_text or None
        if code is not None or reason is not None:
            return code, reason
    return None, None


def _message_preview(message: WebSocketMessage, max_length: int = 160) -> str | None:
    if not message.data:
        return None
    return message.data[:max_length]


def _message_summary(message: WebSocketMessage) -> dict[str, Any]:
    close_code = message.close_code
    close_reason = message.close_reason
    summary = {
        "seq": message.seq,
        "direction": message.direction,
        "timestamp": message.timestamp,
        "opcode": message.opcode,
        "message_type": message.message_type,
        "has_json": message.data_json is not None,
        "is_binary": message.is_binary,
        "body_truncated": message.body_truncated,
        "data_preview": _message_preview(message),
        "close_code": close_code,
        "close_reason": close_reason,
        "raw_present": message.raw is not None,
    }
    if isinstance(message.data_json, dict):
        summary["top_level_keys"] = sorted(str(key) for key in list(message.data_json.keys())[:20])
        summary["json_structure"] = extract_json_structure(message.data_json)
    return summary


@mcp.tool()
def ingest_status(auto_start: bool = True) -> str:
    """Check local ingest server status."""
    if auto_start:
        _ensure_ingest_started()
    return _json_response(ingest_server.status(include_events=True))


@mcp.tool()
def health_report(detail: bool = True, sample_limit: int = 5) -> str:
    """Return ingest status plus data quality checks for WebSocket payloads."""
    normalized_sample_limit = _bounded_limit(sample_limit, default=5, maximum=50)
    return _json_response(
        {
            "ingest": ingest_server.status(include_events=detail),
            "websocket_health": storage.websocket_health_report(
                sample_limit=normalized_sample_limit
            ),
        }
    )


@mcp.tool()
def import_har(file_path: str) -> str:
    """Import a HAR file (or line-delimited JSON entries) as fallback."""
    path = Path(file_path).expanduser()
    if not path.exists():
        return _json_response({"ok": False, "error": f"File not found: {path}"})
    if not path.is_file():
        return _json_response({"ok": False, "error": f"Not a file: {path}"})

    try:
        result = storage.import_har_file(
            path,
            max_file_size_bytes=config.max_import_file_size,
        )
        return _json_response({"ok": True, "file": str(path), **result})
    except Exception as exc:  # broad for robust tool UX
        storage.add_event("error", "import_har failed", {"file": str(path), "error": str(exc)})
        return _json_response({"ok": False, "error": str(exc)})


@mcp.tool()
def list_requests(
    limit: int = 20,
    detail: str = "summary",
    domain: str | None = None,
    method: str | None = None,
    status: int | None = None,
) -> str:
    """List recent HTTP/WebSocket handshake requests captured from Reqable reports."""
    normalized_limit = _bounded_limit(limit, default=20, maximum=100)
    normalized_detail = detail.lower().strip()
    detail_level = (
        DetailLevel(normalized_detail)
        if normalized_detail in VALID_DETAILS
        else DetailLevel.SUMMARY
    )

    requests = storage.get_requests(
        limit=normalized_limit,
        detail_level=detail_level,
        domain=domain,
        method=method,
        status_code=status,
    )
    return _json_response([request.model_dump() for request in requests])


@mcp.tool()
def list_websocket_sessions(limit: int = 20, detail: str = "summary", domain: str | None = None) -> str:
    """List captured WebSocket sessions."""
    normalized_limit = _bounded_limit(limit, default=20, maximum=100)
    normalized_detail = detail.lower().strip()
    detail_level = (
        DetailLevel(normalized_detail)
        if normalized_detail in VALID_DETAILS
        else DetailLevel.SUMMARY
    )
    requests = storage.get_requests(
        limit=normalized_limit,
        detail_level=detail_level,
        domain=domain,
        websocket_only=True,
    )
    return _json_response([request.model_dump() for request in requests])


@mcp.tool()
def get_request(request_id: str, include_body: bool = True) -> str:
    """Get detailed information for a single request."""
    if not request_id.strip():
        return _json_response({"error": "request_id is required"})
    detail_level = DetailLevel.FULL if include_body else DetailLevel.KEY
    request = storage.get_request_by_id(request_id=request_id, detail_level=detail_level)
    if request is None:
        return _json_response({"error": f"Request {request_id} not found"})
    result = request.model_dump()
    if isinstance(request, RequestFull) and not request.is_websocket:
        result["curl_command"] = request.to_curl()
    return _json_response(result)


@mcp.tool()
def get_websocket_session(request_id: str, include_messages: bool = True) -> str:
    """Get WebSocket session details and messages by request ID."""
    if not request_id.strip():
        return _json_response({"error": "request_id is required"})
    detail_level = DetailLevel.FULL if include_messages else DetailLevel.KEY
    request = storage.get_request_by_id(request_id=request_id, detail_level=detail_level)
    if request is None:
        return _json_response({"error": f"Request {request_id} not found"})
    result = request.model_dump()
    if not result.get("is_websocket"):
        return _json_response({
            "error": f"Request {request_id} is not a WebSocket session",
            "request": result,
        })
    if not include_messages and "websocket_messages" in result:
        result.pop("websocket_messages", None)
    return _json_response(result)


@mcp.tool()
def search_requests(keyword: str, search_in: str = "all", limit: int = 20) -> str:
    """Search requests by keyword in URL, bodies, or raw uploaded entry (`raw` / `raw_entry`)."""
    if not keyword.strip():
        return _json_response({"error": "keyword is required"})
    normalized_search_in = search_in if search_in in VALID_SEARCH_AREAS else "all"
    normalized_limit = _bounded_limit(limit, default=20, maximum=100)
    results = storage.search(
        keyword=keyword,
        search_in=normalized_search_in,
        limit=normalized_limit,
    )
    return _json_response(results)


@mcp.tool()
def search_websocket_messages(
    keyword: str = "",
    direction: str | None = None,
    message_type: str | None = None,
    opcode: int | None = None,
    request_id: str | None = None,
    domain: str | None = None,
    close_code: int | None = None,
    has_json: bool | None = None,
    limit: int = 20,
) -> str:
    """Search WebSocket messages by keyword and precise frame filters."""
    normalized_direction = direction if direction in VALID_WS_DIRECTIONS else None
    normalized_message_type = (message_type or "").strip().lower() or None
    if normalized_message_type not in VALID_WS_MESSAGE_TYPES:
        normalized_message_type = None
    normalized_limit = _bounded_limit(limit, default=20, maximum=100)
    results = storage.search_websocket_messages(
        keyword=keyword,
        direction=normalized_direction,
        message_type=normalized_message_type,
        opcode=opcode,
        request_id=request_id,
        domain=domain,
        close_code=close_code,
        has_json=has_json,
        limit=normalized_limit,
    )
    return _json_response(results)


@mcp.tool()
def repair_websocket_messages(max_rows: int = 2000, dry_run: bool = False) -> str:
    """Backfill missing WebSocket message fields from raw frames."""
    normalized_max_rows = _bounded_limit(max_rows, default=2000, maximum=5000)
    result = storage.repair_websocket_messages(max_rows=normalized_max_rows, dry_run=dry_run)
    return _json_response(result)


@mcp.tool()
def analyze_websocket_session(request_id: str, sample_limit: int = 3) -> str:
    """Analyze a WebSocket session and summarize frame directions, types, JSON shapes, and close events."""
    if not request_id.strip():
        return _json_response({"error": "request_id is required"})
    normalized_sample_limit = _bounded_limit(sample_limit, default=3, maximum=10)
    request = storage.get_request_by_id(request_id=request_id, detail_level=DetailLevel.FULL)
    if request is None:
        return _json_response({"error": f"Request {request_id} not found"})
    if not isinstance(request, RequestFull) or not request.is_websocket:
        return _json_response({"error": f"Request {request_id} is not a WebSocket session"})

    messages = request.websocket_messages
    direction_counts = Counter(message.direction for message in messages)
    message_type_counts = Counter((message.message_type or "unknown") for message in messages)
    opcode_counts = Counter(str(message.opcode) if message.opcode is not None else "unknown" for message in messages)
    close_code_counts: Counter[str] = Counter()
    top_level_keys = Counter()
    semantic_markers = {
        field: Counter()
        for field in ("event", "type", "action", "command", "topic", "method")
    }
    samples: dict[str, list[dict[str, Any]]] = {"outbound": [], "inbound": [], "unknown": []}
    close_events: list[dict[str, Any]] = []
    json_message_count = 0
    raw_message_count = 0

    for message in messages:
        if message.raw is not None:
            raw_message_count += 1
        if message.data_json is not None:
            json_message_count += 1
        if len(samples.setdefault(message.direction, [])) < normalized_sample_limit:
            samples[message.direction].append(_message_summary(message))
        close_code_value = message.close_code
        close_reason = message.close_reason
        if close_code_value is not None:
            close_code_counts[str(close_code_value)] += 1
        if close_code_value is not None or close_reason is not None or message.message_type == "close":
            close_events.append(
                {
                    "seq": message.seq,
                    "direction": message.direction,
                    "timestamp": message.timestamp,
                    "opcode": message.opcode,
                    "close_code": close_code_value,
                    "close_reason": close_reason,
                    "raw_present": message.raw is not None,
                }
            )
        if isinstance(message.data_json, dict):
            top_level_keys.update(str(key) for key in message.data_json.keys())
            for field, counter in semantic_markers.items():
                value = message.data_json.get(field)
                if isinstance(value, (str, int, float, bool)):
                    counter[str(value)] += 1

    result = {
        "request_id": request.id,
        "url": request.url,
        "host": request.host,
        "path": request.path,
        "status": request.status,
        "timestamp": request.timestamp,
        "source": request.source,
        "platform": request.platform,
        "websocket_message_count": request.websocket_message_count,
        "raw_entry_present": request.raw_entry is not None,
        "raw_message_count": raw_message_count,
        "missing_raw_message_count": max(0, len(messages) - raw_message_count),
        "json_message_count": json_message_count,
        "binary_message_count": sum(1 for message in messages if message.is_binary),
        "body_truncated_message_count": sum(1 for message in messages if message.body_truncated),
        "directions": dict(direction_counts),
        "message_types": dict(message_type_counts),
        "opcodes": dict(opcode_counts),
        "close_codes": dict(close_code_counts),
        "top_level_keys": [
            {"key": key, "count": count}
            for key, count in top_level_keys.most_common(15)
        ],
        "semantic_markers": {
            field: [{"value": value, "count": count} for value, count in counter.most_common(10)]
            for field, counter in semantic_markers.items()
            if counter
        },
        "close_events": close_events,
        "samples": {direction: items for direction, items in samples.items() if items},
    }
    return _json_response(result)


@mcp.tool()
def export_websocket_session_raw(request_id: str, include_normalized: bool = False) -> str:
    """Export the raw uploaded WebSocket session entry and raw frame list."""
    if not request_id.strip():
        return _json_response({"error": "request_id is required"})
    request = storage.get_request_by_id(request_id=request_id, detail_level=DetailLevel.FULL)
    if request is None:
        return _json_response({"error": f"Request {request_id} not found"})
    if not isinstance(request, RequestFull) or not request.is_websocket:
        return _json_response({"error": f"Request {request_id} is not a WebSocket session"})

    raw_messages = [
        {
            "seq": message.seq,
            "direction": message.direction,
            "timestamp": message.timestamp,
            "raw": message.raw,
        }
        for message in request.websocket_messages
    ]
    result = {
        "request_id": request.id,
        "url": request.url,
        "host": request.host,
        "path": request.path,
        "status": request.status,
        "timestamp": request.timestamp,
        "raw_entry": request.raw_entry,
        "raw_messages": raw_messages,
        "raw_message_count": sum(1 for item in raw_messages if item["raw"] is not None),
        "missing_raw_message_count": sum(1 for item in raw_messages if item["raw"] is None),
    }
    if include_normalized:
        result["normalized_session"] = request.model_dump()
    return _json_response(result)


@mcp.tool()
def get_domains() -> str:
    """Get all captured domains with request counts."""
    return _json_response(storage.get_domains(limit=1000))


@mcp.tool()
def analyze_api(domain: str) -> str:
    """Analyze API structure for a specific domain."""
    normalized_domain = domain.strip()
    if not normalized_domain:
        return _json_response({"error": "domain is required"})

    requests = storage.get_requests(
        limit=300,
        detail_level=DetailLevel.KEY,
        domain=normalized_domain,
    )

    endpoints: dict[str, dict[str, Any]] = {}
    for req in requests:
        if req.is_websocket:
            continue
        path = req.path or "/"
        normalized_path = re.sub(r"/\d+", "/{id}", path)
        normalized_path = re.sub(
            r"/[a-f0-9-]{32,}",
            "/{uuid}",
            normalized_path,
            flags=re.IGNORECASE,
        )
        key = f"{req.method} {normalized_path}"
        endpoint = endpoints.setdefault(
            key,
            {
                "method": req.method,
                "path": normalized_path,
                "count": 0,
                "statuses": set(),
                "has_auth": False,
                "request_structure": None,
                "response_structure": None,
                "sample_url": req.url,
            },
        )
        endpoint["count"] += 1
        if req.status is not None:
            endpoint["statuses"].add(req.status)
        if req.has_auth:
            endpoint["has_auth"] = True
        if req.request_body_structure and not endpoint["request_structure"]:
            endpoint["request_structure"] = req.request_body_structure
        if req.response_body_structure and not endpoint["response_structure"]:
            endpoint["response_structure"] = req.response_body_structure

    result = {
        "domain": normalized_domain,
        "total_requests": len(requests),
        "endpoints_count": len(endpoints),
        "endpoints": [
            {
                "method": endpoint["method"],
                "path": endpoint["path"],
                "count": endpoint["count"],
                "statuses": sorted(endpoint["statuses"]),
                "has_auth": endpoint["has_auth"],
                "request_structure": endpoint["request_structure"],
                "response_structure": endpoint["response_structure"],
                "sample_url": endpoint["sample_url"],
            }
            for _, endpoint in sorted(endpoints.items(), key=lambda item: -item[1]["count"])
        ],
    }
    return _json_response(result)


def _safe_headers(req: RequestFull) -> dict[str, str]:
    return {
        key: values[0]
        for key, values in req.request_headers.items()
        if values and key.lower() not in ("host", "content-length", "connection")
    }


def _gen_python(req: RequestFull, framework: str) -> str:
    lines = []
    headers = _safe_headers(req)
    method = req.method.upper()

    if framework == "httpx":
        lines.extend(["import asyncio", "import httpx", "", "async def main():"])
        indent = "    "
    else:
        lines.extend(["import requests", ""])
        indent = ""

    lines.append(f"{indent}method = {json.dumps(method)}")
    lines.append(f"{indent}url = {json.dumps(req.url, ensure_ascii=False)}")
    if headers:
        lines.append(f"{indent}headers = {json.dumps(headers, ensure_ascii=False)}")

    payload_param = ""
    if req.request_body_json is not None:
        lines.append(f"{indent}payload = {json.dumps(req.request_body_json, ensure_ascii=False)}")
        payload_param = ", json=payload"
    elif req.request_body:
        lines.append(f"{indent}payload = {json.dumps(req.request_body, ensure_ascii=False)}")
        payload_param = ", data=payload"

    headers_param = ", headers=headers" if headers else ""
    lines.append("")

    if framework == "httpx":
        lines.append(f"{indent}async with httpx.AsyncClient(timeout=30) as client:")
        lines.append(
            f"{indent}    response = await client.request("
            f"method, url{headers_param}{payload_param})"
        )
        lines.append(f"{indent}    print(response.status_code)")
        lines.append(f"{indent}    print(response.text)")
        lines.append("")
        lines.append("asyncio.run(main())")
    else:
        lines.append(
            f"response = requests.request(method, url{headers_param}{payload_param}, timeout=30)"
        )
        lines.append("print(response.status_code)")
        lines.append("print(response.text)")
    return "\n".join(lines)


def _gen_js(req: RequestFull, framework: str) -> str:
    lines = []
    headers = _safe_headers(req)
    method = req.method.upper()

    if framework == "axios":
        lines.append("import axios from 'axios';")
        lines.append("")
        config_data: dict[str, Any] = {"method": method, "url": req.url}
        if headers:
            config_data["headers"] = headers
        if req.request_body_json is not None:
            config_data["data"] = req.request_body_json
        elif req.request_body:
            config_data["data"] = req.request_body
        lines.append(f"const config = {json.dumps(config_data, indent=2, ensure_ascii=False)};")
        lines.append("")
        lines.append("axios(config)")
        lines.append("  .then((response) => console.log(response.data))")
        lines.append("  .catch((error) => console.error(error));")
        return "\n".join(lines)

    lines.append(f"const url = {json.dumps(req.url, ensure_ascii=False)};")
    options: dict[str, Any] = {"method": method}
    if headers:
        options["headers"] = headers
    lines.append(f"const options = {json.dumps(options, indent=2, ensure_ascii=False)};")

    if req.request_body_json is not None:
        lines.append(
            "options.body = JSON.stringify("
            f"{json.dumps(req.request_body_json, ensure_ascii=False)});"
        )
    elif req.request_body:
        lines.append(f"options.body = {json.dumps(req.request_body, ensure_ascii=False)};")

    lines.append("")
    lines.append("fetch(url, options)")
    lines.append("  .then((response) => response.text())")
    lines.append("  .then((text) => console.log(text))")
    lines.append("  .catch((error) => console.error('Error:', error));")
    return "\n".join(lines)


@mcp.tool()
def generate_code(request_id: str, language: str = "python", framework: str = "requests") -> str:
    """Generate API call code from a captured request."""
    normalized_language = language.lower().strip()
    if normalized_language not in VALID_LANGUAGES:
        return f"# Error: unsupported language '{language}'. Use one of {sorted(VALID_LANGUAGES)}"

    req_obj = storage.get_request_by_id(request_id=request_id, detail_level=DetailLevel.FULL)
    if req_obj is None:
        return f"# Error: Request {request_id} not found"
    if not isinstance(req_obj, RequestFull):
        return f"# Error: Request {request_id} unavailable as full detail"
    if req_obj.is_websocket:
        return "# Error: WebSocket sessions are not supported by generate_code yet. Use get_websocket_session/analyze_websocket_session/export_websocket_session_raw instead."

    if normalized_language == "curl":
        return req_obj.to_curl()
    if normalized_language == "python":
        normalized_framework = framework.lower().strip()
        if normalized_framework not in VALID_PYTHON_FRAMEWORKS:
            normalized_framework = "requests"
        return _gen_python(req_obj, normalized_framework)

    normalized_framework = framework.lower().strip()
    if normalized_framework not in VALID_JS_FRAMEWORKS:
        normalized_framework = "fetch"
    return _gen_js(req_obj, normalized_framework)


@mcp.resource("reqable://requests/recent")
def recent_requests_resource() -> str:
    requests = storage.get_requests(limit=30, detail_level=DetailLevel.SUMMARY)
    return _json_response([item.model_dump() for item in requests])


@mcp.resource("reqable://websocket/sessions")
def websocket_sessions_resource() -> str:
    requests = storage.get_requests(limit=30, detail_level=DetailLevel.SUMMARY, websocket_only=True)
    return _json_response([item.model_dump() for item in requests])


@mcp.resource("reqable://domains")
def domains_resource() -> str:
    return get_domains()


@mcp.resource("reqable://health")
def health_resource() -> str:
    return health_report(detail=False, sample_limit=5)


@mcp.prompt()
def startup_check_prompt() -> str:
    return """Before traffic analysis:
1) Call ingest_status and confirm listening=true
2) Start capture in Reqable
3) Use list_requests/search_requests/get_request for HTTP analysis
4) Use list_websocket_sessions/get_websocket_session/search_websocket_messages/analyze_websocket_session/export_websocket_session_raw for WebSocket data
5) Use health_report/repair_websocket_messages if data quality looks off
6) Reqable Report Server official docs currently describe completed HTTP session upload; if live WebSocket frames are missing, export/import HAR as fallback
"""


def main() -> None:
    _ensure_ingest_started()
    mcp.run()


if __name__ == "__main__":
    main()
