from pathlib import Path

import pytest

from reqable_mcp.models import DetailLevel
from reqable_mcp.storage import RequestStorage


def _sample_payload() -> dict:
    return {
        "log": {
            "entries": [
                {
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
