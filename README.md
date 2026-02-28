# Reqable MCP Server

![NPM Version](https://img.shields.io/npm/v/reqable-mcp.svg) ![PyPI Version](https://img.shields.io/pypi/v/reqable-mcp.svg) ![GitHub License](https://img.shields.io/github/license/ElonJask/reqable-mcp.svg)

Reqable MCP Server exposes local Reqable capture traffic to MCP clients (Windsurf/Cursor/Claude/Codex).

Default architecture is local-only:

1. Reqable posts HAR(JSON) to `http://127.0.0.1:18765/report`.
2. `reqable-mcp` normalizes and stores requests in local SQLite.
3. MCP tools query local data only (no cloud relay by default).

Docs: [English](README.md) | [中文](README_CN.md)

## Features

- Local-first, privacy-first ingest path.
- Real-time ingest via Reqable Report Server.
- HAR file import fallback for missed sessions.
- Fast request query/search/domain stats/API analysis.
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

## Available Tools

- `ingest_status`: ingest server state and counters
- `import_har`: import HAR from file path
- `list_requests`: list recent requests with filters
- `get_request`: fetch request details by ID
- `search_requests`: keyword search in URL/body
- `get_domains`: domain-level request statistics
- `analyze_api`: infer API shapes for a domain
- `generate_code`: generate sample client code from captured request

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `REQABLE_INGEST_HOST` | Report receiver host | `127.0.0.1` |
| `REQABLE_INGEST_PORT` | Report receiver port | `18765` |
| `REQABLE_INGEST_PATH` | Report receiver path | `/report` |
| `REQABLE_DATA_DIR` | Local data directory | platform app data dir |
| `REQABLE_DB_PATH` | SQLite file path | `${REQABLE_DATA_DIR}/requests.db` |
| `REQABLE_MAX_BODY_SIZE` | Max persisted body bytes per request | `102400` |
| `REQABLE_MAX_REPORT_SIZE` | Max accepted report payload bytes | `10485760` |
| `REQABLE_MAX_IMPORT_FILE_SIZE` | Max HAR import file bytes | `104857600` |
| `REQABLE_RETENTION_DAYS` | Local retention window | `7` |
| `REQABLE_INGEST_TOKEN` | Optional local auth token | unset |

## Privacy and Data Retention

- Data stays on local machine in default mode.
- Retention cleanup is applied to local DB records.
- If ingest server is offline, Reqable failed report push is not retried.

## License

MIT
