from pathlib import Path

import pytest

from reqable_mcp.models import DetailLevel
from reqable_mcp.storage import RequestStorage


def _sample_payload() -> dict:
    return {
        "log": {
            "entries": [
                {
                    "comment": "needle-from-raw-entry",
                    "startedDateTime": "2026-02-28T09:00:00.000Z",
                    "time": 120,
                    "request": {
                        "method": "POST",
                        "url": "https://api.example.com/v1/login?from=app",
                        "headers": [
                            {"name": "Content-Type", "value": "application/json"},
                            {"name": "Authorization", "value": "Bearer token"},
                        ],
                        "postData": {"text": '{"username":"demo"}'},
                    },
                    "response": {
                        "status": 200,
                        "statusText": "OK",
                        "content": {"text": '{"ok":true}'},
                    },
                }
            ]
        }
    }


def _sample_websocket_payload() -> dict:
    return {
        "log": {
            "entries": [
                {
                    "_resourceType": "websocket",
                    "startedDateTime": "2026-02-28T09:01:00.000Z",
                    "time": 640,
                    "request": {
                        "method": "GET",
                        "url": "wss://ws.example.com/socket?channel=demo",
                        "headers": [
                            {"name": "Connection", "value": "Upgrade"},
                            {"name": "Upgrade", "value": "websocket"},
                        ],
                    },
                    "response": {
                        "status": 101,
                        "statusText": "Switching Protocols",
                        "headers": [
                            {"name": "Connection", "value": "Upgrade"},
                            {"name": "Upgrade", "value": "websocket"},
                        ],
                    },
                    "_webSocketMessages": [
                        {
                            "type": "send",
                            "time": "2026-02-28T09:01:00.100Z",
                            "opcode": 1,
                            "data": '{"event":"subscribe","channel":"demo"}',
                        },
                        {
                            "type": "receive",
                            "time": "2026-02-28T09:01:00.200Z",
                            "opcode": 1,
                            "data": '{"event":"ack","channel":"demo"}',
                        },
                    ],
                }
            ]
        }
    }


def test_ingest_and_query(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )

    result = storage.ingest_payload(_sample_payload(), source="report_server", platform="macos")
    assert result["received"] == 1
    assert result["inserted"] == 1

    summaries = storage.get_requests(limit=10, detail_level=DetailLevel.SUMMARY)
    assert len(summaries) == 1
    assert summaries[0].host == "api.example.com"

    request_id = summaries[0].id
    full = storage.get_request_by_id(request_id, detail_level=DetailLevel.FULL)
    assert full is not None
    assert full.method == "POST"

    matches = storage.search("demo", search_in="request_body", limit=10)
    assert len(matches) == 1
    assert matches[0]["id"] == request_id

    raw_matches = storage.search("needle-from-raw-entry", search_in="raw", limit=10)
    assert len(raw_matches) == 1
    assert raw_matches[0]["id"] == request_id
    assert "raw_entry" in raw_matches[0]["_matches"]
    assert "needle-from-raw-entry" in (raw_matches[0]["raw_entry_preview"] or "")


def test_ingest_websocket_and_query_messages(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )

    result = storage.ingest_payload(_sample_websocket_payload(), source="har_import")
    assert result["received"] == 1
    assert result["websocket_sessions"] == 1
    assert result["websocket_messages"] == 2

    sessions = storage.get_requests(
        limit=10,
        detail_level=DetailLevel.SUMMARY,
        websocket_only=True,
    )
    assert len(sessions) == 1
    assert sessions[0].is_websocket is True
    assert sessions[0].websocket_message_count == 2

    request_id = sessions[0].id
    full = storage.get_request_by_id(request_id, detail_level=DetailLevel.FULL)
    assert full is not None
    assert full.is_websocket is True
    assert full.websocket_message_count == 2
    assert len(full.websocket_messages) == 2
    assert full.raw_entry is not None
    assert full.raw_entry["_resourceType"] == "websocket"
    assert full.websocket_messages[0].direction == "outbound"
    assert full.websocket_messages[0].raw is not None
    assert full.websocket_messages[0].raw["type"] == "send"
    assert full.websocket_messages[1].data_json == {"event": "ack", "channel": "demo"}
    assert full.websocket_messages[1].close_code is None

    matches = storage.search_websocket_messages("ack", limit=10)
    assert len(matches) == 1
    assert matches[0]["request_id"] == request_id
    assert matches[0]["direction"] == "inbound"
    assert matches[0]["raw"] is not None
    assert matches[0]["raw"]["type"] == "receive"
    assert "data" in matches[0]["_matches"]


