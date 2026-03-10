# Reqable MCP Server

![NPM Version](https://img.shields.io/npm/v/reqable-mcp.svg) ![PyPI Version](https://img.shields.io/pypi/v/reqable-mcp.svg) ![GitHub License](https://img.shields.io/github/license/ElonJask/reqable-mcp.svg)

`reqable-mcp` 用于把本地 Reqable 抓包数据暴露给 MCP 客户端（Windsurf/Cursor/Claude/Codex）。

默认采用本地优先架构：

1. Reqable 把 HAR(JSON) 上报到 `http://127.0.0.1:18765/report`。
2. 可选地把增量 WebSocket 事件上报到 `http://127.0.0.1:18765/ws/events`。
3. `reqable-mcp` 解析后写入本地 SQLite。
4. MCP 工具只读取本地数据，不走默认云端中转。

文档： [English](README.md) | [中文](README_CN.md)

## 核心特性

- 本地优先、隐私优先。
- 基于 Report Server 的近实时上报。
- 支持 HAR 文件补录兜底。
- HTTP 请求列表、检索、域名统计、API 结构分析。
- 支持解析带 WebSocket 帧扩展字段的 HAR 会话与消息。
- 跨平台可用（macOS / Linux / Windows，Python 3.10+）。

## 前置条件

1. 已安装并打开 Reqable。
2. 在 Reqable 中配置 Report Server 上报到 `http://127.0.0.1:18765/report`。
3. 本机具备 Node.js（给 `npx`）和 `uv`（给 `uvx`）。

## 安装与启动

### npx 启动（推荐）

```bash
npx -y reqable-mcp@latest
```

### 本地开发运行

```bash
uv run reqable-mcp
```

## MCP 配置示例

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

## Reqable Report Server 配置

在 Reqable 的“添加报告服务器”中建议填写：

1. 名称：`reqable-mcp-local`
2. 匹配规则：`*`（或你自己的目标域名规则）
3. 服务器 URL：`http://127.0.0.1:18765/report`
4. 压缩：`无`（或与接收端保持一致）

保存后产生一点请求流量，再调用 `ingest_status` 验证计数是否增长。

重要说明：`reqable-mcp` 仍然是 HTTP 接收模式（不提供原生 `ws://` 监听端点），但新增了两条 HTTP 接收路径：`/report`（HAR/会话快照）和 `/ws/events`（增量 WebSocket 事件）。只要上报 payload 里包含帧数据（例如 `_webSocketMessages` 或事件 `frame`），就会保留并透传原始 entry JSON 与原始消息 JSON；如果实时上报缺帧，建议从 Reqable 导出 HAR 后用 `import_har` 补录。

## 可用工具

- `ingest_status`：查看接收端状态和计数
- `import_har`：从 HAR 文件导入
- `list_requests`：按条件列出 HTTP / WebSocket 握手请求
- `get_request`：按 ID 查询请求明细（完整模式会返回 `raw_entry`）
- `search_requests`：按关键字检索 HTTP URL / Body / 原始上报条目（`raw` / `raw_entry`）
- `list_websocket_sessions`：列出 WebSocket 会话
- `list_active_websocket_sessions`：按最近帧活动列出活跃 WebSocket 会话
- `get_websocket_session`：按 ID 查询 WebSocket 会话及消息（含 `raw_entry` 与每条消息 `raw`）
- `tail_websocket_messages`：按 `request_id + after_seq` 游标增量拉取消息
- `search_websocket_messages`：按关键字、方向、类型、opcode、close code、域名、请求 ID 等精确检索 WebSocket 消息
- `analyze_websocket_session`：汇总会话方向、消息类型、JSON 结构、close 事件
- `export_websocket_session_raw`：导出 WebSocket 会话原始 entry 与原始帧列表
- `health_report`：返回接收状态 + WebSocket 数据质量报告
- `repair_websocket_messages`：根据 raw 帧回填缺失字段（可 dry-run）
- `get_domains`：按域名聚合统计
- `analyze_api`：分析某域名 API 结构
- `generate_code`：从 HTTP 抓包生成调用代码

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `REQABLE_INGEST_HOST` | 接收服务监听地址 | `127.0.0.1` |
| `REQABLE_INGEST_PORT` | 接收服务监听端口 | `18765` |
| `REQABLE_INGEST_PATH` | 接收服务路径 | `/report` |
| `REQABLE_WS_EVENTS_PATH` | 增量 WebSocket 事件接收路径 | `/ws/events` |
| `REQABLE_DATA_DIR` | 本地数据目录 | 平台默认应用数据目录 |
| `REQABLE_DB_PATH` | SQLite 文件路径 | `${REQABLE_DATA_DIR}/requests.db` |
| `REQABLE_MAX_BODY_SIZE` | 每条请求/消息落库 body 最大字节数 | `102400` |
| `REQABLE_MAX_REPORT_SIZE` | 单次上报最大字节数 | `10485760` |
| `REQABLE_MAX_IMPORT_FILE_SIZE` | HAR 导入文件最大字节数 | `104857600` |
| `REQABLE_RETENTION_DAYS` | 数据保留天数 | `7` |
| `REQABLE_INGEST_TOKEN` | 可选本地鉴权 token | 未设置 |

## 隐私与保留策略

- 默认模式下数据仅存本机。
- 过期数据会按保留窗口自动清理，包含 WebSocket 消息。
- 若接收端离线，Reqable 对失败上报默认不重试，该次数据会丢失。

## 许可证

MIT
