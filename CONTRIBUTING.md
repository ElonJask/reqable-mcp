# Contributing

## Development Setup

1. Install Python 3.10+.
2. Install `uv`.
3. Install project dependencies:

```bash
uv sync --all-extras --dev
```

## Local Quality Gates

Run checks before opening PRs:

```bash
uv run --with ruff ruff check src tests
uv run pytest
uv build
```

## Pull Request Rules

- Keep PR scope small and focused.
- Add tests for behavior changes.
- Update `CHANGELOG.md` under `Unreleased`.
- Do not commit secrets or real sensitive capture data.

## Commit Convention

Recommended prefixes:

- `feat: ...`
- `fix: ...`
- `docs: ...`
- `chore: ...`
