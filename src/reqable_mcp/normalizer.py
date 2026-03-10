"""Normalize Reqable report payload/HAR payload to uniform request records."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from .models import parse_json_if_possible, truncate_body


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _entry_id(entry: dict[str, Any], request_body: str, status: int | None) -> str:
    raw = _as_text(entry.get("_id")).strip()
    if raw:
        return raw
    req = entry.get("request", {}) or {}
    seed = "|".join(
        [
            _as_text(req.get("method", "GET")),
            _as_text(req.get("url", "")),
            _as_text(entry.get("startedDateTime", "")),
            _as_text(status),
            hashlib.sha1(request_body.encode("utf-8", errors="ignore")).hexdigest()[:12],
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"req-{digest}"


def _build_headers(items: Any) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    if not isinstance(items, list):
        return headers
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name")).strip()
        value = _as_text(item.get("value"))
        if not name:
            continue
        headers.setdefault(name, []).append(value)
    return headers


def _first_header(headers: dict[str, list[str]], name: str) -> str | None:
    for key, values in headers.items():
        if key.lower() == name.lower():
            if values:
                return values[0]
            return None
    return None


def _header_contains(headers: dict[str, list[str]], name: str, expected: str) -> bool:
    value = _first_header(headers, name)
    return value is not None and expected.lower() in value.lower()


def _parse_query_params(parsed_url: Any) -> dict[str, str]:
    if not parsed_url.query:
        return {}
    result: dict[str, str] = {}
    for key, values in parse_qs(parsed_url.query).items():
        result[key] = values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False)
    return result


def _decode_response_text(content: dict[str, Any]) -> str:
    text = content.get("text")
    encoding = _as_text(content.get("encoding")).lower()
    text_value = _as_text(text)
    if encoding != "base64":
        return text_value
    try:
        decoded = base64.b64decode(text_value, validate=False)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return text_value


def _websocket_candidates(entry: dict[str, Any]) -> list[dict[str, Any]]:
    for key in (
        "_webSocketMessages",
        "_websocketMessages",
        "webSocketMessages",
        "websocketMessages",
        "websocket_frames",
        "websocketFrames",
        "messages",
        "frames",
    ):
        value = entry.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _payload_dict(message: dict[str, Any]) -> dict[str, Any] | None:
    payload = message.get("payload")
    return payload if isinstance(payload, dict) else None


def _normalize_ws_direction(message: dict[str, Any]) -> str:
    if "fromClient" in message:
        return "outbound" if bool(message.get("fromClient")) else "inbound"
    if "outgoing" in message:
        return "outbound" if bool(message.get("outgoing")) else "inbound"
    if "flow" in message:
        try:
            flow = int(message.get("flow"))
        except (TypeError, ValueError):
            flow = None
        if flow == 0:
            return "outbound"
        if flow == 1:
            return "inbound"

    for field in ("direction", "side", "sender", "type"):
        raw = _as_text(message.get(field)).strip().lower()
        if raw in {"send", "sent", "out", "outbound", "client", "request", "up"}:
            return "outbound"
        if raw in {"receive", "received", "in", "inbound", "server", "response", "down"}:
            return "inbound"
    return "unknown"


def _normalize_ws_opcode(message: dict[str, Any]) -> int | None:
    raw = message.get("opcode")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass

    payload = _payload_dict(message)
    payload_type = payload.get("type") if payload else None
    if payload_type == 2:
        return 1
    if payload_type == 6:
        return 8
    return None


def _normalize_ws_message_type(message: dict[str, Any], opcode: int | None) -> str | None:
    for field in ("messageType", "frameType", "frame_kind"):
        raw = _as_text(message.get(field)).strip().lower()
        if raw:
            return raw
    fallback = _as_text(message.get("type")).strip().lower()
    if fallback in {"text", "binary", "close", "ping", "pong"}:
        return fallback

    payload = _payload_dict(message)
    payload_type = payload.get("type") if payload else None
    if payload_type == 2:
        return "text"
    if payload_type == 6:
        return "close"

    if opcode == 1:
        return "text"
    if opcode == 2:
        return "binary"
    if opcode == 8:
        return "close"
    if opcode == 9:
        return "ping"
    if opcode == 10:
        return "pong"
    return None


def _extract_ws_close_details(message: dict[str, Any], data_json: Any) -> tuple[int | None, str | None]:
    candidates: list[Any] = []
    payload = _payload_dict(message)
    if isinstance(payload, dict):
        candidates.append(payload)
    candidates.append(data_json)
    candidates.append(message)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        code_raw = candidate.get("code")
        code = None
        if code_raw is not None:
            try:
                code = int(code_raw)
            except (TypeError, ValueError):
                code = None
        reason_raw = candidate.get("reason")
        reason = None
        if reason_raw is not None:
            reason_text = _as_text(reason_raw).strip()
            reason = reason_text or None
        if code is not None or reason is not None:
            return code, reason
    return None, None


def _extract_ws_payload(message: dict[str, Any]) -> str:
    for key in ("data", "text", "payload", "payloadData", "body", "content"):
        if key not in message or message.get(key) is None:
            continue
        value = message.get(key)
        if isinstance(value, dict):
            for nested_key in ("text", "data", "payload", "payloadData", "body", "content"):
                if nested_key in value and value.get(nested_key) is not None:
                    return _as_text(value.get(nested_key))
        return _as_text(value)
    return ""


def normalize_websocket_message(
    message: dict[str, Any],
    max_body_size: int,
    seq: int,
) -> dict[str, Any]:
    payload_raw = _extract_ws_payload(message)
    payload, truncated = truncate_body(payload_raw, max_body_size)
    opcode = _normalize_ws_opcode(message)
    message_type = _normalize_ws_message_type(message, opcode)
    data_json = parse_json_if_possible(payload)
    close_code, close_reason = _extract_ws_close_details(message, data_json)
    is_binary = bool(message.get("binary")) or message_type == "binary" or opcode == 2
    encoding = _as_text(message.get("encoding")).strip() or None
    return {
        "seq": seq,
        "direction": _normalize_ws_direction(message),
        "timestamp": _as_text(
            message.get("time")
            or message.get("timestamp")
            or message.get("createdDateTime")
            or message.get("dateTime")
            or message.get("sentDateTime")
            or message.get("receivedDateTime")
        ).strip()
        or None,
        "opcode": opcode,
        "message_type": message_type,
        "data": payload or None,
        "data_json": data_json,
        "is_binary": is_binary,
        "encoding": encoding,
        "body_truncated": truncated,
        "close_code": close_code,
        "close_reason": close_reason,
        "raw": message,
    }


def _normalize_websocket_messages(
    entry: dict[str, Any],
    max_body_size: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, message in enumerate(_websocket_candidates(entry), start=1):
        normalized.append(normalize_websocket_message(message, max_body_size=max_body_size, seq=index))
    return normalized


def extract_entries(payload: Any) -> list[dict[str, Any]]:
    """Extract HAR entries from report payload."""
    if isinstance(payload, dict):
        if "log" in payload and isinstance(payload["log"], dict):
            entries = payload["log"].get("entries")
            if isinstance(entries, list):
                return [entry for entry in entries if isinstance(entry, dict)]
        if "entries" in payload and isinstance(payload.get("entries"), list):
            return [entry for entry in payload["entries"] if isinstance(entry, dict)]
        if isinstance(payload.get("request"), dict):
            return [payload]
        return []

    if isinstance(payload, list):
        result = []
        for item in payload:
            if isinstance(item, dict) and isinstance(item.get("request"), dict):
                result.append(item)
        return result
    return []


def normalize_entry(
    entry: dict[str, Any],
    max_body_size: int,
    source: str,
    platform: str | None = None,
    reporter_host: str | None = None,
) -> dict[str, Any]:
    req = entry.get("request", {}) or {}
    resp = entry.get("response", {}) or {}

    method = _as_text(req.get("method", "GET")).upper() or "GET"
    url = _as_text(req.get("url", "")).strip()
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    query_string = parsed.query or None
    query_params = _parse_query_params(parsed)
    status: int | None
    try:
        status = int(resp.get("status")) if resp.get("status") is not None else None
    except (TypeError, ValueError):
        status = None

    request_headers = _build_headers(req.get("headers"))
    response_headers = _build_headers(resp.get("headers"))

    post_data = req.get("postData", {}) or {}
    request_body_raw = _as_text(post_data.get("text"))
    response_body_raw = _decode_response_text(resp.get("content", {}) or {})

    request_body, request_truncated = truncate_body(request_body_raw, max_body_size)
    response_body, response_truncated = truncate_body(response_body_raw, max_body_size)
    websocket_messages = _normalize_websocket_messages(entry, max_body_size)
    websocket_truncated = any(item["body_truncated"] for item in websocket_messages)
    body_truncated = request_truncated or response_truncated or websocket_truncated

    content_type = (
        _as_text(post_data.get("mimeType")).strip()
        or _first_header(request_headers, "Content-Type")
        or None
    )
    status_text = _as_text(resp.get("statusText")).strip() or None
    timestamp = _as_text(entry.get("startedDateTime")).strip() or None

    request_body_json = parse_json_if_possible(request_body)
    response_body_json = parse_json_if_possible(response_body)

    duration_ms: int | None
    try:
        duration_ms = int(float(_as_text(entry.get("time"))))
    except (TypeError, ValueError):
        duration_ms = None

    is_websocket = bool(websocket_messages)
    if not is_websocket:
        resource_type = _as_text(entry.get("_resourceType") or entry.get("resourceType")).strip().lower()
        is_websocket = resource_type == "websocket"
    if not is_websocket:
        is_websocket = parsed.scheme.lower() in {"ws", "wss"}
    if not is_websocket:
        is_websocket = (
            status == 101
            and _header_contains(request_headers, "Upgrade", "websocket")
            and _header_contains(response_headers, "Upgrade", "websocket")
        )

    has_auth = _first_header(request_headers, "Authorization") is not None
    is_https = parsed.scheme.lower() in {"https", "wss"}
    record_id = _entry_id(entry, request_body, status)

    return {
        "id": record_id,
        "method": method,
        "url": url,
        "host": host or None,
        "path": path or None,
        "query_string": query_string,
        "query_params": query_params,
        "status": status,
        "status_text": status_text,
        "duration_ms": duration_ms,
        "timestamp": timestamp,
        "request_headers": request_headers,
        "response_headers": response_headers,
        "request_body": request_body or None,
        "response_body": response_body or None,
        "request_body_json": request_body_json,
        "response_body_json": response_body_json,
        "content_type": content_type,
        "has_auth": has_auth,
        "is_https": is_https,
        "body_truncated": body_truncated,
        "remote_ip": _as_text(entry.get("serverIPAddress")).strip() or None,
        "source": source,
        "platform": platform,
        "reporter_host": reporter_host,
        "is_websocket": is_websocket,
        "websocket_messages": websocket_messages,
        "raw_entry": entry,
    }
