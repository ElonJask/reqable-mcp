# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog and this project follows Semantic Versioning.

## [Unreleased]

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
