# Release Checklist

## 1. Pre-check

1. Run tests and lint:
   - `uv run --with ruff ruff check src tests`
   - `uv run pytest`
2. Build package:
   - `uv build`
3. Ensure version consistency:
   - `pyproject.toml` version
   - `npm-bridge/package.json` version

## 2. Publish Python package (PyPI)

1. Login or provide token:
   - `uv publish --token <PYPI_TOKEN>`
2. Verify package exists:
   - `https://pypi.org/project/reqable-mcp/`

## 3. Publish npm launcher

1. Login to npm:
   - `npm adduser`
2. Publish npm bridge:
   - `cd npm-bridge`
   - `npm publish --access public`
3. Verify package exists:
   - `https://www.npmjs.com/package/reqable-mcp`

## 4. Post-check

1. Test npx launcher:
   - `npx -y reqable-mcp@latest --help`
2. Test MCP client config with `npx` command.
