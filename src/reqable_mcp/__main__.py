"""CLI entry points for reqable-mcp."""

from __future__ import annotations

import argparse
import signal
import time

from .server import ingest_server, main


def collector_main() -> None:
    ingest_server.ensure_started()

    running = True

    def _handle_stop(*_args) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    while running:
        time.sleep(0.5)
    ingest_server.stop()


def cli() -> None:
    parser = argparse.ArgumentParser(prog="reqable-mcp")
    parser.add_argument(
        "mode",
        nargs="?",
        default="mcp",
        choices=["mcp", "collector"],
        help="run MCP stdio server or ingest collector only",
    )
    args = parser.parse_args()
    if args.mode == "collector":
        collector_main()
        return
    main()


if __name__ == "__main__":
    cli()
