# Project Progress

## 2026-02-28

### Scope

面向 Reqable 的本地 MCP 服务架构设计与实施规划。

### Completed

1. 明确总体路线：本地模式优先，不走默认云端汇聚。
2. 完成架构设计文档：`ARCHITECTURE_CN.md`。
3. 确认主备数据通道：
   - 主：Reqable Report Server -> 本地 ingest。
   - 备：HAR 文件补录。
4. 确认启动策略：
   - 默认按需启动（MCP 客户端拉起）。
   - 可选手动常驻（非开机自启）。
5. 确认首版工具集合与环境变量设计。
6. 完成 `reqable-mcp` M0 代码骨架：
   - Python package 与 CLI 入口（`mcp` / `collector`）
   - SQLite 存储层与保留策略
   - 本地 ingest HTTP server（`POST /report` + `GET /health`）
   - 首批 MCP 工具：`ingest_status/import_har/list_requests/get_request/search_requests`
   - 辅助工具：`get_domains/analyze_api/generate_code`
7. 增加示例配置：
   - `examples/windsurf-config-npx.json`
   - `examples/claude-desktop-config.json`
8. 增加基础测试并通过：
   - `test_config.py`
   - `test_normalizer.py`
   - `test_storage.py`
   - `test_ingest_server.py`
9. 完成一轮性能与稳定性增强：
   - 列表查询从 `SELECT *` 改为按需列读取
   - 排序改为 `created_at DESC`（可命中索引）
   - 搜索逻辑下推到 SQLite（减少 Python 扫描）
   - ingest 增加 `REQABLE_MAX_REPORT_SIZE` 防止超大 payload
   - retention 改为周期触发，不仅在启动时清理
10. 完成发布资产补齐：
   - 新增 `npm-bridge`（`npx` 启动器）
   - 新增 CI 工作流（ruff + pytest + build）
   - 新增 `RELEASE_CHECKLIST.md`
   - 补充 `LICENSE` 与 `pyproject` 项目链接元数据
11. 完成正式发布：
   - PyPI：`reqable-mcp==0.1.0` 已发布
   - npm：`reqable-mcp@0.1.0` 已发布
   - 已验证 `npx -y reqable-mcp@latest --help` 与 `uvx --from reqable-mcp reqable-mcp --help` 可用
12. 完成仓库规范化（对齐 proxypin-mcp 风格）：
   - 新增治理文档：`CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、`CHANGELOG.md`
   - 新增社区模板：`.github/ISSUE_TEMPLATE/*`、`.github/pull_request_template.md`
   - 新增发布工作流：`.github/workflows/publish.yml`
   - 新增开发规范：`.editorconfig`、`.pre-commit-config.yaml`
   - 升级 `README.md` 与 `README_CN.md`（完整使用流、工具说明、环境变量矩阵、隐私说明）
13. 完成 Codex MCP 配置统一：
   - `proxypin` 改为 `npx -y proxypin-mcp@latest`
   - `reqable` 改为 `npx -y reqable-mcp@latest`
   - 已通过 `codex exec` 实测两者工具调用
14. 按当前维护偏好移除 GitHub Actions：
   - 删除 `.github/workflows/ci.yml`
   - 删除 `.github/workflows/publish.yml`
15. 完成一轮代码鲁棒性修复：
   - `get_domains` 改为 SQL 聚合，避免因查询窗口截断导致统计失真
   - `import_har` 增加文件大小限制（`REQABLE_MAX_IMPORT_FILE_SIZE`）
   - ingest 解压后大小二次校验，拦截压缩绕过型超限 payload
   - ingest 启动失败事件增加冷却，减少端口冲突时日志刷屏
   - 新增对应测试用例并通过
16. 完成 0.1.1 全量验收与发布：
   - 质量门禁：`ruff` 通过，`pytest` 11 项通过，`uv build` 通过
   - 合成数据基准（10,000 条）：
     - ingest：约 219.46ms
     - `get_domains(limit=1)`：约 4.646ms（统计结果准确）
     - `list_requests(limit=20)`：约 0.246ms
     - `search_requests(limit=20)`：约 1.916ms
   - 发布结果：
     - PyPI：`reqable-mcp==0.1.1`
     - npm：`reqable-mcp@0.1.1`
   - 回归：`npx -y reqable-mcp@latest --help` 可启动

### In Progress

1. 补充常见故障排查文档。

### Pending

1. 评估是否将 `import_har` 扩展为目录批量导入。

### Notes

1. 当前会话未发现可用 `serena` MCP server，未执行 `serena activate`。
2. 为保证可用性，建议在抓包前先调用 `ingest_status` 做接收端预检。
