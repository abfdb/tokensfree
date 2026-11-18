# 旧实现与新内核对照片

旧实现 = `server.py`（mock 参考实现，行为冻结）；新实现 = `tokensfree/` 内核 + 可运行 host。

## 已对齐（有测试保护）

| 行为 | 旧实现 | 新实现 | 保护测试 |
|---|---|---|---|
| Host 校验 | 精确 loopback + 端口 | 同（共享 `edge/auth.py`） | `tests/test_legacy_edge_parity.py` |
| Origin 校验 | 无 Origin 或同源 | 同 | 同上 |
| 本地令牌 | Bearer，常量时间比较 | 同（方案名大小写不敏感） | 同上 |
| 错误形状 | `{error:{code,message,retryable,request_id}}` | 同（共享 `errors.py`） | `test_errors.py` / 契约矩阵 |
| 拒绝流式（mock 运行时） | 400 | 400 `unsupported_capability` | `test_contract_matrix.py` |
| 拒绝未知模型 | 400 | 400 `invalid_request` | 同上 |
| 幂等键冲突 | 409 | 409（新实现持久化） | `test_idempotency.py` / `test_persistence_integration.py` |
| 无付费回退 | 503 `no_free_provider` | 同 | 双方测试 |

## 有意差异（不是 bug）

| 项 | 旧实现 | 新实现 | 原因 |
|---|---|---|---|
| 会话令牌 | `GET /api/session` 每次启动随机下发 | 无该端点；启动时从 `TOKENSFREE_LOCAL_TOKEN` 读取稳定令牌 | DSH 等外部客户端需要稳定令牌，不能跟随重启轮换 |
| 模型目录 | 固定 `tokensfree-auto` | 由 provider spec 生成（如 `mock-a`） | 多供应商路由需要真实模型 id；客户端配置与网关同源 |
| 流式 | 一律 400 | mock 运行时 400；loopback 运行时真流式（chunked + `[DONE]`） | 按能力 fail-closed，不做静默降级 |
| 幂等缓存 | 进程内 5 分钟 / 100 键 | SQLite 持久化，跨重启有效 | 审计与重试语义 |
| 状态端点 | `/api/status` + 场景重置 `/api/scenario` | `/api/status`（无场景操作） | 演示场景只属于旧 mock UI |
| 事件 | 内存 100 条 | SQLite 事件表（脱敏、保留期） | 可追溯 |
| 工具调用 | 合成 fixture 单次实验端点 | 真实 tools 透传 + 真实 DSH 闭环 | MVP 目标 |

## 迁移建议

- 新客户端只用新实现端点（`/v1/models`、`/v1/chat/completions`、`/api/status`），不要依赖 `/api/session` 与 `/api/scenario`。
- 旧 `server.py` 继续保留用于对照与 UI 演示；P2 完成线后可以退场，但需先确认 UI 迁移。
