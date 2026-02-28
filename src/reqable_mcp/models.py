"""Data models and utility functions."""

from __future__ import annotations

import json
import shlex
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DetailLevel(str, Enum):
    SUMMARY = "summary"
    KEY = "key"
    FULL = "full"


class RequestSummary(BaseModel):
    id: str
    method: str
    url: str
    host: str | None = None
    path: str | None = None
    status: int | None = None
    duration_ms: int | None = None
    timestamp: str | None = None


class RequestKey(BaseModel):
    id: str
    method: str
    url: str
    host: str | None = None
    path: str | None = None
    status: int | None = None
    duration_ms: int | None = None
    timestamp: str | None = None
    query_params: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None
    request_body_preview: str | None = None
    request_body_structure: Any | None = None
    response_body_preview: str | None = None
    response_body_structure: Any | None = None
    has_auth: bool = False
    is_json: bool = False
    body_truncated: bool = False


class RequestFull(BaseModel):
    id: str
    method: str
    url: str
    protocol: str = "HTTP/1.1"
    host: str | None = None
    port: int | None = None
    path: str | None = None
    query_string: str | None = None
    query_params: dict[str, str] = Field(default_factory=dict)
    status: int | None = None
    status_text: str | None = None
    duration_ms: int | None = None
    timestamp: str | None = None
    request_headers: dict[str, list[str]] = Field(default_factory=dict)
    response_headers: dict[str, list[str]] = Field(default_factory=dict)
    request_body: str | None = None
    request_body_json: Any | None = None
    response_body: str | None = None
    response_body_json: Any | None = None
    remote_ip: str | None = None
    is_https: bool = False
    body_truncated: bool = False
    source: str | None = None
    platform: str | None = None

    def to_curl(self) -> str:
        parts = [f"curl -X {shlex.quote(self.method.upper())}"]
        parts.append(shlex.quote(self.url))
        for name, values in self.request_headers.items():
            if name.lower() in ("host", "content-length"):
                continue
            for value in values:
                parts.append(f"-H {shlex.quote(f'{name}: {value}')}")
        if self.request_body:
            parts.append(f"--data-raw {shlex.quote(self.request_body)}")
        return " \\\n  ".join(parts)


def extract_json_structure(data: Any, max_depth: int = 3, current_depth: int = 0) -> Any:
    """Extract structure only and drop concrete values."""
    if current_depth >= max_depth:
        return "..."
    if data is None:
        return "null"
    if isinstance(data, bool):
        return "bool"
    if isinstance(data, int):
        return "int"
    if isinstance(data, float):
        return "float"
    if isinstance(data, str):
        return "string"
    if isinstance(data, list):
        if not data:
            return []
        return [
            extract_json_structure(
                data[0],
                max_depth=max_depth,
                current_depth=current_depth + 1,
            )
        ]
    if isinstance(data, dict):
        return {
            key: extract_json_structure(value, max_depth=max_depth, current_depth=current_depth + 1)
            for key, value in list(data.items())[:10]
        }
    return str(type(data).__name__)


def parse_json_if_possible(text: str | None) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def truncate_body(body: str, max_length: int) -> tuple[str, bool]:
    if len(body) <= max_length:
        return body, False
    return body[:max_length] + "...[truncated]", True
