from reqable_mcp.normalizer import extract_entries


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
