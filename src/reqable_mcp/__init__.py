"""Reqable MCP server package."""

from __future__ import annotations

from typing import Any

__version__ = "0.3.1"
__all__ = ["mcp", "main"]


def main() -> None:
    from .server import main as server_main

    server_main()


def __getattr__(name: str) -> Any:
    if name == "mcp":
        from .server import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
