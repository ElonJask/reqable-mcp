# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

## [0.3.1] - 2026-03-10

### Fixed

- WebSocket incremental ingest compatibility:
  - Accepts top-level default session fields (`session_id`, `request`, `response`) with nested `events[]`.
  - Prevents duplicate inserts for retry events without explicit `seq` by content-based deduplication.
- WebSocket tail cursor robustness:
  - `tail_websocket_messages` now returns `scanned_until_seq`.
  - Cursor advances on sparse-filter empty windows to avoid polling stalls.
- Ingest route safety and hardening:
  - Rejects invalid path configurations where `REQABLE_INGEST_PATH` equals `REQABLE_WS_EVENTS_PATH`.
  - Rejects reserved ingest paths (`/health`) for data routes.
  - Uses constant-time token comparison (`hmac.compare_digest`).
- Health endpoint exposure control:
  - `/health` response no longer exposes `db_path` and `last_error`.

### Changed

- Incremental WS ingest performance improved by avoiding redundant per-event session upserts when session metadata is unchanged.
- Added regression tests for all fixes above, including defaults compatibility, dedup retries, tail cursor progression, health redaction, and config path validation.

## [0.3.0] - 2026-03-10

### Added

- Incremental WebSocket ingest path over HTTP:
  - New receiver path `/ws/events` (configurable via `REQABLE_WS_EVENTS_PATH`).
  - New storage ingest pipeline `ingest_websocket_events` for event-style frame uploads.
- WebSocket incremental query tools:
  - `tail_websocket_messages` for cursor-based (`after_seq`) session tailing.
  - `list_active_websocket_sessions` for recent-session activity view.
- New MCP resource: `reqable://websocket/active`.
- Ingest status now exposes incremental capability fields:
  - `supports_incremental_websocket_events`
  - `ws_events_url`
  - `ws_events_path`

### Changed

- Storage model extended from snapshot-only replacement to support incremental append/merge semantics for WebSocket frames:
  - Session-level upsert for live event streams.
  - Message-level append with duplicate/update detection.
- README (EN/CN) updated with `/ws/events`, new tools, and configuration.
- Test coverage expanded for:
  - `/ws/events` ingest roundtrip
  - Event ingest + tail + active-session flows
  - New MCP tool behavior and status fields

## [0.2.0] - 2026-03-10

### Added

- WebSocket governance and diagnostics:
  - `health_report` tool with data quality metrics and samples.
  - `repair_websocket_messages` tool to backfill missing WebSocket fields from raw frames (supports dry-run).
  - `reqable://health` MCP resource.
- WebSocket analysis and export tools:
  - `analyze_websocket_session` session summary (directions, message types, JSON shapes, close events).
  - `export_websocket_session_raw` to export raw entry and raw frame list.
- Close-frame details promoted to first-class fields (`close_code`, `close_reason`) on WebSocket messages.
- Extended WebSocket message search filters (direction, type, opcode, close code, request id, domain, has_json).
- Data quality and repair test coverage for WebSocket health and backfill.

### Changed

- WebSocket normalization/ingest now preserves richer raw fields and close semantics.
- WebSocket message search uses expanded candidate windows to avoid missing rare frames under filter-only queries.
- README (EN/CN) updated with new tools and governance workflow.

## [0.1.1] - 2026-02-28

### Added

- Repository governance files:
  - `CONTRIBUTING.md`
  - `SECURITY.md`
  - `CODE_OF_CONDUCT.md`
  - `CHANGELOG.md`
- Community templates:
  - `.github/ISSUE_TEMPLATE/*`
  - `.github/pull_request_template.md`
- Release workflow: `.github/workflows/publish.yml`.
- Developer baseline files: `.editorconfig`, `.pre-commit-config.yaml`.
- New env var: `REQABLE_MAX_IMPORT_FILE_SIZE` to guard HAR import size.
- Tests for:
  - compressed payload post-decode size limits
  - accurate domain aggregation
  - oversized HAR import rejection

### Changed

- Upgraded README (EN/CN) to production-grade structure:
  - clearer setup flow
  - tool descriptions
  - environment matrix
  - privacy and retention notes
- `get_domains` now uses SQL aggregation for accurate counts and method sets.
- `import_har` now applies max file size guard before loading file content.
- Ingest now validates decoded payload size to prevent compressed-over-limit bypass.
- Ingest startup error logging now uses cooldown to reduce repeated event spam on port conflicts.

### Removed

- Removed GitHub workflows by maintainer preference:
  - `.github/workflows/ci.yml`
  - `.github/workflows/publish.yml`