def test_search_websocket_messages_matches_raw_fields(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )

    payload = {
        "log": {
            "entries": [
                {
                    "_resourceType": "websocket",
                    "startedDateTime": "2026-02-28T09:01:00.000Z",
                    "request": {
                        "method": "GET",
                        "url": "wss://ws.example.com/socket?channel=demo",
                        "headers": [
                            {"name": "Connection", "value": "Upgrade"},
                            {"name": "Upgrade", "value": "websocket"},
                        ],
                    },
                    "response": {"status": 101, "statusText": "Switching Protocols"},
                    "_webSocketMessages": [
                        {
                            "flow": 0,
                            "timestamp": 1773104896236,
                            "payload": {"type": 6, "code": 1001, "reason": ""},
                        }
                    ],
                }
            ]
        }
    }

    storage.ingest_payload(payload, source="report_server")
    with storage._connect() as conn:
        conn.execute(
            "UPDATE websocket_messages SET direction = 'unknown', opcode = NULL, message_type = NULL WHERE request_id = (SELECT id FROM requests ORDER BY created_at DESC, id DESC LIMIT 1)"
        )
        conn.commit()

    matches = storage.search_websocket_messages("1001", direction="outbound", limit=10)

    assert len(matches) == 1
    assert matches[0]["direction"] == "outbound"
    assert matches[0]["message_type"] == "close"
    assert matches[0]["opcode"] == 8
    assert matches[0]["raw"] is not None
    assert matches[0]["raw"]["payload"]["code"] == 1001
    request_id = matches[0]["request_id"]
    full = storage.get_request_by_id(request_id, detail_level=DetailLevel.FULL)
    assert full is not None
    assert full.websocket_messages[-1].close_code == 1001
    assert "raw_message" in matches[0]["_matches"]


def test_search_websocket_messages_supports_precise_filters(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )

    payload = {
        "log": {
            "entries": [
                {
                    "_resourceType": "websocket",
                    "startedDateTime": "2026-02-28T09:01:00.000Z",
                    "request": {
                        "method": "GET",
                        "url": "wss://ws.example.com/socket?channel=demo",
                        "headers": [
                            {"name": "Connection", "value": "Upgrade"},
                            {"name": "Upgrade", "value": "websocket"},
                        ],
                    },
                    "response": {"status": 101, "statusText": "Switching Protocols"},
                    "_webSocketMessages": [
                        {
                            "type": "send",
                            "time": "2026-02-28T09:01:00.100Z",
                            "opcode": 1,
                            "data": '{"command":"subscribe","channel":"demo"}',
                        },
                        {
                            "type": "receive",
                            "time": "2026-02-28T09:01:00.200Z",
                            "opcode": 1,
                            "data": '{"event":"ack","channel":"demo"}',
                        },
                        {
                            "type": "receive",
                            "time": "2026-02-28T09:01:00.250Z",
                            "opcode": 9,
                            "data": 'ping-marker',
                        },
                        {
                            "flow": 0,
                            "timestamp": 1773104896236,
                            "payload": {"type": 6, "code": 1001, "reason": ""},
                        },
                    ],
                }
            ]
        }
    }

    storage.ingest_payload(payload, source="report_server")
    request_id = storage.get_requests(limit=1, detail_level=DetailLevel.SUMMARY, websocket_only=True)[0].id

    with storage._connect() as conn:
        conn.execute(
            "UPDATE websocket_messages SET direction = 'unknown', opcode = NULL, message_type = NULL, data_json = NULL WHERE request_id = ?",
            (request_id,),
        )
        conn.commit()

    close_matches = storage.search_websocket_messages(
        direction="outbound",
        message_type="close",
        opcode=8,
        request_id=request_id,
        close_code=1001,
        limit=10,
    )
    assert len(close_matches) == 1
    assert close_matches[0]["direction"] == "outbound"
    assert close_matches[0]["message_type"] == "close"
    assert close_matches[0]["opcode"] == 8
    assert close_matches[0]["close_code"] == 1001

    ack_matches = storage.search_websocket_messages(
        keyword="ack",
        direction="inbound",
        message_type="text",
        request_id=request_id,
        domain="ws.example.com",
        has_json=True,
        limit=10,
    )
    assert len(ack_matches) == 1
    assert ack_matches[0]["data_json"] == {"event": "ack", "channel": "demo"}
    assert ack_matches[0]["has_json"] is True

    ping_matches = storage.search_websocket_messages(
        direction="inbound",
        opcode=9,
        request_id=request_id,
        limit=10,
    )
    assert len(ping_matches) == 1
    assert ping_matches[0]["message_type"] == "ping"


def test_search_websocket_messages_filter_only_scans_deep_enough(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )

    entries = [
        {
            "_resourceType": "websocket",
            "startedDateTime": "2026-02-28T09:00:00.000Z",
            "request": {"method": "GET", "url": "wss://ws.example.com/socket-close"},
            "response": {"status": 101, "statusText": "Switching Protocols"},
            "_webSocketMessages": [
                {"flow": 0, "timestamp": 1773104896236, "payload": {"type": 6, "code": 1001, "reason": ""}}
            ],
        }
    ]
    for index in range(1, 41):
        entries.append(
            {
                "_resourceType": "websocket",
                "startedDateTime": f"2026-02-28T09:10:{index:02d}.000Z",
                "request": {"method": "GET", "url": f"wss://ws.example.com/socket-{index}"},
                "response": {"status": 101, "statusText": "Switching Protocols"},
                "_webSocketMessages": [
                    {
                        "type": "receive",
                        "time": f"2026-02-28T09:10:{index:02d}.100Z",
                        "opcode": 1,
                        "data": '{"event":"noise"}',
                    }
                ],
            }
        )

    storage.ingest_payload({"log": {"entries": entries}}, source="report_server")
    matches = storage.search_websocket_messages(
        direction="outbound",
        message_type="close",
        close_code=1001,
        limit=5,
    )

    assert len(matches) == 1
    assert matches[0]["url"] == "wss://ws.example.com/socket-close"
    assert matches[0]["close_code"] == 1001


