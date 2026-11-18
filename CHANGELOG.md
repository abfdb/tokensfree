# Changelog

本文件记录 tokensfree 从"本地 mock 原型"演进为"可被真实客户端驱动、默认零外发的本地网关"的过程。
详细设计与验收标准见 [设计文档](docs/superpowers/specs/2026-11-18-tokensfree-productionization-design.md) 与 [操作手册](docs/operator-guide.md)。

## v0.6.0（未发布，分支 p0-engineering-baseline）

### 新增

**内核（框架无关，零运行时依赖）**
- 冻结配置对象与显式环境校验（`tokensfree/config.py`）
- 统一错误契约与唯一合法错误体（`errors.py`）
- 结构化日志与字段白名单（`obs.py`）
- 适配器协议、准入凭据（不透明 `Admission`）、上游错误归一化（`adapters/base.py`）
- 候选构建、准入闸门、有界重试执行（`router/candidates.py` / `admission.py` / `execute.py`）
- 客户端模型选择：`tokensfree-auto` 由路由器决定，具体模型 id 精确匹配，未知模型返回 404 `model_not_found`
- 账号调度（LRU / 冷却 / 并发上限）、熔断器、账号池（`router/scheduling.py` / `health.py` / `account_pool.py`）
- 运行时默认按 provider 装配熔断器：连续失败后跳过该 provider，`/api/status` 可见，窗口结束后单探测恢复
- SQLite 持久化：迁移、幂等键、只增账本、事件（`store/`），并接入可运行服务（`--db`，默认进程内）

**边缘与协议**
- OpenAI 形状的请求解析与响应构造（`edge/protocol.py`），含工具形状严格校验
- SSE 编码器 / 解析器 / 工具增量装配（`sse.py`、`adapters/stream_parse.py`）
- 流式核心：首帧前可回退、首帧后错误终态、断流必有终态帧（`edge/stream_core.py`）
- HTTP 后端契约、ASGI 适配器、可运行的 stdlib asyncio host（`edge/backend.py` / `asgi.py` / `host.py`）
- 结构化访问日志（每请求一行，无 prompt）与 `x-request-id` 响应头
- `/api/status` 含版本与运行时长；CLI 支持 `--version`
- 真实调用闸门：开关 + 授权 + 未过期免费证据三者同时成立才放行（默认关闭）
- 环境变量驱动的真实供应商装配（`--real` + `TOKENSFREE_REAL_*`）：D2 之后只需配置，不用改代码
- 真实供应商验收脚本 `scripts/real_smoke.py`（`--fixture` 自检模式用本地假上游，真实模式驱动 DSH）
- 真实供应商直连探针 `scripts/real_probe.py`（非流式 / 流式 / 工具调用三条路径，成本极小）
- 免费证据快照工具 `scripts/evidence_snapshot.py`（精确匹配模型行、只认输入/输出价格列、sha256 + 到期时间）
- 非流式响应的 `tool_calls` 现在完整透传给客户端（此前会被丢弃）

**上游适配器**
- loopback 非流式 / 流式适配器（仅 127.0.0.1，硬截止时间）
- OpenAI 兼容适配器（默认禁用；密钥不落日志、拒绝明文远程、远程需显式允许、流式硬时限）
- 真实 DSH headless 端到端冒烟（流式 + 23 个工具透传 + `read` 工具往返 + 退出码 0），并作为 opt-in 回归门禁

### 修复（源码审计发现，均有测试）
- 流结束但无终止事件时可能污染协议 → 补终态帧 / 断流错误
- 流式响应首字节前失败返回 200 后断连 → 改为真实 JSON 503/502
- host 遇未捕获异常直接断连 → 返回结构化 500
- fixture 运行时 60 秒后全员 503 → 默认 TTL 改为长寿命
- `finish_reason` 被硬编码为 `stop` → 原样透传
- 幂等键无长度上限、幂等预留中途过期导致 500 → 已修复
- 流式传输只有单次读超时 → 增加墙钟总期限
- 契约上限挡住真实客户端（DSH 的 `max_tokens:128000`、`content:null`）→ 已兼容

### 文档与工具
- 设计文档（含附录 A–G）、P0/P1/P2 实施计划、操作手册、旧/新实现对照表、生产化路线图
- 一条命令验收 `scripts/verify_p0.py`（lint / 测试 / 契约矩阵 / 静态网络导入门禁 / 密钥扫描）
- CI：三平台 × 3 个 Python 版本，含导入出口门禁
- 干净克隆可复现（锁定开发依赖版本）

### 已知限制 / 待决
- **D2 未决**：没有启用真实供应商（`openai_compat.py` 与闸门已就绪，只差 key + 证据 + 开关）
- 无系统凭据库（keyring）
- Windows 无 Linux 网络命名空间，"零外发"由配置与断言保证
- 多协议端点（`/v1/responses`、`/v1/messages`）、账号池接线、UI 迁移尚未排期
