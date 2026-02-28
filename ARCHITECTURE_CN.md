# Reqable MCP 架构设计（本地模式优先）

## 1. 结论

本项目采用“本地按需启动”的双通道架构：

- 通道 A（主）：Reqable `Report Server` 推送 HAR(JSON) 到本地接收端，提供准实时数据。
- 通道 B（备）：手动导入 HAR 文件，作为漏采补偿。

默认不做系统开机自启。仅在用户有长期后台采集需求时，再启用可选常驻采集器。

---

## 2. 目标与非目标

### 2.1 目标

1. 跨平台：Windows/macOS/Linux 统一运行方式。
2. 隐私本地化：默认仅监听 `127.0.0.1`，不经过云端。
3. 对齐现有工具体验：与 `proxypin-mcp` 尽量保持同类工具名与返回结构。
4. 可恢复：当推送漏采时可通过 HAR 补录。

### 2.2 非目标

1. 不做默认云端多租户存储与分发。
2. 不在首版支持复杂 RBAC/组织权限。
3. 不在首版承诺 Reqable 私有历史文件格式直读（缺少稳定规范）。

---

## 3. 参考的开源 MCP 启动模式

主流开源 MCP 服务普遍采用“客户端按需拉起 stdio 子进程”的方式：

1. MCP Transport 规范支持 stdio 与 HTTP/SSE，stdio 是本地集成常见形态。  
   Source: https://modelcontextprotocol.io/specification/2024-11-05/basic/transports
2. 官方 `servers` 仓库示例主要以 `npx/uvx` 配置 `command + args` 启动。  
   Source: https://github.com/modelcontextprotocol/servers
3. `mcp-proxy` 展示了远程 HTTP/SSE 的扩展方式，但属于可选增强，不是本地模式必选。  
   Source: https://github.com/punkpeye/mcp-proxy

因此本项目默认选择：`npx` 分发 + 本地 stdio 运行。

---

## 4. 总体架构

```text
Windsurf/Cursor/Codex
        |
        | MCP (stdio)
        v
+-------------------------------+
| reqable-mcp process           |
|                               |
| 1) MCP Tool Layer             |
| 2) Ingest HTTP Server         | <---- Reqable Report Server POST
| 3) Parse + Normalize + Dedupe |
| 4) SQLite Store               |
+-------------------------------+
        ^
        |
  import_har (fallback)
        |
    HAR files
```

核心原则：代码可从 npm 拉取，但数据只在用户本机流转。

---

## 5. 组件设计

### 5.1 MCP Tool Layer（stdio）

对外暴露工具（首版）：

1. `list_requests`
2. `get_request`
3. `search_requests`
4. `get_domains`
5. `analyze_api`
6. `generate_code`
7. `ingest_status`（新增，检查接收端在线状态）
8. `import_har`（新增，补录 HAR）

### 5.2 Ingest HTTP Server（本地接收）

1. 默认绑定 `127.0.0.1:<port>`，防止外网访问。
2. 路由示例：
   - `POST /report`：接收 Reqable 上报 HAR JSON。
   - `GET /health`：本地健康检查。
3. 可选鉴权：
   - `X-Reqable-Token`（本地共享密钥，默认可关闭）。

### 5.3 Parse / Normalize / Dedupe

1. 统一提取字段：`id/method/url/host/path/status/timestamp/headers/body`。
2. 去重键建议：
   - `sha1(method + url + started_time + request_body_hash + status)`。
3. 长文本截断：默认保留前 N KB，避免上下文爆炸。

### 5.4 SQLite Store

建议表结构（首版最小）：

1. `requests`：主数据。
2. `request_headers`：请求头（可 JSON 列，后续可拆分）。
3. `response_headers`：响应头（同上）。
4. `ingest_events`：上报接收日志与错误审计。

---

## 6. 启动模式（重点）

### 6.1 默认模式：按需启动（推荐）

1. IDE 通过 MCP 配置自动执行 `npx -y @scope/reqable-mcp@version`。
2. 进程启动时自动拉起本地 ingest server。
3. 用户抓包前调用一次 `ingest_status` 确认在线。

优点：零安装感、隐私本地、符合 MCP 习惯。  
缺点：若用户未先拉起 MCP，且 Reqable 发送失败不重试，会漏该次数据。

### 6.2 可选模式：手动常驻采集

1. 命令：`reqable-mcp collector`（非开机自启）。
2. 用户仅在长时间抓包场景手动开启，结束后关闭。

优点：漏采风险更低。  
缺点：多一个进程管理动作。

> 不推荐首版默认开机自启。开机自启仅作为可选能力。

---

## 7. 可靠性策略

针对 Reqable“发送失败不重试”特性（官方文档）：

1. 预检：`ingest_status` 返回 `listening=true/false`。
2. 可观测：记录最近 N 次 ingest 错误到 `ingest_events`。
3. 补偿：提供 `import_har` 手工补录。
4. 指导：文档明确“先 health 再抓包”。

Source: https://reqable.com/en-US/docs/capture/report-server

---

## 8. 配置设计（ENV）

1. `REQABLE_INGEST_HOST`（默认 `127.0.0.1`）
2. `REQABLE_INGEST_PORT`（默认 `18765`）
3. `REQABLE_DATA_DIR`（默认系统用户目录下应用数据目录）
4. `REQABLE_DB_PATH`（默认 `${REQABLE_DATA_DIR}/requests.db`）
5. `REQABLE_MAX_BODY_SIZE`（默认 `102400`）
6. `REQABLE_RETENTION_DAYS`（默认 `7`）
7. `REQABLE_INGEST_TOKEN`（可选）

跨平台目录建议使用 `platformdirs` 生成标准路径。

---

## 9. Windsurf/Cursor 使用流程

1. 配置 MCP：

```json
{
  "mcpServers": {
    "reqable": {
      "command": "npx",
      "args": ["-y", "@your-scope/reqable-mcp@1.0.0"]
    }
  }
}
```

2. 在 Reqable 设置 Report Server URL 指向 `http://127.0.0.1:18765/report`。
3. 开始抓包前，先调用 `ingest_status` 确认接收端在线。
4. 抓包后调用 `list_requests/search_requests/get_request` 分析数据。

---

## 10. 里程碑

### M0（1-2 天）

1. 项目骨架、配置、SQLite 初始化。
2. `POST /report` 接收与入库。
3. `list_requests/get_request/search_requests`。
4. `ingest_status`。

### M1（+1-2 天）

1. `get_domains/analyze_api/generate_code`。
2. `import_har` 补录。
3. 基础测试与错误处理。

### M2（+1 天）

1. 打包发布（PyPI + npm bridge）。
2. Windsurf/Cursor/Claude 配置样例。
3. 文档完善与发布清单。

---

## 11. 风险与边界

1. 发送端离线丢包：接收端不在线时，上报不会自动重试。
2. 大 body 成本：需要截断与按需返回，避免 token 过大。
3. 历史格式变化：不承诺首版直读 Reqable 私有历史文件。

---

## 12. 为什么这是当前最优解

1. 满足你的核心诉求：本地模式、跨平台、无需强制开机自启。
2. 遵循开源 MCP 生态主流启动习惯：`npx/uvx` 按需拉起。
3. 把风险留在本地可控范围：健康预检 + HAR 补录，而非引入云端复杂度。

