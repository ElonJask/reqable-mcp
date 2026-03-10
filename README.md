# Reqable MCP Server

![NPM Version](https://img.shields.io/npm/v/reqable-mcp.svg) ![PyPI Version](https://img.shields.io/pypi/v/reqable-mcp.svg) ![GitHub License](https://img.shields.io/github/license/ElonJask/reqable-mcp.svg)

Reqable MCP Server exposes local Reqable capture traffic to MCP clients (Windsurf/Cursor/Claude/Codex).

Default architecture is local-only:

1. Reqable posts HAR(JSON) to `http://127.0.0.1:18765/report`.
2. Optional incremental WebSocket events can be posted to `http://127.0.0.1:18765/ws/events`.
3. `reqable-mcp` normalizes and stores requests/messages in local SQLite.
4. MCP tools query local data only (no cloud relay by default).

Docs: [English](README.md) | [中文](README_CN.md)

## Features

- Local-first, privacy-first ingest path.
- Real-time ingest via Reqable Report Server.
- HAR file import fallback for missed sessions.
- HTTP request query/search/domain stats/API analysis.
- WebSocket session/message parsing for HAR entries carrying message-frame extensions.
- Cross-platform runtime (macOS / Linux / Windows with Python 3.10+).

## Prerequisites

1. Install and open Reqable.
2. Configure Reqable Report Server to post to `http://127.0.0.1:18765/report`.
3. Ensure Node.js (for `npx`) and `uv` (for `uvx`) are available.

## Installation

### Run via npx (recommended)

```bash
npx -y reqable-mcp@latest
```

### Local development

```bash
uv run reqable-mcp
```

## MCP Client Configuration

```json
{
  "mcpServers": {
    "reqable": {
      "command": "npx",
      "args": ["-y", "reqable-mcp@latest"]
    }
  }
}
```

## Reqable Report Server Setup

Use these values in Reqable "Add Report Server":

1. Name: `reqable-mcp-local`
2. Match rule: `*` (or your target domains)
3. Server URL: `http://127.0.0.1:18765/report`
4. Compression: `None` (or keep consistent with your receiver)

After saving, generate traffic and call `ingest_status` to verify incoming payload count.

Important note: `reqable-mcp` still uses HTTP-only ingest transport (no native `ws://` listener), but now supports two HTTP ingest paths: `/report` for HAR/session payload and `/ws/events` for incremental WebSocket events. WebSocket capture works when Reqable payload includes frame data (for example `_webSocketMessages` or event frame objects). Raw entry JSON and raw message JSON are preserved and exposed by WebSocket tools. HAR export/import remains the fallback when live pushes miss frames.

## Available Tools

- `ingest_status`: ingest server state and counters
- `import_har`: import HAR from file path
- `list_requests`: list recent HTTP/WebSocket handshake requests with filters
- `get_request`: fetch request details by ID (`full` includes `raw_entry`)
- `search_requests`: keyword search in HTTP URL/body/raw uploaded entry (`raw` / `raw_entry`)
- `list_websocket_sessions`: list captured WebSocket sessions
- `list_active_websocket_sessions`: list recently active WebSocket sessions by latest captured frames
- `get_websocket_session`: fetch WebSocket session details and messages by ID (including `raw_entry` and message `raw`)
- `tail_websocket_messages`: incremental fetch by `request_id` + `after_seq` cursor
- `search_websocket_messages`: precise WebSocket message search by keyword, direction, type, opcode, close code, domain, and request ID
- `analyze_websocket_session`: summarize directions, message types, JSON shapes, and close events for a session
- `export_websocket_session_raw`: export the raw uploaded WebSocket entry and raw frame list
- `health_report`: ingest status + WebSocket data quality report
- `repair_websocket_messages`: backfill missing fields from raw frames (supports dry-run)
- `get_domains`: domain-level request statistics
- `analyze_api`: infer API shapes for a domain
- `generate_code`: generate sample client code from captured HTTP request

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `REQABLE_INGEST_HOST` | Report receiver host | `127.0.0.1` |
| `REQABLE_INGEST_PORT` | Report receiver port | `18765` |
| `REQABLE_INGEST_PATH` | Report receiver path | `/report` |
| `REQABLE_WS_EVENTS_PATH` | Incremental WebSocket event receiver path | `/ws/events` |
| `REQABLE_DATA_DIR` | Local data directory | platform app data dir |
| `REQABLE_DB_PATH` | SQLite file path | `${REQABLE_DATA_DIR}/requests.db` |
| `REQABLE_MAX_BODY_SIZE` | Max persisted body bytes per request/message | `102400` |
| `REQABLE_MAX_REPORT_SIZE` | Max accepted report payload bytes | `10485760` |
| `REQABLE_MAX_IMPORT_FILE_SIZE` | Max HAR import file bytes | `104857600` |
| `REQABLE_RETENTION_DAYS` | Local retention window | `7` |
| `REQABLE_INGEST_TOKEN` | Optional local auth token | unset |

## Privacy and Data Retention

- Data stays on local machine in default mode.
- Retention cleanup is applied to local DB records, including WebSocket messages.
- If ingest server is offline, Reqable failed report push is not retried.

## License

MIT
