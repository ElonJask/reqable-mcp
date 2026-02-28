#!/usr/bin/env node
"use strict";

const { spawn, spawnSync } = require("node:child_process");

function hasCommand(command) {
  const result = spawnSync(command, ["--version"], { stdio: "ignore" });
  return result.status === 0;
}

function start(command, args) {
  const child = spawn(command, args, { stdio: "inherit", env: process.env });
  child.on("exit", (code) => process.exit(code ?? 1));
  child.on("error", (err) => {
    console.error(`[reqable-mcp] failed to start ${command}: ${err.message}`);
    process.exit(1);
  });
}

if (!hasCommand("uvx")) {
  console.error("[reqable-mcp] `uvx` is required but was not found in PATH.");
  console.error(
    "[reqable-mcp] Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
  );
  process.exit(1);
}

start("uvx", ["--from", "reqable-mcp", "reqable-mcp", ...process.argv.slice(2)]);
