"""SQLite storage for reqable-mcp."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .models import DetailLevel, RequestFull, RequestKey, RequestSummary, extract_json_structure
from .normalizer import extract_entries, normalize_entry

LOGGER = logging.getLogger(__name__)
VALID_SEARCH_AREAS = {"all", "url", "request_body", "response_body"}


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

                CREATE TABLE IF NOT EXISTS ingest_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )

    def prune_retention(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM requests WHERE datetime(created_at) < datetime('now', ?)",
                (f"-{self.retention_days} days",),
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
                        remote_ip, source, platform, reporter_host
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                        reporter_host=excluded.reporter_host
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
                    ),
                )
                if existing:
                    updated += 1
                else:
                    inserted += 1
            conn.commit()

        self.add_event(
            "info",
            "Payload ingested",
            {"received": len(entries), "inserted": inserted, "updated": updated},
        )
        self._ingest_calls += 1
        if self._ingest_calls % 200 == 0:
            self.prune_retention()
        return {"received": len(entries), "inserted": inserted, "updated": updated}

    def import_har_file(self, file_path: Path) -> dict[str, int]:
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
        columns: str = "*",
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
        )

    def _row_to_full(self, row: sqlite3.Row) -> RequestFull:
        parsed_headers = _json_loads(row["request_headers"], {})
        response_headers = _json_loads(row["response_headers"], {})
        url = row["url"] or ""
        port: int | None = None
        if url.startswith("https://"):
            port = 443
        elif url.startswith("http://"):
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
            is_https=bool(row["is_https"]),
            body_truncated=bool(row["body_truncated"]),
            source=row["source"],
            platform=row["platform"],
            port=port,
        )

    def get_requests(
        self,
        limit: int,
        detail_level: DetailLevel,
        domain: str | None = None,
        method: str | None = None,
        status_code: int | None = None,
    ) -> list[RequestSummary | RequestKey | RequestFull]:
        if detail_level == DetailLevel.SUMMARY:
            rows = self._query_rows(
                limit=limit,
                domain=domain,
                method=method,
                status_code=status_code,
                columns="id, method, url, host, path, status, duration_ms, timestamp",
            )
            return [self._row_to_summary(row) for row in rows]
        if detail_level == DetailLevel.KEY:
            rows = self._query_rows(
                limit=limit,
                domain=domain,
                method=method,
                status_code=status_code,
                columns=(
                    "id, method, url, host, path, status, duration_ms, timestamp, "
                    "query_params, content_type, request_body, request_body_json, "
                    "response_body, response_body_json, has_auth, body_truncated"
                ),
            )
            return [self._row_to_key(row) for row in rows]
        rows = self._query_rows(
            limit=limit,
            domain=domain,
            method=method,
            status_code=status_code,
        )
        return [self._row_to_full(row) for row in rows]

    def get_request_by_id(
        self,
        request_id: str,
        detail_level: DetailLevel,
    ) -> RequestSummary | RequestKey | RequestFull | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        if detail_level == DetailLevel.SUMMARY:
            return self._row_to_summary(row)
        if detail_level == DetailLevel.KEY:
            return self._row_to_key(row)
        return self._row_to_full(row)

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
            where = "LOWER(request_body) LIKE ?"
            params = [f"%{needle}%"]
        elif area == "response_body":
            where = "LOWER(response_body) LIKE ?"
            params = [f"%{needle}%"]
        else:
            where = "LOWER(url) LIKE ? OR LOWER(request_body) LIKE ? OR LOWER(response_body) LIKE ?"
            like = f"%{needle}%"
            params = [like, like, like]

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, method, url, host, path, status, timestamp, request_body, response_body
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
            if area in ("all", "url") and needle in url:
                hit_areas.append("url")
            if area in ("all", "request_body") and needle in req_body:
                hit_areas.append("request_body")
            if area in ("all", "response_body") and needle in resp_body:
                hit_areas.append("response_body")
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
            matches.append(
                {
                    "id": row["id"],
                    "method": row["method"],
                    "url": row["url"],
                    "host": row["host"],
                    "path": row["path"],
                    "status": row["status"],
                    "timestamp": row["timestamp"],
                    "_matches": hit_areas,
                    "request_body_preview": request_preview,
                    "response_body_preview": response_preview,
                }
            )
            if len(matches) >= normalized_limit:
                break
        return matches

    def get_domains(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query_rows(limit=limit)
        domains: dict[str, dict[str, Any]] = {}
        for row in rows:
            host = row["host"]
            if not host:
                continue
            entry = domains.setdefault(host, {"count": 0, "methods": set()})
            entry["count"] += 1
            entry["methods"].add(row["method"])
        result = [
            {"domain": domain, "count": info["count"], "methods": sorted(info["methods"])}
            for domain, info in sorted(domains.items(), key=lambda item: -item[1]["count"])
        ]
        return result

    def total_requests(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM requests").fetchone()
        return int(row["cnt"]) if row is not None else 0
