from pathlib import Path

from reqable_mcp import config as config_module


def test_get_db_path_from_env(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "x.db"
    monkeypatch.setenv("REQABLE_DB_PATH", str(db_path))
    assert config_module.get_db_path() == Path(db_path)


def test_get_ingest_port_invalid_fallback(monkeypatch) -> None:
    monkeypatch.setenv("REQABLE_INGEST_PORT", "70000")
    assert config_module.get_ingest_port() == config_module.DEFAULT_INGEST_PORT


def test_get_max_report_size_invalid_fallback(monkeypatch) -> None:
    monkeypatch.setenv("REQABLE_MAX_REPORT_SIZE", "abc")
    assert config_module.get_max_report_size() == config_module.DEFAULT_MAX_REPORT_SIZE


def test_get_max_import_file_size_invalid_fallback(monkeypatch) -> None:
    monkeypatch.setenv("REQABLE_MAX_IMPORT_FILE_SIZE", "0")
    assert (
        config_module.get_max_import_file_size() == config_module.DEFAULT_MAX_IMPORT_FILE_SIZE
    )


def test_load_config_rejects_same_ingest_and_ws_paths(monkeypatch) -> None:
    monkeypatch.setenv("REQABLE_INGEST_PATH", "/same")
    monkeypatch.setenv("REQABLE_WS_EVENTS_PATH", "/same")
    try:
        config_module.load_config()
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "must be different paths" in str(exc)


def test_load_config_rejects_reserved_paths(monkeypatch) -> None:
    monkeypatch.setenv("REQABLE_INGEST_PATH", "/health")
    monkeypatch.setenv("REQABLE_WS_EVENTS_PATH", "/ws/events")
    try:
        config_module.load_config()
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "reserved paths" in str(exc)
