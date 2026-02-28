import json
import socket
from pathlib import Path
from urllib.request import Request, urlopen

from reqable_mcp.config import Config
from reqable_mcp.ingest_server import IngestServerManager
from reqable_mcp.storage import RequestStorage


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ingest_server_roundtrip(tmp_path: Path) -> None:
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        db_path=tmp_path / "requests.db",
        ingest_host="127.0.0.1",
        ingest_port=port,
        ingest_path="/report",
        ingest_token=None,
        max_body_size=102400,
        max_report_size=10 * 1024 * 1024,
        retention_days=7,
    )
    storage = RequestStorage(
        db_path=cfg.db_path,
        max_body_size=cfg.max_body_size,
        summary_body_preview_length=200,
        key_body_preview_length=500,
        retention_days=cfg.retention_days,
    )
    manager = IngestServerManager(config=cfg, storage=storage)
    manager.start()
    try:
        payload = {
            "request": {"method": "GET", "url": "https://example.com/ping"},
            "response": {"status": 200},
        }
        raw = json.dumps(payload).encode("utf-8")
        req = Request(
            url=f"http://127.0.0.1:{port}/report",
            data=raw,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["ok"] is True
        assert storage.total_requests() == 1
    finally:
        manager.stop()
