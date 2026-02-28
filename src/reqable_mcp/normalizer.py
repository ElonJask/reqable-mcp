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
    body_truncated = request_truncated or response_truncated

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

    has_auth = _first_header(request_headers, "Authorization") is not None
    is_https = parsed.scheme.lower() == "https"
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
    }
