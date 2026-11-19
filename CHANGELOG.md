# Changelog

本文件记录 tokensfree 从"本地 mock 原型"演进为"可被真实客户端驱动、默认零外发的本地网关"的过程。
详细设计与验收标准见 [设计文档](docs/superpowers/specs/2026-11-18-tokensfree-productionization-design.md) 与 [操作手册](docs/operator-guide.md)。

## 未发布

- 易上手优化：新增 `scripts/quickstart.ps1`（创建 .venv、生成本地令牌 .env、启动）、`scripts/start.ps1`、`.env.example`；主程序新增 `--env-file`（仅接受 `TOKENSFREE_*`，真实环境变量优先，兼容 BOM）；README 重写为快速开始优先

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
- 无法交付的能力（`n>1`、logprobs、非文本 modalities、audio、非空 `logit_bias`）明确返回 `unsupported_capability` 而不是静默忽略
- 账号调度（LRU / 冷却 / 并发上限）、熔断器、账号池（`router/scheduling.py` / `health.py` / `account_pool.py`）
- 运行时默认按 provider 装配熔断器：连续失败后跳过该 provider，`/api/status` 可见，窗口结束后单探测恢复
- 熔断状态持久化到 `provider_health` 表（`--db` 时跨重启保留）
- 真实 key 自动登记为账号（仅存 sha256 指纹），成功请求写配额账本，重放不重复计费
- 账号作用域失败（额度耗尽 / 鉴权失败 / 限流）会更新账号状态（platform_signal、invalid、cooldown）并自动回退到下一个 key；内容级失败（refusal 等）仍然立即终止
- 账号状态参与路由：exhausted / invalid / 冷却中的 key 在准入阶段被跳过（`/api/status` 可见 `account_*` 原因）
- 替换 key 自动恢复账号：指纹变化时重置为 active 并清除冷却；同一 key 重启不会重置状态
- 运维工具：`/api/status` 增加账号摘要（状态/冷却/已计费请求数）与每个 provider 的模型/仿真标记/证据到期时间，`scripts/accounts_admin.py` 支持列出、激活、清除冷却
- 启动体验：`--port 0` 自动选空闲端口并打印真实端口；端口占用返回一行明确错误而不是 traceback
- 健壮性：账本/事件/幂等完成/账号状态写入失败不再把成功响应变成 500，只记录 `bookkeeping_error` 日志事件
- `--db` 文件路径自动创建父目录、展开 `~`；无法打开时输出单行 `cannot open db ...` 错误
- 修复：只设置 `TOKENSFREE_REAL_API_KEY_FILE` / `_API_KEYS_FILE` 启动时会误报缺少 API key（现在文件来源算作合法来源）；顺带减少熔断健康记录的重复写入
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
- 真实联调验证（2026-11-19）：智谱 glm-4.7-flash 探针（非流式/流式/工具）与真实网关 + DSH 验收全部通过；`real_probe.py` 增加 1305 退避重试与 `--max-tokens`（推理模型需更大预算），OpenAI 兼容适配器回填 provider 报告的 `model`
- Windows 桌面外壳：`python -m tokensfree.desktop`（tkinter 控制面板、ctypes 系统托盘、开机启动、网关进程管理、`--self-check`、快捷方式带 `--start-gateway` 自动启动网关），`install/install.ps1` + `uninstall.ps1` 创建/清理用户级快捷方式
- 网页端通道管理：`/api/profiles`（列表/新增/更新/删除/测试连接，本地令牌鉴权，key 写入凭据管理器，保存后重启生效）+ 网页控制台"接入通道"卡片；OpenAI 适配器新增 `list_models()` 供连通性检查
- 自定义接入通道：providers.json + `--profiles` 多通道运行时，支持本机 Ollama/LM Studio/vLLM 与远端 HTTPS OpenAI 兼容 endpoint；成本策略 free_verified / zero_cost_local / user_paid（默认禁止，需 TOKENSFREE_ALLOW_USER_PAID=1）；密钥只存 Windows 凭据管理器；桌面面板新增"接入通道"页（增删改/测试连接/凭据写入）
- 网页控制台：新内核新增 `/`（单页控制台：模型选择/流式对话/状态面板/连接信息复制）与 `/api/session`（loopback 本地令牌），静态资源走 `/web/*`，CSP + 路径白名单
- 启动前自检：`python -m tokensfree --check [--real]` 只校验配置（key/证据/db）并输出 JSON，不绑定端口、不发起请求
- Windows 系统凭据库：`tokensfree/credstore.py` + `scripts/credential_store.py`（命名空间 `tokensfree/<provider>/<alias>`），运行时用 `TOKENSFREE_REAL_API_KEY_STORE` 读取，key 不落文件/环境变量；其他平台 fail-closed
- 流式请求不支持 `Idempotency-Key`：带键的 `stream:true` 请求返回 400 `invalid_request`（流无法逐字节重放，静默忽略会破坏 exactly-once 语义）
- 流式请求现在同样写入配额账本与事件表（此前只有非流式请求被记账，DSH 的流式流量漏计）
- 免费证据可直接从 `evidence_snapshot.py` 生成的快照读取（`TOKENSFREE_REAL_EVIDENCE_SNAPSHOT`），并校验 model/provider 绑定，避免手工抄时间戳出错
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
