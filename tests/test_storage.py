from pathlib import Path

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