def test_websocket_health_report_and_repair(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )

    payload = {
        "log": {
            "entries": [
                {
                    "_resourceType": "websocket",
                    "startedDateTime": "2026-02-28T09:01:00.000Z",
                    "request": {
                        "method": "GET",
                        "url": "wss://ws.example.com/socket?channel=demo",
                    },
                    "response": {"status": 101, "statusText": "Switching Protocols"},
                    "_webSocketMessages": [
                        {"type": "send", "opcode": 1, "data": '{"event":"hello"}'},
                        {"flow": 0, "timestamp": 1773104896236, "payload": {"type": 6, "code": 1001}},
                    ],
                }
            ]
        }
    }

    storage.ingest_payload(payload, source="report_server")
    request_id = storage.get_requests(limit=1, detail_level=DetailLevel.SUMMARY, websocket_only=True)[0].id

    with storage._connect() as conn:
        conn.execute("UPDATE requests SET raw_entry_json = NULL WHERE id = ?", (request_id,))
        conn.execute(
            "UPDATE websocket_messages SET raw_message_json = NULL WHERE request_id = ? AND seq = 1",
            (request_id,),
        )
        conn.execute(
            "UPDATE websocket_messages SET direction = 'unknown', opcode = NULL, message_type = NULL, data_json = NULL WHERE request_id = ? AND seq = 2",
            (request_id,),
        )
        conn.commit()

    report = storage.websocket_health_report(sample_limit=5)
    assert report["websocket_sessions_missing_raw_entry"] == 1
    assert report["websocket_messages_missing_raw"] == 1
    assert report["websocket_messages_direction_unknown"] >= 1
    assert report["samples"]["messages_missing_raw"]

    dry_run = storage.repair_websocket_messages(max_rows=50, dry_run=True)
    assert dry_run["dry_run"] is True
    assert dry_run["repaired"] >= 1

    fixed = storage.repair_websocket_messages(max_rows=50, dry_run=False)
    assert fixed["dry_run"] is False
    assert fixed["repaired"] >= 1

    rows = storage.search_websocket_messages(request_id=request_id, limit=10)
    assert rows
    assert rows[0]["direction"] in {"outbound", "inbound"}


def test_get_domains_aggregates_all_rows(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )
    payload = {
        "log": {
            "entries": [
                {
                    "startedDateTime": "2026-02-28T09:00:00.000Z",
                    "request": {"method": "GET", "url": "https://a.example.com/p1"},
                    "response": {"status": 200},
                },
                {
                    "startedDateTime": "2026-02-28T09:00:01.000Z",
                    "request": {"method": "POST", "url": "https://a.example.com/p2"},
                    "response": {"status": 201},
                },
                {
                    "startedDateTime": "2026-02-28T09:00:02.000Z",
                    "request": {"method": "GET", "url": "https://a.example.com/p3"},
                    "response": {"status": 204},
                },
                {
                    "startedDateTime": "2026-02-28T09:00:03.000Z",
                    "request": {"method": "GET", "url": "https://b.example.com/p4"},
                    "response": {"status": 200},
                },
            ]
        }
    }
    storage.ingest_payload(payload, source="report_server")

    top = storage.get_domains(limit=1)
    assert len(top) == 1
    assert top[0]["domain"] == "a.example.com"
    assert top[0]["count"] == 3
    assert top[0]["methods"] == ["GET", "POST"]


def test_import_har_file_rejects_oversized_file(tmp_path: Path) -> None:
    storage = RequestStorage(
        db_path=tmp_path / "requests.db",
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )
    har_path = tmp_path / "big.har"
    har_path.write_text("x" * 200, encoding="utf-8")

    with pytest.raises(ValueError, match="HAR file too large"):
        storage.import_har_file(har_path, max_file_size_bytes=100)


def test_storage_migrates_existing_db_for_raw_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE requests (
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
        CREATE TABLE websocket_messages (
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
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (request_id, seq)
        );
        """
    )
    conn.commit()
    conn.close()

    storage = RequestStorage(
        db_path=db_path,
        max_body_size=102400,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=7,
    )
    storage.ingest_payload(_sample_websocket_payload(), source="har_import")

    conn = sqlite3.connect(db_path)
    request_columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
    ws_columns = {row[1] for row in conn.execute("PRAGMA table_info(websocket_messages)").fetchall()}
    conn.close()

    assert "raw_entry_json" in request_columns
    assert "raw_message_json" in ws_columns
