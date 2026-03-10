"""SQLite storage for reqable-mcp."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .models import (
    DetailLevel,
    RequestFull,
    RequestKey,
    RequestSummary,
    WebSocketMessage,
    extract_json_structure,
)
from .normalizer import extract_entries, normalize_entry, normalize_websocket_message

LOGGER = logging.getLogger(__name__)
VALID_SEARCH_AREAS = {"all", "url", "request_body", "response_body", "raw_entry", "raw"}
VALID_WS_DIRECTIONS = {"inbound", "outbound", "unknown"}


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ws_close_details(message: WebSocketMessage) -> tuple[int | None, str | None]:
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


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class RequestStorage:
    def __init__(
        self,
        db_path: Path,
        max_body_size: int,
        summary_body_preview_length: int,
        key_body_preview_length: int,
        retention_days: int,
    ) -> None:
        self.db_path = db_path
        self.max_body_size = max_body_size
        self.summary_body_preview_length = summary_body_preview_length
        self.key_body_preview_length = key_body_preview_length
        self.retention_days = retention_days
        self._ingest_calls = 0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.prune_retention()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row[1]) for row in rows}

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        if column_name in self._table_columns(conn, table_name):
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    method TEXT NOT NULL,
                    url TEXT NOT NULL,
                    host TEXT,
                    path TEXT,
                    query_string TEXT,
                    query_params TEXT NOT NULL DEFAULT '{}',
                    status INTEGER,
                    status_text TEXT,
                    duration_ms INTEGER,
                    timestamp TEXT,
                    request_headers TEXT NOT NULL DEFAULT '{}',
                    response_headers TEXT NOT NULL DEFAULT '{}',
                    request_body TEXT,
                    response_body TEXT,
                    request_body_json TEXT,
                    response_body_json TEXT,
                    content_type TEXT,
                    has_auth INTEGER NOT NULL DEFAULT 0,
                    is_https INTEGER NOT NULL DEFAULT 0,
                    body_truncated INTEGER NOT NULL DEFAULT 0,
                    remote_ip TEXT,
                    source TEXT,
                    platform TEXT,
                    reporter_host TEXT,
                    raw_entry_json TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_requests_created_at
                    ON requests(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_requests_created_at_id
                    ON requests(created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_requests_host
                    ON requests(host);
                CREATE INDEX IF NOT EXISTS idx_requests_method
                    ON requests(method);
                CREATE INDEX IF NOT EXISTS idx_requests_status
                    ON requests(status);

                CREATE TABLE IF NOT EXISTS websocket_messages (
                    request_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    direction TEXT NOT NULL,
                    timestamp TEXT,
                    opcode INTEGER,
                    message_type TEXT,
                    data TEXT,
                    data_json TEXT,
                    is_binary INTEGER NOT NULL DEFAULT 0,
                    encoding TEXT,
                    body_truncated INTEGER NOT NULL DEFAULT 0,
                    raw_message_json TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (request_id, seq)
                );

                CREATE INDEX IF NOT EXISTS idx_websocket_messages_request_id
                    ON websocket_messages(request_id, seq);
                CREATE INDEX IF NOT EXISTS idx_websocket_messages_direction
                    ON websocket_messages(direction);

                CREATE TABLE IF NOT EXISTS ingest_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            self._ensure_column(conn, "requests", "raw_entry_json", "TEXT")
            self._ensure_column(conn, "websocket_messages", "raw_message_json", "TEXT")

    def _with_ws_projection(self, columns: str) -> str:
        return (
            f"{columns}, "
            "EXISTS(SELECT 1 FROM websocket_messages wm WHERE wm.request_id = requests.id) "
            "AS is_websocket, "
            "(SELECT COUNT(*) FROM websocket_messages wm WHERE wm.request_id = requests.id) "
            "AS websocket_message_count"
        )

    def prune_retention(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM requests WHERE datetime(created_at) < datetime('now', ?)",
                (f"-{self.retention_days} days",),
            )
            conn.execute(
                "DELETE FROM websocket_messages WHERE request_id NOT IN (SELECT id FROM requests)"
            )
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            conn.commit()
            return deleted

    def add_event(self, level: str, message: str, details: dict[str, Any] | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ingest_events(level, message, details) VALUES (?, ?, ?)",
                (level, message, _json_dumps(details) if details else None),
            )
            conn.commit()

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(limit, 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT level, message, details, created_at
                FROM ingest_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()
        return [
            {
                "level": row["level"],
                "message": row["message"],
                "details": _json_loads(row["details"], None),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _replace_websocket_messages(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        conn.execute("DELETE FROM websocket_messages WHERE request_id = ?", (request_id,))
        for message in messages:
            conn.execute(
                """
                INSERT INTO websocket_messages(
                    request_id, seq, direction, timestamp, opcode, message_type,
                    data, data_json, is_binary, encoding, body_truncated, raw_message_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    message["seq"],
                    message["direction"],
                    message["timestamp"],
                    message["opcode"],
                    message["message_type"],
                    message["data"],
                    _json_dumps(message["data_json"]),
                    int(message["is_binary"]),
                    message["encoding"],
                    int(message["body_truncated"]),
                    _json_dumps(message.get("raw")),
                ),
            )

    def ingest_payload(
        self,
        payload: Any,
        source: str,
        platform: str | None = None,
        reporter_host: str | None = None,
    ) -> dict[str, int]:
        entries = extract_entries(payload)
        if not entries:
            self.add_event("warning", "No valid HAR entries found in payload")
            return {"received": 0, "inserted": 0, "updated": 0}

        inserted = 0
        updated = 0
        websocket_sessions = 0
        websocket_messages = 0
        with self._connect() as conn:
            for entry in entries:
                record = normalize_entry(
                    entry=entry,
                    max_body_size=self.max_body_size,
                    source=source,
                    platform=platform,
                    reporter_host=reporter_host,
                )

                existing = conn.execute(
                    "SELECT 1 FROM requests WHERE id = ?",
                    (record["id"],),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO requests(
                        id, method, url, host, path, query_string, query_params,
                        status, status_text, duration_ms, timestamp,
                        request_headers, response_headers,
                        request_body, response_body,
                        request_body_json, response_body_json,
                        content_type, has_auth, is_https, body_truncated,
                        remote_ip, source, platform, reporter_host, raw_entry_json
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        method=excluded.method,
                        url=excluded.url,
                        host=excluded.host,
                        path=excluded.path,
                        query_string=excluded.query_string,
                        query_params=excluded.query_params,
                        status=excluded.status,
                        status_text=excluded.status_text,
                        duration_ms=excluded.duration_ms,
                        timestamp=excluded.timestamp,
                        request_headers=excluded.request_headers,
                        response_headers=excluded.response_headers,
                        request_body=excluded.request_body,
                        response_body=excluded.response_body,
                        request_body_json=excluded.request_body_json,
                        response_body_json=excluded.response_body_json,
                        content_type=excluded.content_type,
                        has_auth=excluded.has_auth,
                        is_https=excluded.is_https,
                        body_truncated=excluded.body_truncated,
                        remote_ip=excluded.remote_ip,
                        source=excluded.source,
                        platform=excluded.platform,
                        reporter_host=excluded.reporter_host,
                        raw_entry_json=excluded.raw_entry_json
                    """,
                    (
                        record["id"],
                        record["method"],
                        record["url"],
                        record["host"],
                        record["path"],
                        record["query_string"],
                        _json_dumps(record["query_params"]),
                        record["status"],
                        record["status_text"],
                        record["duration_ms"],
                        record["timestamp"],
                        _json_dumps(record["request_headers"]),
                        _json_dumps(record["response_headers"]),
                        record["request_body"],
                        record["response_body"],
                        _json_dumps(record["request_body_json"]),
                        _json_dumps(record["response_body_json"]),
                        record["content_type"],
                        int(record["has_auth"]),
                        int(record["is_https"]),
                        int(record["body_truncated"]),
                        record["remote_ip"],
                        record["source"],
                        record["platform"],
                        record["reporter_host"],
                        _json_dumps(record.get("raw_entry")),
                    ),
                )
                self._replace_websocket_messages(conn, record["id"], record["websocket_messages"])
                if record["is_websocket"]:
                    websocket_sessions += 1
                    websocket_messages += len(record["websocket_messages"])
                if existing:
                    updated += 1
                else:
                    inserted += 1
            conn.commit()

        self.add_event(
            "info",
            "Payload ingested",
            {
                "received": len(entries),
                "inserted": inserted,
                "updated": updated,
                "websocket_sessions": websocket_sessions,
                "websocket_messages": websocket_messages,
            },
        )
        self._ingest_calls += 1
        if self._ingest_calls % 200 == 0:
            self.prune_retention()
        return {
            "received": len(entries),
            "inserted": inserted,
            "updated": updated,
            "websocket_sessions": websocket_sessions,
            "websocket_messages": websocket_messages,
        }

    def import_har_file(
        self,
        file_path: Path,
        max_file_size_bytes: int | None = None,
    ) -> dict[str, int]:
        if max_file_size_bytes is not None:
            file_size = file_path.stat().st_size
            if file_size > max_file_size_bytes:
                raise ValueError(
                    f"HAR file too large: {file_size} bytes > {max_file_size_bytes} bytes"
                )
        text = file_path.read_text(encoding="utf-8", errors="replace")
        try:
            payload = json.loads(text)
            return self.ingest_payload(payload=payload, source="har_import")
        except json.JSONDecodeError:
            entries: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip().rstrip(",")
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
            return self.ingest_payload(payload=entries, source="har_import")

    def _query_rows(
        self,
        limit: int,
        domain: str | None = None,
        method: str | None = None,
        status_code: int | None = None,
        columns: str = "requests.*",
        websocket_only: bool = False,
    ) -> list[sqlite3.Row]:
        normalized_limit = max(1, min(limit, 500))
        where_parts = []
        params: list[Any] = []
        if domain:
            where_parts.append("(host LIKE ? OR url LIKE ?)")
            params.extend([f"%{domain}%", f"%{domain}%"])
        if method:
            where_parts.append("UPPER(method) = ?")
            params.append(method.upper())
        if status_code is not None:
            where_parts.append("status = ?")
            params.append(status_code)
        if websocket_only:
            where_parts.append(
                "EXISTS(SELECT 1 FROM websocket_messages wm WHERE wm.request_id = requests.id)"
            )

        where_clause = ""
        if where_parts:
            where_clause = "WHERE " + " AND ".join(where_parts)

        sql = f"""
            SELECT {columns}
            FROM requests
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        params.append(normalized_limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return rows

    def _row_bool(self, row: sqlite3.Row, key: str) -> bool:
        return bool(row[key]) if key in row.keys() else False

    def _row_int(self, row: sqlite3.Row, key: str) -> int:
        return int(row[key]) if key in row.keys() and row[key] is not None else 0

    def _row_to_summary(self, row: sqlite3.Row) -> RequestSummary:
        return RequestSummary(
            id=row["id"],
            method=row["method"],
            url=row["url"],
            host=row["host"],
            path=row["path"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            timestamp=row["timestamp"],
            is_websocket=self._row_bool(row, "is_websocket"),
            websocket_message_count=self._row_int(row, "websocket_message_count"),
        )

    def _row_to_key(self, row: sqlite3.Row) -> RequestKey:
        request_body = row["request_body"] or ""
        response_body = row["response_body"] or ""
        request_preview = request_body[: self.key_body_preview_length] if request_body else None
        response_preview = response_body[: self.key_body_preview_length] if response_body else None

        request_body_json = _json_loads(row["request_body_json"], None)
        response_body_json = _json_loads(row["response_body_json"], None)
        return RequestKey(
            id=row["id"],
            method=row["method"],
            url=row["url"],
            host=row["host"],
            path=row["path"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            timestamp=row["timestamp"],
            query_params=_json_loads(row["query_params"], {}),
            content_type=row["content_type"],
            request_body_preview=request_preview,
            request_body_structure=extract_json_structure(request_body_json)
            if request_body_json is not None
            else None,
            response_body_preview=response_preview,
            response_body_structure=extract_json_structure(response_body_json)
            if response_body_json is not None
            else None,
            has_auth=bool(row["has_auth"]),
            is_json=(request_body_json is not None or response_body_json is not None),
            body_truncated=bool(row["body_truncated"]),
            is_websocket=self._row_bool(row, "is_websocket"),
            websocket_message_count=self._row_int(row, "websocket_message_count"),
        )

    def _row_to_websocket_message(self, row: sqlite3.Row) -> WebSocketMessage:
        raw_message = _json_loads(row["raw_message_json"], None)
        derived: dict[str, Any] | None = None
        if isinstance(raw_message, dict):
            derived = normalize_websocket_message(
                raw_message,
                max_body_size=self.max_body_size,
                seq=int(row["seq"]),
            )
        direction = row["direction"]
        if direction == "unknown" and derived is not None:
            direction = str(derived["direction"])
        timestamp = row["timestamp"] or (derived["timestamp"] if derived is not None else None)
        opcode = row["opcode"] if row["opcode"] is not None else (derived["opcode"] if derived is not None else None)
        message_type = row["message_type"] or (derived["message_type"] if derived is not None else None)
        data = row["data"] or (derived["data"] if derived is not None else None)
        data_json = _json_loads(row["data_json"], None)
        if data_json is None and derived is not None:
            data_json = derived["data_json"]
        is_binary = bool(row["is_binary"]) or bool(derived["is_binary"] if derived is not None else False)
        encoding = row["encoding"] or (derived["encoding"] if derived is not None else None)
        body_truncated = bool(row["body_truncated"]) or bool(
            derived["body_truncated"] if derived is not None else False
        )
        close_code, close_reason = _ws_close_details(
            WebSocketMessage(
                seq=int(row["seq"]),
                direction=direction,
                timestamp=timestamp,
                opcode=opcode,
                message_type=message_type,
                data=data,
                data_json=data_json,
                is_binary=is_binary,
                encoding=encoding,
                body_truncated=body_truncated,
                raw=raw_message,
            )
        )
        return WebSocketMessage(
            seq=int(row["seq"]),
            direction=direction,
            timestamp=timestamp,
            opcode=opcode,
            message_type=message_type,
            data=data,
            data_json=data_json,
            is_binary=is_binary,
            encoding=encoding,
            body_truncated=body_truncated,
            close_code=close_code,
            close_reason=close_reason,
            raw=raw_message,
        )

    def get_websocket_messages(
        self,
        request_id: str,
        limit: int = 5000,
    ) -> list[WebSocketMessage]:
        normalized_limit = max(1, min(limit, 10000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT seq, direction, timestamp, opcode, message_type,
                       data, data_json, is_binary, encoding, body_truncated, raw_message_json
                FROM websocket_messages
                WHERE request_id = ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (request_id, normalized_limit),
            ).fetchall()
        return [self._row_to_websocket_message(row) for row in rows]

    def _row_to_full(self, row: sqlite3.Row) -> RequestFull:
        parsed_headers = _json_loads(row["request_headers"], {})
        response_headers = _json_loads(row["response_headers"], {})
        url = row["url"] or ""
        port: int | None = None
        if url.startswith(("https://", "wss://")):
            port = 443
        elif url.startswith(("http://", "ws://")):
            port = 80
        return RequestFull(
            id=row["id"],
            method=row["method"],
            url=url,
            host=row["host"],
            path=row["path"],
            query_string=row["query_string"],
            query_params=_json_loads(row["query_params"], {}),
            status=row["status"],
            status_text=row["status_text"],
            duration_ms=row["duration_ms"],
            timestamp=row["timestamp"],
            request_headers=parsed_headers,
            response_headers=response_headers,
            request_body=row["request_body"],
            response_body=row["response_body"],
            request_body_json=_json_loads(row["request_body_json"], None),
            response_body_json=_json_loads(row["response_body_json"], None),
            remote_ip=row["remote_ip"],
            raw_entry=_json_loads(row["raw_entry_json"], None),
            is_https=bool(row["is_https"]),
            body_truncated=bool(row["body_truncated"]),
            source=row["source"],
            platform=row["platform"],
            port=port,
            is_websocket=self._row_bool(row, "is_websocket"),
            websocket_message_count=self._row_int(row, "websocket_message_count"),
        )

    def get_requests(
        self,
        limit: int,
        detail_level: DetailLevel,
        domain: str | None = None,
        method: str | None = None,
        status_code: int | None = None,
        websocket_only: bool = False,
    ) -> list[RequestSummary | RequestKey | RequestFull]:
        if detail_level == DetailLevel.SUMMARY:
            rows = self._query_rows(
                limit=limit,
                domain=domain,
                method=method,
                status_code=status_code,
                columns=self._with_ws_projection(
                    "id, method, url, host, path, status, duration_ms, timestamp"
                ),
                websocket_only=websocket_only,
            )
            return [self._row_to_summary(row) for row in rows]
        if detail_level == DetailLevel.KEY:
            rows = self._query_rows(
                limit=limit,
                domain=domain,
                method=method,
                status_code=status_code,
                columns=self._with_ws_projection(
                    "id, method, url, host, path, status, duration_ms, timestamp, "
                    "query_params, content_type, request_body, request_body_json, "
                    "response_body, response_body_json, has_auth, body_truncated"
                ),
                websocket_only=websocket_only,
            )
            return [self._row_to_key(row) for row in rows]
        rows = self._query_rows(
            limit=limit,
            domain=domain,
            method=method,
            status_code=status_code,
            columns=self._with_ws_projection("requests.*"),
            websocket_only=websocket_only,
        )
        results = [self._row_to_full(row) for row in rows]
        for result in results:
            if result.is_websocket:
                result.websocket_messages = [
                    item for item in self.get_websocket_messages(result.id, limit=5000)
                ]
        return results

    def get_request_by_id(
        self,
        request_id: str,
        detail_level: DetailLevel,
    ) -> RequestSummary | RequestKey | RequestFull | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {self._with_ws_projection('requests.*')}
                FROM requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        if detail_level == DetailLevel.SUMMARY:
            return self._row_to_summary(row)
        if detail_level == DetailLevel.KEY:
            return self._row_to_key(row)
        result = self._row_to_full(row)
        if result.is_websocket:
            result.websocket_messages = self.get_websocket_messages(request_id=result.id, limit=5000)
        return result

    def search(self, keyword: str, search_in: str = "all", limit: int = 20) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(limit, 500))
        area = search_in if search_in in VALID_SEARCH_AREAS else "all"
        needle = keyword.lower()
        where: str
        params: list[Any]
        if area == "url":
            where = "LOWER(url) LIKE ?"
            params = [f"%{needle}%"]
        elif area == "request_body":
            where = "LOWER(COALESCE(request_body, '')) LIKE ?"
            params = [f"%{needle}%"]
        elif area == "response_body":
            where = "LOWER(COALESCE(response_body, '')) LIKE ?"
            params = [f"%{needle}%"]
        elif area in ("raw_entry", "raw"):
            where = "LOWER(COALESCE(raw_entry_json, '')) LIKE ?"
            params = [f"%{needle}%"]
        else:
            where = (
                "LOWER(url) LIKE ? OR LOWER(COALESCE(request_body, '')) LIKE ? "
                "OR LOWER(COALESCE(response_body, '')) LIKE ? "
                "OR LOWER(COALESCE(raw_entry_json, '')) LIKE ?"
            )
            like = f"%{needle}%"
            params = [like, like, like, like]

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {self._with_ws_projection('id, method, url, host, path, status, timestamp, request_body, response_body, raw_entry_json')}
                FROM requests
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (*params, normalized_limit),
            ).fetchall()

        matches: list[dict[str, Any]] = []
        for row in rows:
            hit_areas: list[str] = []
            url = (row["url"] or "").lower()
            req_body = (row["request_body"] or "").lower()
            resp_body = (row["response_body"] or "").lower()
            raw_entry = (row["raw_entry_json"] or "").lower()
            if area in ("all", "url") and needle in url:
                hit_areas.append("url")
            if area in ("all", "request_body") and needle in req_body:
                hit_areas.append("request_body")
            if area in ("all", "response_body") and needle in resp_body:
                hit_areas.append("response_body")
            if area in ("all", "raw_entry", "raw") and needle in raw_entry:
                hit_areas.append("raw_entry")
            if not hit_areas:
                continue

            request_preview = (
                row["request_body"][: self.summary_body_preview_length]
                if row["request_body"]
                else None
            )
            response_preview = (
                row["response_body"][: self.summary_body_preview_length]
                if row["response_body"]
                else None
            )
            raw_entry_preview = (
                row["raw_entry_json"][: self.summary_body_preview_length]
                if row["raw_entry_json"]
                else None
            )
            matches.append(
                {
                    "id": row["id"],
                    "method": row["method"],
                    "url": row["url"],
                    "host": row["host"],
                    "path": row["path"],
                    "status": row["status"],
                    "timestamp": row["timestamp"],
                    "is_websocket": bool(row["is_websocket"]),
                    "websocket_message_count": int(row["websocket_message_count"] or 0),
                    "_matches": hit_areas,
                    "request_body_preview": request_preview,
                    "response_body_preview": response_preview,
                    "raw_entry_preview": raw_entry_preview,
                }
            )
            if len(matches) >= normalized_limit:
                break
        return matches

    def search_websocket_messages(
        self,
        keyword: str = "",
        direction: str | None = None,
        message_type: str | None = None,
        opcode: int | None = None,
        request_id: str | None = None,
        domain: str | None = None,
        close_code: int | None = None,
        has_json: bool | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(limit, 500))
        normalized_keyword = keyword.lower().strip()
        normalized_direction = direction if direction in VALID_WS_DIRECTIONS else None
        normalized_message_type = (message_type or "").strip().lower() or None
        normalized_opcode = _safe_int(opcode)
        normalized_request_id = (request_id or "").strip() or None
        normalized_domain = (domain or "").strip().lower() or None
        normalized_close_code = _safe_int(close_code)

        where_parts: list[str] = []
        params: list[Any] = []
        if normalized_keyword:
            like = f"%{normalized_keyword}%"
            where_parts.append(
                "("
                "LOWER(COALESCE(wm.data, '')) LIKE ? "
                "OR LOWER(COALESCE(wm.data_json, '')) LIKE ? "
                "OR LOWER(COALESCE(wm.raw_message_json, '')) LIKE ?"
                ")"
            )
            params.extend([like, like, like])
        if normalized_request_id:
            where_parts.append("wm.request_id = ?")
            params.append(normalized_request_id)
        if normalized_domain:
            where_parts.append("LOWER(COALESCE(r.host, '')) = ?")
            params.append(normalized_domain)

        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        needs_post_filter = any(
            [
                normalized_direction,
                normalized_message_type,
                normalized_opcode is not None,
                normalized_close_code is not None,
                has_json is not None,
            ]
        )
        query_limit = normalized_limit
        if not normalized_keyword:
            query_limit = 5000
        elif needs_post_filter:
            query_limit = min(max(normalized_limit * 50, 500), 5000)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT wm.request_id, wm.seq, wm.direction, wm.timestamp, wm.opcode,
                       wm.message_type, wm.data, wm.data_json, wm.is_binary,
                       wm.encoding, wm.body_truncated, wm.raw_message_json,
                       r.method, r.url, r.host, r.path, r.status
                FROM websocket_messages wm
                JOIN requests r ON r.id = wm.request_id
                {where_clause}
                ORDER BY r.created_at DESC, wm.seq DESC
                LIMIT ?
                """,
                (*params, query_limit),
            ).fetchall()

        matches: list[dict[str, Any]] = []
        for row in rows:
            message = self._row_to_websocket_message(row)
            close_code_value = message.close_code
            close_reason = message.close_reason
            if normalized_direction and message.direction != normalized_direction:
                continue
            if normalized_message_type and (message.message_type or "").lower() != normalized_message_type:
                continue
            if normalized_opcode is not None and message.opcode != normalized_opcode:
                continue
            if normalized_close_code is not None and close_code_value != normalized_close_code:
                continue
            if has_json is not None and (message.data_json is not None) != has_json:
                continue

            hit_areas: list[str] = []
            if normalized_keyword:
                data = (message.data or "").lower()
                data_json_text = _json_dumps(message.data_json).lower() if message.data_json is not None else ""
                raw_message_json = (row["raw_message_json"] or "").lower()
                if normalized_keyword in data:
                    hit_areas.append("data")
                if normalized_keyword in data_json_text:
                    hit_areas.append("data_json")
                if normalized_keyword in raw_message_json:
                    hit_areas.append("raw_message")

            matches.append(
                {
                    "request_id": row["request_id"],
                    "seq": message.seq,
                    "direction": message.direction,
                    "timestamp": message.timestamp,
                    "opcode": message.opcode,
                    "message_type": message.message_type,
                    "data": message.data,
                    "data_json": message.data_json,
                    "has_json": message.data_json is not None,
                    "is_binary": message.is_binary,
                    "encoding": message.encoding,
                    "body_truncated": message.body_truncated,
                    "close_code": close_code_value,
                    "close_reason": close_reason,
                    "raw": message.raw,
                    "_matches": hit_areas,
                    "method": row["method"],
                    "url": row["url"],
                    "host": row["host"],
                    "path": row["path"],
                    "status": row["status"],
                }
            )
            if len(matches) >= normalized_limit:
                break
        return matches

    def get_domains(self, limit: int = 500) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(limit, 2000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    host AS domain,
                    COUNT(*) AS count,
                    GROUP_CONCAT(DISTINCT UPPER(method)) AS methods
                FROM requests
                WHERE host IS NOT NULL AND host != ''
                GROUP BY host
                ORDER BY count DESC, domain ASC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            methods_raw = row["methods"] or ""
            methods = sorted([item for item in methods_raw.split(",") if item])
            result.append(
                {
                    "domain": row["domain"],
                    "count": int(row["count"]),
                    "methods": methods,
                }
            )
        return result

    def total_requests(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM requests").fetchone()
        return int(row["cnt"]) if row is not None else 0

    def total_websocket_sessions(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT request_id) AS cnt FROM websocket_messages"
            ).fetchone()
        return int(row["cnt"]) if row is not None else 0

    def total_websocket_messages(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM websocket_messages").fetchone()
        return int(row["cnt"]) if row is not None else 0

    def websocket_health_report(self, sample_limit: int = 20) -> dict[str, Any]:
        normalized_sample_limit = max(1, min(sample_limit, 50))

        with self._connect() as conn:
            def _count(where: str, params: tuple[Any, ...] = ()) -> int:
                row = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM websocket_messages WHERE {where}",
                    params,
                ).fetchone()
                return int(row["cnt"]) if row is not None else 0

            total_requests = self.total_requests()
            total_sessions = self.total_websocket_sessions()
            total_messages = self.total_websocket_messages()
            missing_raw_messages = _count("raw_message_json IS NULL OR raw_message_json = ''")
            direction_unknown = _count("direction = 'unknown'")
            missing_opcode = _count("opcode IS NULL")
            missing_message_type = _count("message_type IS NULL")
            missing_data = _count("data IS NULL OR data = ''")
            missing_data_json = _count("data_json IS NULL OR data_json = ''")

            sessions_missing_raw_entry = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM requests r
                WHERE (r.raw_entry_json IS NULL OR r.raw_entry_json = '')
                  AND EXISTS (
                      SELECT 1 FROM websocket_messages wm WHERE wm.request_id = r.id
                  )
                """
            ).fetchone()
            sessions_missing_raw_entry_count = (
                int(sessions_missing_raw_entry["cnt"]) if sessions_missing_raw_entry is not None else 0
            )

            samples = {
                "messages_missing_raw": [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT request_id, seq, direction, opcode, message_type
                        FROM websocket_messages
                        WHERE raw_message_json IS NULL OR raw_message_json = ''
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (normalized_sample_limit,),
                    ).fetchall()
                ],
                "messages_direction_unknown": [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT request_id, seq, opcode, message_type
                        FROM websocket_messages
                        WHERE direction = 'unknown'
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (normalized_sample_limit,),
                    ).fetchall()
                ],
                "sessions_missing_raw_entry": [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT r.id AS request_id, r.url, r.created_at
                        FROM requests r
                        WHERE (r.raw_entry_json IS NULL OR r.raw_entry_json = '')
                          AND EXISTS (
                              SELECT 1 FROM websocket_messages wm WHERE wm.request_id = r.id
                          )
                        ORDER BY r.created_at DESC
                        LIMIT ?
                        """,
                        (normalized_sample_limit,),
                    ).fetchall()
                ],
            }

        return {
            "total_requests": total_requests,
            "total_websocket_sessions": total_sessions,
            "total_websocket_messages": total_messages,
            "websocket_sessions_missing_raw_entry": sessions_missing_raw_entry_count,
            "websocket_messages_missing_raw": missing_raw_messages,
            "websocket_messages_direction_unknown": direction_unknown,
            "websocket_messages_missing_opcode": missing_opcode,
            "websocket_messages_missing_message_type": missing_message_type,
            "websocket_messages_missing_data": missing_data,
            "websocket_messages_missing_data_json": missing_data_json,
            "samples": samples,
        }

    def repair_websocket_messages(self, max_rows: int = 2000, dry_run: bool = False) -> dict[str, Any]:
        normalized_limit = max(1, min(max_rows, 5000))
        updated_fields: dict[str, int] = {
            "direction": 0,
            "timestamp": 0,
            "opcode": 0,
            "message_type": 0,
            "data": 0,
            "data_json": 0,
            "is_binary": 0,
            "encoding": 0,
            "body_truncated": 0,
        }
        scanned = 0
        repaired = 0

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, seq, direction, timestamp, opcode, message_type,
                       data, data_json, is_binary, encoding, body_truncated, raw_message_json
                FROM websocket_messages
                WHERE raw_message_json IS NOT NULL AND raw_message_json != ''
                  AND (
                      direction = 'unknown'
                      OR opcode IS NULL
                      OR message_type IS NULL
                      OR data IS NULL
                      OR data_json IS NULL
                  )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()

            for row in rows:
                scanned += 1
                raw_message = _json_loads(row["raw_message_json"], None)
                if not isinstance(raw_message, dict):
                    continue
                derived = normalize_websocket_message(
                    raw_message,
                    max_body_size=self.max_body_size,
                    seq=int(row["seq"]),
                )
                updates: dict[str, Any] = {}
                if row["direction"] == "unknown" and derived["direction"] != "unknown":
                    updates["direction"] = derived["direction"]
                    updated_fields["direction"] += 1
                if row["timestamp"] is None and derived["timestamp"] is not None:
                    updates["timestamp"] = derived["timestamp"]
                    updated_fields["timestamp"] += 1
                if row["opcode"] is None and derived["opcode"] is not None:
                    updates["opcode"] = derived["opcode"]
                    updated_fields["opcode"] += 1
                if row["message_type"] is None and derived["message_type"] is not None:
                    updates["message_type"] = derived["message_type"]
                    updated_fields["message_type"] += 1
                if row["data"] is None and derived["data"] is not None:
                    updates["data"] = derived["data"]
                    updated_fields["data"] += 1
                if row["data_json"] is None and derived["data_json"] is not None:
                    updates["data_json"] = _json_dumps(derived["data_json"])
                    updated_fields["data_json"] += 1
                if not row["is_binary"] and derived["is_binary"]:
                    updates["is_binary"] = 1
                    updated_fields["is_binary"] += 1
                if row["encoding"] is None and derived["encoding"] is not None:
                    updates["encoding"] = derived["encoding"]
                    updated_fields["encoding"] += 1
                if not row["body_truncated"] and derived["body_truncated"]:
                    updates["body_truncated"] = 1
                    updated_fields["body_truncated"] += 1

                if not updates:
                    continue
                repaired += 1
                if dry_run:
                    continue
                assignments = ", ".join(f"{key} = ?" for key in updates.keys())
                params = list(updates.values()) + [row["request_id"], row["seq"]]
                conn.execute(
                    f"UPDATE websocket_messages SET {assignments} WHERE request_id = ? AND seq = ?",
                    params,
                )

            if not dry_run:
                conn.commit()

        return {
            "ok": True,
            "dry_run": dry_run,
            "scanned": scanned,
            "repaired": repaired,
            "updated_fields": updated_fields,
        }
