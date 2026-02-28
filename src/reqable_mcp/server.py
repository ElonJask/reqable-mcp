"""Reqable MCP server (local mode first)."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import config
from .ingest_server import IngestServerManager
from .models import DetailLevel, RequestFull
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
VALID_SEARCH_AREAS = {"all", "url", "request_body", "response_body"}
VALID_LANGUAGES = {"python", "javascript", "typescript", "curl"}
VALID_PYTHON_FRAMEWORKS = {"requests", "httpx"}
VALID_JS_FRAMEWORKS = {"fetch", "axios"}
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


@mcp.tool()
def ingest_status(auto_start: bool = True) -> str:
    """Check local ingest server status."""
    if auto_start:
        _ensure_ingest_started()
    return _json_response(ingest_server.status(include_events=True))


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
    """List recent HTTP requests captured from Reqable reports."""
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
def get_request(request_id: str, include_body: bool = True) -> str:
    """Get detailed information for a single request."""
    if not request_id.strip():
        return _json_response({"error": "request_id is required"})
    detail_level = DetailLevel.FULL if include_body else DetailLevel.KEY
    request = storage.get_request_by_id(request_id=request_id, detail_level=detail_level)
    if request is None:
        return _json_response({"error": f"Request {request_id} not found"})
    result = request.model_dump()
    if isinstance(request, RequestFull):
        result["curl_command"] = request.to_curl()
    return _json_response(result)


@mcp.tool()
def search_requests(keyword: str, search_in: str = "all", limit: int = 20) -> str:
    """Search requests by keyword."""
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


@mcp.resource("reqable://domains")
def domains_resource() -> str:
    return get_domains()


@mcp.prompt()
def startup_check_prompt() -> str:
    return """Before traffic analysis:
1) Call ingest_status and confirm listening=true
2) Start capture in Reqable
3) Use list_requests/search_requests/get_request for analysis
"""


def main() -> None:
    _ensure_ingest_started()
    mcp.run()


if __name__ == "__main__":
    main()
