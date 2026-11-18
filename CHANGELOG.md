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
- 客户端采样参数原样转发：`temperature` / `top_p` / `stop` / `seed` / `response_format`（此前被静默丢弃）；`n>1` 明确拒绝
- 流式用量：`stream_options.include_usage` 会在终止帧后返回标准 usage 帧；`created` 字段改为真实时间戳
- `max_completion_tokens` 别名与 `presence_penalty`/`frequency_penalty` 透传（避免新 SDK 的 token 上限被静默忽略）
- 无法交付的能力（`n>1`、logprobs、非文本 modalities、audio）明确返回 `unsupported_capability` 而不是静默忽略
- 账号调度（LRU / 冷却 / 并发上限）、熔断器、账号池（`router/scheduling.py` / `health.py` / `account_pool.py`）
- 运行时默认按 provider 装配熔断器：连续失败后跳过该 provider，`/api/status` 可见，窗口结束后单探测恢复
- 熔断状态持久化到 `provider_health` 表（`--db` 时跨重启保留）
- 真实 key 自动登记为账号（仅存 sha256 指纹），成功请求写配额账本，重放不重复计费
- 账号作用域失败（额度耗尽 / 鉴权失败 / 限流）会更新账号状态（platform_signal、invalid、cooldown）并自动回退到下一个 key；内容级失败（refusal 等）仍然立即终止
- 账号状态参与路由：exhausted / invalid / 冷却中的 key 在准入阶段被跳过（`/api/status` 可见 `account_*` 原因）
- 替换 key 自动恢复账号：指纹变化时重置为 active 并清除冷却；同一 key 重启不会重置状态
- 运维工具：`/api/status` 增加账号摘要（状态/冷却/已计费请求数）与每个 provider 的模型/仿真标记/证据到期时间，`scripts/accounts_admin.py` 支持列出、激活、清除冷却
- 启动体验：`--port 0` 自动选空闲端口并打印真实端口；端口占用返回一行明确错误而不是 traceback
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
- 多密钥轮换：`TOKENSFREE_REAL_API_KEYS`（1–8 个）为每个 key 建候选 provider，可重试限流自动切下一个 key；请求按 round-robin 分配首个尝试
- key 支持从文件读取：`TOKENSFREE_REAL_API_KEY_FILE` / `_API_KEYS_FILE`（四个来源互斥）
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
