import importlib
import json
from pathlib import Path


def _load_server_module(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REQABLE_DB_PATH", str(tmp_path / "requests.db"))
    import reqable_mcp.server as server_module

    return importlib.reload(server_module)


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


def test_websocket_tools_and_status(tmp_path: Path, monkeypatch) -> None:
    server_module = _load_server_module(tmp_path, monkeypatch)
    payload = _sample_websocket_payload()

    result = server_module.storage.ingest_payload(payload, source="tool_smoke")
    sessions = json.loads(server_module.list_websocket_sessions(limit=5, detail="summary"))
    request_id = sessions[0]["id"]
    detail = json.loads(server_module.get_websocket_session(request_id=request_id, include_messages=True))
    search = json.loads(
        server_module.search_websocket_messages(
            keyword="ack",
            direction="inbound",
            message_type="text",
            request_id=request_id,
            domain="ws.example.com",
            has_json=True,
            limit=5,
        )
    )
    close_search = json.loads(
        server_module.search_websocket_messages(
            direction="outbound",
            message_type="close",
            opcode=8,
            request_id=request_id,
            close_code=1001,
            limit=5,
        )
    )
    analysis = json.loads(server_module.analyze_websocket_session(request_id=request_id, sample_limit=2))
    exported = json.loads(
        server_module.export_websocket_session_raw(request_id=request_id, include_normalized=True)
    )
    health = json.loads(server_module.health_report(detail=False, sample_limit=3))
    repair = json.loads(server_module.repair_websocket_messages(max_rows=10, dry_run=True))
    status = json.loads(server_module.ingest_status(auto_start=False))

    assert result["websocket_sessions"] == 1
    assert result["websocket_messages"] == 4
    assert len(sessions) == 1
    assert detail["is_websocket"] is True
    assert detail["websocket_message_count"] == 4
    assert detail["raw_entry"]["_resourceType"] == "websocket"
    assert len(detail["websocket_messages"]) == 4
    assert detail["websocket_messages"][0]["raw"]["type"] == "send"
    assert detail["websocket_messages"][-1]["close_code"] == 1001

    assert len(search) == 1
    assert search[0]["request_id"] == request_id
    assert search[0]["direction"] == "inbound"
    assert search[0]["message_type"] == "text"
    assert search[0]["data_json"]["event"] == "ack"
    assert search[0]["raw"] is not None

    assert len(close_search) == 1
    assert close_search[0]["direction"] == "outbound"
    assert close_search[0]["message_type"] == "close"
    assert close_search[0]["opcode"] == 8
    assert close_search[0]["close_code"] == 1001

    assert analysis["websocket_message_count"] == 4
    assert analysis["directions"] == {"outbound": 2, "inbound": 2}
    assert analysis["message_types"]["text"] == 2
    assert analysis["message_types"]["ping"] == 1
    assert analysis["message_types"]["close"] == 1
    assert analysis["close_codes"]["1001"] == 1
    assert analysis["raw_message_count"] == 4
    assert analysis["semantic_markers"]["event"][0]["value"] == "ack"
    assert analysis["samples"]["outbound"][0]["seq"] == 1

    assert exported["raw_entry"]["_resourceType"] == "websocket"
    assert len(exported["raw_messages"]) == 4
    assert exported["raw_message_count"] == 4
    assert exported["normalized_session"]["is_websocket"] is True

    assert health["websocket_health"]["total_websocket_sessions"] == 1
    assert repair["dry_run"] is True
    assert status["ingest_transport"] == "http"
    assert status["supports_raw_websocket_listener"] is False
    assert status["supports_websocket_capture"] is True
    assert status["total_websocket_sessions"] == 1
    assert status["total_websocket_messages"] == 4
