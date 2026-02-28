# Reqable MCP (npx launcher)

A lightweight npx launcher for `reqable-mcp` (Python MCP server).

## Core Features

- Start the real server via `uvx --from reqable-mcp reqable-mcp`
- Provide `npx` style configuration for MCP clients

## Run

```bash
npx -y reqable-mcp@latest
```

## MCP config (npx)

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

## Notes

- `uv` must be installed (provides `uvx`)
- `reqable-mcp` must exist on PyPI

## License

MIT
