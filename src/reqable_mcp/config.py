"""Configuration for reqable-mcp local mode."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_INGEST_HOST = "127.0.0.1"
DEFAULT_INGEST_PORT = 18765
DEFAULT_INGEST_PATH = "/report"
DEFAULT_MAX_BODY_SIZE = 1024 * 100  # 100KB
DEFAULT_MAX_REPORT_SIZE = 10 * 1024 * 1024  # 10MB
DEFAULT_MAX_IMPORT_FILE_SIZE = 100 * 1024 * 1024  # 100MB
DEFAULT_RETENTION_DAYS = 7


def _read_int_env(
    key: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("Ignoring invalid %s=%r, fallback=%d", key, raw, default)
        return default
    if value < minimum or (maximum is not None and value > maximum):
        LOGGER.warning("Ignoring out-of-range %s=%r, fallback=%d", key, raw, default)
        return default
    return value


def _read_path_env(key: str) -> Path | None:
    raw = os.environ.get(key)
    if not raw:
        return None
    return Path(raw).expanduser()


def get_data_dir() -> Path:
    env = _read_path_env("REQABLE_DATA_DIR")
    if env:
        return env

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "reqable-mcp"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / "reqable-mcp"
    return Path.home() / ".local" / "share" / "reqable-mcp"


def get_db_path() -> Path:
    env = _read_path_env("REQABLE_DB_PATH")
    if env:
        return env
    return get_data_dir() / "requests.db"


def get_ingest_host() -> str:
    return os.environ.get("REQABLE_INGEST_HOST", DEFAULT_INGEST_HOST).strip() or DEFAULT_INGEST_HOST


def get_ingest_port() -> int:
    return _read_int_env("REQABLE_INGEST_PORT", DEFAULT_INGEST_PORT, minimum=1, maximum=65535)


def get_ingest_path() -> str:
    value = os.environ.get("REQABLE_INGEST_PATH", DEFAULT_INGEST_PATH).strip()
    if not value:
        return DEFAULT_INGEST_PATH
    if not value.startswith("/"):
        return f"/{value}"
    return value


def get_ingest_token() -> str | None:
    value = os.environ.get("REQABLE_INGEST_TOKEN", "").strip()
    return value or None


def get_max_body_size() -> int:
    return _read_int_env(
        "REQABLE_MAX_BODY_SIZE",
        DEFAULT_MAX_BODY_SIZE,
        minimum=1024,
        maximum=10 * 1024 * 1024,
    )


def get_retention_days() -> int:
    return _read_int_env("REQABLE_RETENTION_DAYS", DEFAULT_RETENTION_DAYS, minimum=1, maximum=3650)


def get_max_report_size() -> int:
    return _read_int_env(
        "REQABLE_MAX_REPORT_SIZE",
        DEFAULT_MAX_REPORT_SIZE,
        minimum=1024,
        maximum=100 * 1024 * 1024,
    )


def get_max_import_file_size() -> int:
    return _read_int_env(
        "REQABLE_MAX_IMPORT_FILE_SIZE",
        DEFAULT_MAX_IMPORT_FILE_SIZE,
        minimum=1024,
        maximum=1024 * 1024 * 1024,
    )


@dataclass
class Config:
    data_dir: Path
    db_path: Path
    ingest_host: str
    ingest_port: int
    ingest_path: str
    ingest_token: str | None
    max_body_size: int
    max_report_size: int
    retention_days: int
    max_import_file_size: int = DEFAULT_MAX_IMPORT_FILE_SIZE
    default_list_limit: int = 20
    key_body_preview_length: int = 500
    summary_body_preview_length: int = 200

    @property
    def ingest_url(self) -> str:
        return f"http://{self.ingest_host}:{self.ingest_port}{self.ingest_path}"


def load_config() -> Config:
    data_dir = get_data_dir()
    db_path = get_db_path()
    return Config(
        data_dir=data_dir,
        db_path=db_path,
        ingest_host=get_ingest_host(),
        ingest_port=get_ingest_port(),
        ingest_path=get_ingest_path(),
        ingest_token=get_ingest_token(),
        max_body_size=get_max_body_size(),
        max_report_size=get_max_report_size(),
        max_import_file_size=get_max_import_file_size(),
        retention_days=get_retention_days(),
    )


config = load_config()
