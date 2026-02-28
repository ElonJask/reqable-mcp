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
