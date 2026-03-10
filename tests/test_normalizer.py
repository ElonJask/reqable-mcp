from reqable_mcp.normalizer import extract_entries, normalize_entry


def test_extract_entries_from_har_log() -> None:
    payload = {
        "log": {
            "entries": [
                {"request": {"method": "GET", "url": "https://a.com"}, "response": {"status": 200}}
            ]
        }
    }
    entries = extract_entries(payload)
    assert len(entries) == 1


def test_extract_entries_from_single_entry() -> None:
    payload = {
        "request": {"method": "POST", "url": "https://a.com/login"},
        "response": {"status": 401},
    }
    entries = extract_entries(payload)
    assert len(entries) == 1


def test_normalize_websocket_entry_with_messages() -> None:
    entry = {
        "_resourceType": "websocket",
        "startedDateTime": "2026-02-28T09:00:00.000Z",
        "time": 321,
        "request": {
            "method": "GET",
            "url": "wss://ws.example.com/chat?room=dev",
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
                "time": "2026-02-28T09:00:00.100Z",
                "opcode": 1,
                "data": '{"action":"ping"}',
            },
            {
                "type": "receive",
                "time": "2026-02-28T09:00:00.200Z",
                "opcode": 1,
                "data": '{"action":"pong"}',
            },
        ],
    }

    normalized = normalize_entry(entry=entry, max_body_size=1024, source="har_import")

    assert normalized["is_websocket"] is True
    assert normalized["is_https"] is True
    assert normalized["host"] == "ws.example.com"
    assert normalized["query_params"] == {"room": "dev"}
    assert len(normalized["websocket_messages"]) == 2
    assert normalized["websocket_messages"][0]["direction"] == "outbound"
    assert normalized["websocket_messages"][1]["direction"] == "inbound"
    assert normalized["websocket_messages"][0]["data_json"] == {"action": "ping"}


def test_normalize_websocket_entry_compatible_message_fields() -> None:
    entry = {
        "resourceType": "websocket",
        "startedDateTime": "2026-02-28T10:00:00.000Z",
        "request": {
            "method": "GET",
            "url": "wss://ws.example.com/compat",
        },
        "response": {
            "status": 101,
            "headers": [
                {"name": "Upgrade", "value": "websocket"},
            ],
        },
        "websocketFrames": [
            {
                "fromClient": True,
                "dateTime": "2026-02-28T10:00:00.100Z",
                "type": "text",
                "payloadData": '{"kind":"hello"}',
            },
            {
                "fromClient": False,
                "dateTime": "2026-02-28T10:00:00.200Z",
                "type": "binary",
                "content": {"text": "AQID"},
            },
        ],
    }

    normalized = normalize_entry(entry=entry, max_body_size=1024, source="har_import")

    assert normalized["is_websocket"] is True
    assert len(normalized["websocket_messages"]) == 2
    assert normalized["websocket_messages"][0]["direction"] == "outbound"
    assert normalized["websocket_messages"][0]["message_type"] == "text"
    assert normalized["websocket_messages"][0]["data_json"] == {"kind": "hello"}
    assert normalized["websocket_messages"][1]["direction"] == "inbound"
    assert normalized["websocket_messages"][1]["message_type"] == "binary"
    assert normalized["websocket_messages"][1]["data"] == "AQID"


def test_normalize_websocket_entry_reqable_raw_flow_payload_format() -> None:
    entry = {
        "request": {"method": "GET", "url": "wss://ws.example.com/raw"},
        "response": {"status": 101},
        "_webSocketMessages": [
            {"flow": 1, "timestamp": 1001, "payload": {"type": 2, "text": "connected,"}},
            {"flow": 0, "timestamp": 1002, "payload": {"type": 2, "text": "echo,hello"}},
            {"flow": 0, "timestamp": 1003, "payload": {"type": 6, "code": 1001, "reason": ""}},
        ],
    }

    normalized = normalize_entry(entry=entry, max_body_size=1024, source="report_server")
    messages = normalized["websocket_messages"]

    assert messages[0]["direction"] == "inbound"
    assert messages[0]["message_type"] == "text"
    assert messages[0]["opcode"] == 1
    assert messages[0]["data"] == "connected,"
    assert messages[1]["direction"] == "outbound"
    assert messages[1]["message_type"] == "text"
    assert messages[2]["direction"] == "outbound"
    assert messages[2]["message_type"] == "close"
    assert messages[2]["opcode"] == 8
