# tokensfree 生产化改造方案（P0–P5）

> **状态：已被 `docs/superpowers/specs/2026-11-18-tokensfree-productionization-design.md` 细化并取代（本文保留为分析记录）。**
>
> 版本：v1.0 ／ 适用基线：abfdb/tokensfree @ 499576e（main）
> 读者：负责实施的 AI 或工程师。本文件给出可执行的架构决策、接口草图、阶段验收门、风险与选型；不替代实现。

---

## 0. 先看实测：这个仓库现在到底是什么状态

我在 Windows + Python 3.10.8 上把仓库跑了一遍，先给事实，再谈改造：

| 实测项 | 命令 | 结果 |
|---|---|---|
| 单元/集成测试 | `py -3 -m unittest -v` | **Ran 119 tests, 1 failure, 17.2s** |
| 失败用例 | `test_gateway_agent.GatewayAgentTests.test_client_cannot_choose_path` | Windows 路径反斜杠被 JSON 转义（`\\`），用原始字节子串断言必然失败（test_gateway_agent.py:69） |
| DSH 契约探测 | `py -3 check_dsh_contract.py` | **exit 0，8/8 符合预期**：models 200、text-only 200、stream/tools/developer-role 400、responses/messages/generateContent 404，且所有拒绝路径网关上游尝试数 = 0 |
| 工程元数据 | — | 无 `.github/`、无 `LICENSE`、无 `pyproject.toml`、无 `requirements.txt`、无 Dockerfile |
| 代码体量 | — | 21 个 Python 文件（其中 12 个是 test_*），核心 server.py 208 行、protocol.py 134 行、client.py 98 行、credentials.py 88 行、providers.py 55 行 |

**结论先行**：README 的自述（mock-only、fail-closed、拒绝而非假装成功）与代码完全一致，没有夸大。这个仓库目前是一份**可执行的"安全边界规格 + 协议契约测试台"**，不是产品。它的最大资产是"什么不能做"的判定逻辑与拒绝测试；最大缺口是"真实能力"一条都没有。

---

## 1. 产品定义与不可退让的不变量

在写任何代码前先把定义钉死，否则后续每个决策都会漂移。

**tokensfree = 本机运行的"免费额度路由层"**

- 输入：OpenAI 兼容请求（先 `/v1/chat/completions`，后续 `/v1/responses`、`/v1/messages`）
- 输出：把请求路由到"当前可用、且可证明处于免费层"的上游；失败按策略转移；**永不付费**
- 边界：仅绑定 127.0.0.1；不做远程托管；不代注册账号；不抓网页 Cookie；不做 GPU 调度

**三条不变量（任何 PR 都不得违反）**

- **I1 Fail-closed**：上游不满足「用户授权 + 能力匹配 + 免费证据有效 + 未过期」→ 不发起任何网络请求，直接拒绝并说明原因。
- **I2 No silent degradation**：不允许为了"让请求成功"悄悄丢掉 `tools`、`stream`、多模态或 developer role。要么正确实现，要么明确 400/501。
- **I3 Zero-bill**：在"平台侧硬停止"被真实验证之前，本地只允许**已确认免费层**的模型；付费回退永久关闭。本地计数只能触发预警，不能作为"肯定没超"的证据。

---

## 2. 现状评估：七条短板逐条落到代码

| # | 短板 | 代码证据 | 真实影响 |
|---|---|---|---|
| 1 | 只有 MockAdapter | `providers.py:50` 硬阻断 `real_provider_not_implemented`；`server.py:88` 只有 `MockAdapter(result)` | 无任何真实价值，只能是演示 |
| 2 | 无 SSE / 无工具调用 | `server.py:66` 拒绝 stream、`server.py:67` 拒绝 tools | 真实 DSH 主请求就是 `stream:true` + 23 个 tools → 必然 400（仓库 docs 已实测记录） |
| 3 | 无持久化 / 无账号池 / 无额度 | `server.py:22-24` 全部内存；去重缓存 5 分钟 / 100 键，进程重启即失忆 | 无法审计、无法跨重启幂等、无法管理多个账号 |
| 4 | 全局串行锁 | `server.py:22` 单个 `threading.RLock`；`server.py:47`、`76`、`109` 在持锁状态下覆盖**整个上游调用循环** | 真实慢网络下吞吐退化为 1，任何长请求阻塞全部请求 |
| 5 | DSH 被 400 | 同 #2；`check_dsh_contract.py` 8 项全拒 | 不能作为任何 harness 的模型后端 |
| 6 | 资源目录是静态快照 | `static/catalog/data.js` 手写 10 条，字段 `checked:'2026-11-17'`，无任何 API | 用户看到的"额度机会"与真实状态无关 |
| 7 | 工程化缺失 | 无 CI / LICENSE / pyproject；测试在 Windows 上已有 1 例失败 | 不可协作、不可信、不可交付 |

### 2.1 原清单之外的隐性债务（同样会挡路）

1. **错误模型不统一**：`server.py` 中错误是散落的 dict 字面量，code 有中文 message 混排，客户端无法稳定分支。需要 `{error:{code,message,retryable,provider,request_id}}` 单一模型 + 错误码表。
2. **会话 token 无 TTL 且公开下发**：`GET /api/session` 明文返回，重启轮换。引入多账号/多客户端后，必须有"调用方身份"概念（本地进程级 client_id + 短期票据）。
3. **响应缺 `usage`**：DSH 等 harness 需要 token 用量做上下文管理；当前响应无 `usage` 字段。
4. **无结构化日志与 request_id 贯穿**：只有 100 条内存 event，排障靠猜。
5. **单文件混合职责**：`server.py` 同时做 HTTP 边缘、路由策略、静态资源、SSE——加功能必然失控。
6. **测试耦合实现细节**：`check_dsh_contract.py` 用 `unittest.mock.patch('server.MockAdapter.complete')` 统计调用次数，重构即碎。应改为在 adapter 边界注入可观测的 fake。
7. **无调用方配额**：本机任意进程拿到 token 即可无限调用；接真实上游后这会直接烧掉免费额度。

---

## 3. 目标架构

### 3.1 分层与数据流

~~~text
client (DSH / curl / python client)
        │  OpenAI-compatible HTTP, optional SSE
        ▼
┌──────────────────────────────────────────────┐
│ L2 Edge      本地鉴权 / Host-Origin / 体积限制 │
│              client_id 配额 / 请求预算 deadline │
└──────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────┐
│ L3 Router    候选生成 → 策略过滤 → 打分排序     │
│              尝试 → 失败分类 → 转移 → 停止       │
│              (唯一允许做"是否放行"判断的地方)    │
└──────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────┐
│ L4 Adapters  per-provider: 能力声明/请求编码    │
│              流式解析/错误归一化/取消            │
└──────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────┐
│ L5 State     SQLite(WAL): 账号/额度账本/幂等键   │
│              keyring: 凭证密文（不入库）         │
│              健康状态与熔断窗口                  │
└──────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────┐
│ L6 Obs       结构化日志(脱敏) / 内存事件 / 指标   │
└──────────────────────────────────────────────┘
~~~

### 3.2 关键架构决策（附理由）

| 决策 | 选择 | 理由 / 备选 |
|---|---|---|
| 运行时模型 | **单进程 asyncio** | SSE + 长连接 + 多上游并发，线程模型会撞 GIL 与锁；单用户本机场景无需多进程。备选：多线程 + 每 provider 线程池（复杂度更高、取消语义差） |
| Web 框架 | **FastAPI + uvicorn** | 路由/校验/OpenAPI 开箱即用，SSE 用 `StreamingResponse` 即可；备选 aiohttp（更轻但手写路由）、Starlette（更贴近现状） |
| 上游 HTTP | **httpx.AsyncClient** | 原生 stream/timeout/limits；必须 `follow_redirects=False`、固定 base_url |
| 持久化 | **SQLite + WAL + 手写 SQL 迁移** | 单机零运维、事务可靠；不引 ORM（依赖最小、SQL 可审计）。备选：DuckDB（分析场景） |
| 凭证 | **keyring（OS 凭据库）+ 库内只存引用** | Windows Credential Manager / macOS Keychain / Linux Secret Service；符合仓库既有 credential-boundary 设计 |
| 配置 | **pydantic-settings，进程启动时读一次** | 请求处理中禁止读 env，避免注入与竞态 |
| 是否用 LiteLLM/LangChain | **不用** | 它们会隐藏协议细节、内置重试与回退，正好与 I1/I2/I3 冲突。可以作为**参考实现**阅读 |
| 语言 | Python 3.11+（保底 3.10） | 现有代码即 Python；asyncio 3.11 有 TaskGroup |

### 3.3 进程与安全边界

~~~text
[pid 1] tokensfree gateway (asyncio)
   ├── edge server (127.0.0.1:8765)
   ├── router + adapters (same process)
   ├── sqlite writer (single connection, serialized writes)
   └── keyring access (only during credential lease)

[pid 2..n] 无。真实 provider 调用不走子进程，但凭证 lease 必须
           在调用结束后立刻失效（用后就丢，不留引用）。
~~~

**为什么不再做"适配器进程隔离"**：成本高、收益不确定；本机单用户场景用"凭证 lease + 最小权限 + 不落盘"已够。若后续支持"不受信插件式 adapter"，再引入子进程。

---

## 4. 阶段路线（每阶段一个 PR 集，附验收门）

### P0 — 工程基线（1–2 天，必须先做）

- 修 Windows 测试：`test_gateway_agent.py:69` 应 JSON 解析 SSE 帧后比较 `file_path` 字段，而不是对原始字节做子串断言。
- 新增 `pyproject.toml`：`[project]` + ruff + mypy + pytest 配置；`uv.lock` 或 `requirements.txt` 二选一（推荐 uv）。
- 新增 `LICENSE`（建议 Apache-2.0；README 已注明"待决定"，需作者拍板）。
- `.github/workflows/ci.yml`：matrix = ubuntu/windows/macos × 3.10/3.11/3.12，跑 `pytest -q` + `python check_dsh_contract.py`。
- 统一错误模型 + `usage` 字段（mock 也返回）+ 结构化日志（`request_id` 贯穿）+ `client_id` 字段。

**验收门 G0（全部可执行）**
- `git clone && uv sync && pytest -q` 在 Windows/macOS/Linux 三平台全绿
- CI 徽章可点，最近一次运行通过
- `curl /v1/chat/completions` 返回体含 `usage` 与 `request_id`
- 仓库根出现 LICENSE、pyproject.toml

### P1 — Mock → 真实 Adapter 单通道打通（3–5 天，默认关闭）

- 新增 `provider_adapter.py`（接口见 §5.1），把 `providers.py` 的 `MockAdapter` 升级为"提供了 `ChatAdapter` 协议的其中一种实现"。
- 新增 `adapters/gemini.py`（或 `openrouter.py`，二选一，先接**一家**）。
- 把 `providers.evaluate()` 拆成两段：`check_eligibility()`（不变量 I1）+ `admit()`（运行期准入）。**只新增分支，不删除既有拒绝分支。**
- 真实调用默认关闭：`TOKENSFREE_ENABLE_REAL=0`。开启需要同时满足三件事（见 §5.1）。
- 用本地假上游（respx 或现成的 `protocol.LoopbackFixtureAdapter`）覆盖 401/403/429/5xx/超时/断流/畸形 JSON/重定向。

**验收门 G1**
- 默认配置下：任何真实 provider 配置都无法触发网络请求（用断网环境 + 抓包/代理黑名单证明外发连接数为 0）
- 显式开启 + 合成假上游：完整走通"请求 → adapter → 归一化结果 → 响应"，且 429/5xx 转移符合策略
- 新增 ≥ 30 条 adapter/策略测试；既有 119 条仍全绿

### P2 — SSE + 工具调用（3–5 天）

- 实现 `POST /v1/chat/completions` 的 `stream:true`（协议见 §6）。
- 实现 `tools` / `tool_choice` 的透传与 `tool_calls` 增量聚合；gateway **不执行**工具。
- developer role：映射为 system（配置项 `developer_role_policy: map|reject`），默认 map。
- 真实 DSH headless 端到端 smoke（见 §9）。

**验收门 G2**
- 真实 DSH 完成一次 `read` 工具往返：请求含 stream+tools → 网关转发 → tool_calls 下发 → 工具结果回传 → 最终回复
- 断连测试：客户端中止后上游请求被取消，无泄漏任务，账本记 `canceled`
- 流中途错误：已发出 200 后只能以 SSE error 事件收尾，且**不切换**供应商

### P3 — 账号池 + 凭证隔离 + 额度账本（1–2 周）

- keyring 凭证存储；库内只存 `credential_ref` + 指纹
- 账号状态机（active/suspended/exhausted/invalid/cooldown）
- append-only 额度账本 + 平台信号驱动的耗尽判定（§7）
- SQLite schema + 迁移 + 幂等键持久化
- 导入/导出（审计用 JSONL）

**验收门 G3**
- 杀掉进程后重启：幂等键仍生效（同一 key 同内容返回原结果，不重复打上游）
- 凭证不出现在：日志、DB 文件、stdout、Git 历史、崩溃转储（用 `grep` 与 keyring 断言验证）
- 账本可重建：给定事件流可重放得到相同余额视图

### P4 — 并发 / 取消 / 超时 / 背压（约 1 周）

- 删除覆盖网络 IO 的全局锁；改为每请求上下文 + 每 provider 信号量/限速器
- 超时预算分解；取消传播；熔断；有限重试
- 并发压测脚本 `scripts/loadtest.py`

**验收门 G4**
- 20 并发流式请求：无相互阻塞（P95 首字节 < 1.5s，对比串行基线）
- 慢上游（人为 5s 延迟）不阻塞其他 provider 的请求
- 取消后 5s 内上游连接关闭、信号量归还

### P5 — 服务化与可观测（1 周）

- Windows 服务/托盘常驻（可选 WinSW 或 NSSM）、开机自启
- 打包（PyInstaller 单文件，或 shiv）
- 指标（Prometheus 文本格式 /metrics，仅本机）
- UI 增加"真实模式需二次确认"的显式开关与风险提示
- 发布 v1.0，更新 README 与 docs

**验收门 G5**
- 干净 Windows 机器一键安装并可跑通 DSH smoke
- 未开启真实模式时，UI 与 API 均明确显示 MOCK，且外发连接为 0

---

## 5. 问题一：如何从 mock 过渡到真实 adapter，且不破坏 fail-closed

### 5.1 核心设计：单一闸门 + 三级信任 + 三重开关

**唯一闸门**：所有"能不能打这个上游"的判断都必须经过 `router/admission.py`，`adapters` 内部**不得自行**决定重试/回退。现有 `providers.evaluate()` 已经具备这个雏形，扩展而不是重写。

~~~python
class TrustTier(str, Enum):
    MOCK = "mock"                  # 合成 fixture，无网络
    REAL_DECLARED = "real_declared"  # 用户声明免费，未验证
    REAL_VERIFIED = "real_verified"  # 已用真实账号验证过免费层与硬停止

@dataclass(frozen=True)
class ProviderSpec:
    name: str
    model: str
    tier: TrustTier = TrustTier.MOCK
    cost_class: str = "unknown"          # free | trial | paid | unknown
    evidence: Evidence | None = None
    capabilities: frozenset = frozenset({"text"})
    enabled: bool = False
    limits: ProviderLimits = ProviderLimits()

def check_eligibility(spec, ctx) -> Decision:
    # 保持现有拒绝分支语义，只做"资格"判断，不碰网络
    if not ctx.app_config.enable_real and spec.tier is not TrustTier.MOCK:
        return Decision(False, "real_provider_disabled_by_config")
    if spec.tier is TrustTier.REAL_DECLARED:
        return Decision(False, "free_status_unverified")
    if spec.tier is TrustTier.REAL_VERIFIED and not evidence_valid(spec.evidence, ctx.now):
        return Decision(False, "expired_free_evidence")
    ...  # capability / authorization / enabled / clock 沿用现有逻辑
    return Decision(True, "admitted")
~~~

**三重开关（缺一不可）**：真实调用必须同时满足

1. 启动配置 `TOKENSFREE_ENABLE_REAL=1`（默认 0，只在启动时读一次，写入不可变 AppConfig）
2. 该 provider 的 `tier == REAL_VERIFIED`，且 Evidence 未过期（TTL 默认 24h）
3. 用户在该 provider 上显式完成过一次"授权确认"（记录在 DB，可撤销）

任何一条不满足 → 拒绝，**不产生任何外发连接**。这样即使配置写错、模型名改错，也仍然是 fail-closed。

### 5.2 Adapter 接口（新增文件 provider_adapter.py）

~~~python
class ChatMessage(TypedDict): role: str; content: str
class ChatRequest(NamedTuple):
    messages: list[ChatMessage]
    model: str
    max_tokens: int
    stream: bool
    tools: list[dict] | None
    tool_choice: str | dict | None
    temperature: float | None

class ChatChunk(NamedTuple):
    delta: dict            # OpenAI 形状的 delta
    finish_reason: str | None
    usage: dict | None

class NormalizedError(Exception):
    code: str              # 见错误码表
    retryable: bool
    retry_after_s: float | None
    provider: str

class ChatAdapter(Protocol):
    name: str
    capabilities: frozenset[str]           # {"text","tools","stream"}
    async def complete(self, req: ChatRequest, cred: CredentialLease,
                       *, deadline: float) -> ChatResult: ...
    async def stream(self, req: ChatRequest, cred: CredentialLease,
                     *, deadline: float) -> AsyncIterator[ChatChunk]: ...
    def classify(self, exc: BaseException) -> NormalizedError: ...
~~~

**错误归一化表（必须严格区分，不能把 429 一律当"额度耗尽"）**

| 上游信号 | NormalizedError.code | retryable | 账号状态动作 |
|---|---|---|---|
| 401 / 403 | auth_invalid | 否 | 账号 invalid，停用；不切换给同账号 |
| 402 / 余额不足文本 | billing_required | 否 | 账号 exhausted |
| 429 + Retry-After ≤ 60s | rate_limited | 是（按 Retry-After） | cooldown(until) |
| 429 + Retry-After > 60s 或日配额耗尽文本 | quota_exhausted | 否 | exhausted（需平台信号确认） |
| 5xx | upstream_error | 是（1 次，指数退避） | 记失败，可能触发熔断 |
| 超时 / 连接重置 | timeout | 是（未发出首字节时） | 记失败 |
| 3xx | redirect_rejected | 否 | 配置错误告警 |
| 内容过滤 | refusal | 否 | **不切换**（I2：不得用换供应商规避安全拒绝） |
| 畸形 JSON / 超大响应 | invalid_response | 否 | 告警 |

### 5.3 迁移顺序（降低风险）

1. 先把 `MockAdapter` 包装成 `ChatAdapter`，全链路（HTTP → router → adapter → 响应）不变行为，测试全绿。
2. 再把 `protocol.LoopbackFixtureAdapter`（已有）升级为正式的 `loopback` adapter，用于集成测试。
3. 然后接第一家真实 provider，但**默认禁用**；用假上游覆盖全部错误分支。
4. 最后用真实账号做一次人工验收，把该 provider 的 tier 升到 `REAL_VERIFIED`，记录 evidence（账号、模型、限额截图 hash、验证时间）。

### 5.4 合规硬约束（写进代码注释 + 启动断言）

- 只用平台**官方 API**，禁止提取网页登录 Cookie
- 禁止批量注册账号、禁止多账号规避额度
- 只调用用户**自己**的账号，且用户明确授权
- 每次真实调用前确认该模型处于免费层；无法确认即拒绝
- 不对任何平台做自动化注册/验证码处理

**风险**：即使全部合规，平台侧仍可能把"多账号/代理"判定为滥用。缓解：默认单账号、限速保守、文档明示风险、提供一键关闭。

---

## 6. 问题二：SSE 流式与工具调用的协议设计

### 6.1 端点与响应头

~~~http
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer <local-session-token>
Accept: text/event-stream

HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-store
Connection: keep-alive
X-Accel-Buffering: no
~~~

**关键约束：一旦 HTTP 200 与首个字节发出，就不能再改状态码，也不能悄悄换供应商拼接结果（I2）。** 上游错误必须在"发出首字节之前"完成转移。

### 6.2 事件序列状态机

~~~text
[idle] --请求校验--> [preparing] --admit/连接上游--> [started]
[started]  --首个 chunk--> [streaming]
[streaming] --role delta--> 发送 {"delta":{"role":"assistant"}}
[streaming] --content 增量--> 发送 {"delta":{"content":"..."}}  (可多次)
[streaming] --reasoning 增量--> 可选 {"delta":{"reasoning_content":"..."}}
[streaming] --tool_calls 增量--> 发送 {"delta":{"tool_calls":[{index,id,type,function:{name,arguments}}]}}
[streaming] --结束--> 发送 {"delta":{},"finish_reason":"stop|length|tool_calls|content_filter"}
[preparing/started 内错误] --> 发送 event: error，然后关闭（HTTP 已是 200）
[任意] --发送 [DONE]--> [done]
~~~

规则：

1. `delta.tool_calls` 按 `index` 分片：首个分片带 `id` 与 `function.name`，后续分片只带 `function.arguments` 片段；**同一 index 的 id 不得重复下发不同值**。
2. `finish_reason` 只在最后一个 chunk 出现一次，之后必须紧跟 `data: [DONE]`。
3. `usage`：默认不下发；请求带 `stream_options.include_usage=true` 时，在 `[DONE]` 之前的最后一个 chunk 下发（`choices: []` + `usage`）。
4. 心跳：空闲超过 15s 发注释行 `: keep-alive`，避免中间代理断连（仅本机也建议保留，便于测试）。
5. 中途错误：`event: error\ndata: {"error":{"code":"upstream_disconnected","message":"...","request_id":"..."}}\n\n` 后关闭连接，**不重试、不拼接**。
6. 取消：客户端断开 → 取消上游 httpx 请求、释放该 provider 信号量、账本记 `canceled`、不消耗重试预算。
7. 背压：同一事件循环中 await 客户端写；客户端慢 → 上游读自然暂停。设置上游解析缓冲上限（如单行 1MB）。

### 6.3 工具调用：只转发，不执行

- gateway 收到带 `tools` 的请求 → 能力校验：该 provider 的 `capabilities` 必须含 `tools`，否则拒绝（不降级）。
- 收到上游 `tool_calls` → 原样按 OpenAI 形状转发，`finish_reason="tool_calls"`。
- 下一轮客户端带回 `role:"tool"` + `tool_call_id` → 原样放进 messages 再次路由（允许换 provider？**不允许**，同一对话的 tool 结果应与产生 call 的 provider 保持一致，除非显式允许）。
- `tool_choice` 透传；`parallel_tool_calls` 支持则透传，不支持则拒绝。

### 6.4 幂等键在流式下的语义

| 阶段 | 可重试？ | 幂等键状态 |
|---|---|---|
| 校验/准入前 | 是（未消耗任何上游） | reserved |
| 已连上游但未发首字节 | 是（可安全换 provider） | reserved |
| 已向客户端发首字节 | **否** | streaming（不可重试、不可切换） |
| 正常结束 | — | done |
| 客户端取消 | 否（结果不确定） | canceled |

### 6.5 验收脚本需覆盖的流式用例

- 正常流：role → content × N → finish_reason → [DONE]
- 工具流：role → tool_calls 分片 × N → finish_reason=tool_calls → [DONE]
- 上游 500 在首字节前：HTTP 非 200，错误体带 code 与 request_id
- 上游断流在首字节后：SSE error 事件 + 关闭，不拼接第二家
- 客户端 100ms 后断开：上游被取消（假上游断言收到断开）
- 慢客户端：读 1 字节停顿 5s，服务端不 OOM

---

## 7. 问题三：账号池 / 凭证隔离 / 额度账本的持久化方案

### 7.1 凭证隔离（先做，最敏感）

- 真实密钥只存在 **OS 凭据库**：Windows Credential Manager / macOS Keychain / Linux Secret Service（`keyring` 库）。
- SQLite 只存：`credential_ref`（keyring 的 service+username）、`fingerprint = sha256(secret)[:16]`、创建时间、最后验证时间。
- 运行时流程：`CredentialBroker.lease(account_id)` → 从 keyring 取出 → 构造只读 `CredentialLease`（`__repr__` 脱敏）→ 传给 adapter → `finally: lease.revoke()`（清引用，不保证 Python 内存安全，但保证不落盘/不日志）。
- 现有 `credentials.py` 的 `Binding` / `DisabledSystemVault` / `SyntheticVault` 是很好的骨架：把 `DisabledSystemVault` 替换为 `KeyringVault`，保留"默认拒绝"语义。
- 禁止：写 .env、写 DB、写日志、写崩溃转储、写浏览器 localStorage。

### 7.2 账号池

~~~sql
CREATE TABLE accounts (
  id            INTEGER PRIMARY KEY,
  provider      TEXT    NOT NULL,
  alias         TEXT    NOT NULL,           -- 用户可见名，如 "gemini-主号"
  credential_ref TEXT   NOT NULL,           -- keyring 引用，不是密钥
  fingerprint   TEXT    NOT NULL,
  tier          TEXT    NOT NULL DEFAULT 'real_declared',
  status        TEXT    NOT NULL DEFAULT 'pending', -- pending|active|suspended|exhausted|invalid
  created_at    REAL    NOT NULL,
  last_used_at  REAL,
  last_ok_at    REAL,
  cooldown_until REAL,
  notes         TEXT,
  UNIQUE(provider, alias)
);
CREATE TABLE provider_health (
  provider     TEXT PRIMARY KEY,
  fail_streak  INTEGER NOT NULL DEFAULT 0,
  window_start REAL,
  window_fails INTEGER NOT NULL DEFAULT 0,
  circuit_state TEXT NOT NULL DEFAULT 'closed', -- closed|open|half_open
  open_until   REAL
);
~~~

选择算法：过滤 `status=active AND (cooldown_until IS NULL OR < now)` → 按 `last_used_at` 升序（round-robin）→ 同 provider 并发不超过 `limits.max_concurrency`。

### 7.3 额度账本（append-only，双分录）

~~~sql
CREATE TABLE quota_ledger (
  id          INTEGER PRIMARY KEY,
  account_id  INTEGER NOT NULL REFERENCES accounts(id),
  provider    TEXT    NOT NULL,
  request_id  TEXT    NOT NULL,
  kind        TEXT    NOT NULL,   -- request_attempt|upstream_429|upstream_ok|platform_signal|manual_adjust
  delta_units REAL    NOT NULL,   -- 负数为消耗
  unit        TEXT    NOT NULL,   -- request|token|credit
  accuracy    TEXT    NOT NULL,   -- exact|estimated|unknown
  detail      TEXT,               -- 脱敏 JSON（不含提示词）
  created_at  REAL    NOT NULL
);
CREATE INDEX idx_ledger_account_time ON quota_ledger(account_id, created_at);
~~~

**判定规则（对应 I3）**：

- 本地只能产生 **estimate**：`accuracy=estimated|unknown`。
- 只有平台信号（429 的响应头/错误体、控制台导出、官方 usage 字段）才能把账号标记为 `exhausted`，且必须写一条 `platform_signal` 记录。
- UI 与 API 必须区分显示 "estimated 已用 62%" 与 "平台确认已耗尽"。

### 7.4 幂等键持久化

~~~sql
CREATE TABLE idempotency_keys (
  key           TEXT PRIMARY KEY,
  request_hash  TEXT NOT NULL,      -- HMAC-SHA256，不存正文
  status        TEXT NOT NULL,      -- reserved|streaming|done|failed|canceled
  response_json TEXT,               -- 仅非流式；流式不缓存结果
  created_at    REAL NOT NULL,
  expires_at    REAL NOT NULL
);
CREATE INDEX idx_idem_expiry ON idempotency_keys(expires_at);
~~~

- 非流式：成功后缓存响应（含失败结果），TTL 默认 5 分钟（可配置到 24h）。
- 流式：只缓存"是否已开始"，用于防止重复派发；不缓存流内容。
- 定期清理（每小时一次，或启动时）。

### 7.5 事件与审计

~~~sql
CREATE TABLE events (
  id         INTEGER PRIMARY KEY,
  request_id TEXT NOT NULL,
  at         REAL NOT NULL,
  level      TEXT NOT NULL,
  provider   TEXT,
  account_id INTEGER,
  outcome    TEXT,          -- success|fallback|blocked|error|refused|canceled
  latency_ms INTEGER,
  bytes_in   INTEGER,
  bytes_out  INTEGER,
  detail     TEXT           -- 脱敏 JSON，严格白名单字段
);
~~~

- **绝不落盘请求正文**；只存 SHA-256 hash、长度、消息条数。
- 默认保留 7 天，可配置；提供 `tokensfree export --format jsonl` 审计导出。

### 7.6 迁移与备份

- `schema_version` 表 + 顺序迁移脚本（`migrations/0001_init.sql` ...），启动时自动跑。
- SQLite 参数：`journal_mode=WAL`、`busy_timeout=5000`、`synchronous=NORMAL`。
- 备份：`VACUUM INTO` 到 `backups/`，每日一次，保留 7 份。

---

## 8. 问题四：并发模型从全局锁改为每请求并发控制

### 8.1 现状问题（精确）

`server.py:22` 定义 `self.lock = threading.RLock()`；`Gateway.dispatch`（:47）与 `Gateway.run`（:76）在持锁状态下执行**整个候选循环 + 适配器调用**（`MockAdapter(result).complete(...)`，:88）。因为 `ThreadingHTTPServer` 每请求一个线程，真实网络下所有请求会被这把锁串行化，且 `snapshot()`（:109）也会被长请求拖住。

### 8.2 目标模型

~~~text
每请求：RequestContext(request_id, deadline, cancel_event, client_id, budget)
    ↓
router.route():
    candidates = build_candidates(request)          # 纯函数，可单测
    candidates = [c for c in candidates if check_eligibility(c)]   # 无 IO
    for c in rank(candidates):
        await provider_sem[c.name].acquire()         # 每 provider 并发上限
        try:
            async with timeout(budget.remaining()):
                result = await c.adapter.complete(...)
        finally:
            provider_sem[c.name].release()
        if ok: return result
        if not retryable(err): raise
    raise NoFreeProvider
~~~

**规则**：

- 全局锁只在"写账本/写幂等表"这类极短临界区使用，且由 SQLite 事务承担；**不得覆盖任何 await**。
- 每 provider：`asyncio.Semaphore(max_concurrency)` + 令牌桶（RPM/TPM）。
- 每客户端：`client_id` 维度的并发上限与日请求上限（防止本机其他进程刷爆额度）。
- 超时预算：客户端 deadline（默认 60s）→ 单次尝试 = min(剩余预算, provider 上限) → connect/read/write 分别设上限。
- 取消：`asyncio.TaskGroup` 或 anyio cancel scope；`CancelledError` 必须继续抛出，不得吞。
- 熔断：provider 连续失败 ≥ 5 或 60s 内失败率 > 50% → open 30s → half_open 试探 1 个请求。
- 重试：仅 `retryable=True` 且"未向客户端发出任何字节"；总尝试次数 ≤ 2（与现有"每个候选一次"保持一致），跨 provider 最多 2 家。
- 幂等：非流式请求先写 `reserved`，成功后 `done`；崩溃恢复时 `reserved` 视为不确定，不自动重放。

### 8.3 迁移策略

1. 新代码放在 `gateway/` 包下（`edge/`, `router/`, `adapters/`, `store/`），旧 `server.py` 保留为兼容壳，逐步把 handler 指过去。
2. 每一步都保持 `check_dsh_contract.py` 与 119 条测试绿。
3. 并发改造完成后删除 `threading` 相关代码与 `ThreadingHTTPServer`。

---

## 9. 问题五：DSH 完整接入路径与验收标准

### 9.1 接入形态

**不要**修改 DSH 内置 DeepSeek provider；用**自定义 provider**（openai-completions 协议）指向 `http://127.0.0.1:8765/v1`。仓库 `docs/dsh-integration-gap.md` 已记录关键配置项：

- `supportsDeveloperRole: false`（或网关实现 developer→system 映射）
- `maxTokensField: max_tokens`
- 不要覆盖现有 `cordis.patch.yml` 的整个 entry

### 9.2 必须实现的能力（按 DSH 实际请求形状）

| 能力 | 现状 | 目标 |
|---|---|---|
| `stream: true` | 400 | SSE 完整实现 |
| `tools`（DSH 实测 23 个） | 400 | 能力校验 + 透传 + `tool_calls` 增量 |
| `developer` role | 400 | 映射为 system（可配置） |
| `usage` | 无 | 有则透传，无则估算并标注 accuracy |
| 多轮 tool 消息 | 拒绝 | 支持 `role:"tool"` + `tool_call_id` |
| 模型名 | 仅 `tokensfree-auto` | 支持 `tokensfree-auto<provider>/<model>` 路由语法（可选） |

> 注意：DSH 0.2.2-rc.1 实测请求带 23 个工具定义。若网关逐个硬编码工具名就是错误设计——工具应完全透传，网关只做能力位判断。

### 9.3 验收脚本（scripts/dsh_smoke.py）

1. 以 `--mock-upstream` 启动网关（上游用本地假 SSE 服务）。
2. 启动真实 DSH headless（固定版本 `@deepseek-ai/dsh@0.2.2-rc.1`，禁用安装脚本），临时 HOME/DSH_HOME，空工作目录。
3. 断言：
   - 网关收到 `stream:true` 且 `tools` 非空
   - 网关向假上游转发了 tools
   - 客户端收到 `tool_calls`，DSH 执行 `read`，结果回传
   - 最终 assistant 文本非空
   - 网关外发连接数 = 0（除假上游 127.0.0.1）
4. 退出码 0 表示"真闭环"，不是"预期失败"。现有 `experiments/dsh_offline_probe.py` 记录的是**预期失败**，不要混淆。

### 9.4 失败模式排查表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 400 INVALID_REQUEST | 缺 stream/tools 支持 | 先过 G2 |
| 流在第一帧就断 | 未发送 role delta 或 Content-Type 错 | 检查响应头与首帧 |
| tool call 不执行 | `tool_call_id` 不匹配 / `finish_reason` 缺失 | 对齐 OpenAI 语义 |
| 重复工具调用 | 网关重试了已发首字节的请求 | 幂等状态机 |
| 上下文被截断 | 无 usage / 未透传 `max_tokens` | 补 usage 与参数透传 |

---

## 10. 风险清单与缓解

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 平台 ToS / 封号 | 账号失效、法律风险 | 仅用户自有账号、官方 API、单账号默认、文档明示、一键关闭 |
| R2 | 免费额度不可验证/随时变化 | 意外计费 | Evidence TTL 24h、未知即拒、UI 标注 verified/declared、付费回退永久关闭 |
| R3 | 密钥泄漏 | 严重 | keyring + 全链路不落盘 + 日志白名单 + CI 密钥扫描 |
| R4 | SSE 注入/日志泄漏 | 隐私 | 不记正文、结构化日志、CSP 保持 |
| R5 | 并发竞态 | 账本错乱 | SQLite 事务 + 唯一约束 + 账本 append-only |
| R6 | Windows 兼容 | 无法交付 | CI matrix + 已暴露的路径测试 bug |
| R7 | 供应链 | 恶意依赖 | 锁版本 + 哈希 + 禁用 npm 安装脚本（沿用仓库做法） |
| R8 | 代码复杂度失控 | 维护崩溃 | 分层 + 单文件 < 300 行 + 纯函数策略层 |
| R9 | 免费模型能力弱 | 用户预期落差 | UI 明确标注"免费层可能限速/降级"，不承诺 SLA |

---

## 11. 推荐技术栈

| 层 | 选择 | 理由 | 备选 |
|---|---|---|---|
| 语言 | Python 3.11+（3.10 保底） | 现有代码即 Python；asyncio 成熟 | Go（性能好但重写成本高） |
| Web | FastAPI + uvicorn | SSE、校验、OpenAPI、测试友好 | aiohttp / Starlette |
| 上游 HTTP | httpx.AsyncClient | stream/timeout/limits 原生 | aiohttp.ClientSession |
| 数据 | SQLite（aiosqlite）+ 手写 SQL | 零运维、可审计 | DuckDB、Postgres（未来多用户） |
| 凭证 | keyring | 跨平台系统凭据库 | Windows DPAPI 直连（仅 Windows） |
| 配置 | pydantic-settings | 类型安全、启动校验 | dynaconf |
| 测试 | pytest + pytest-asyncio + respx | 异步与 HTTP mock 成熟 | unittest（保留兼容） |
| 质量 | ruff + mypy（新代码 strict）+ pre-commit | 快、低摩擦 | pylint |
| CI | GitHub Actions matrix | 免费、跨平台 | GitLab CI |
| 打包 | PyInstaller / shiv | 单文件分发 | WinSW（仅服务包装） |

**明确不推荐**：LiteLLM、LangChain、任何"自动重试+自动回退"的中间层。它们会绕过 fail-closed 闸门，且隐藏协议细节，与 I1/I2 冲突。

---

## 12. 问题六：最小可用版本（MVP）与后续迭代

### 12.1 MVP = P0 + P1(单家) + P2 的最小集合

**必做**

- [ ] CI 三平台绿 + LICENSE + pyproject
- [ ] 统一错误模型 + usage + request_id + 结构化日志
- [ ] 一家真实 provider 的 adapter（默认关闭，本地假上游全覆盖）
- [ ] SSE 流式（含错误、取消、心跳、[DONE]）
- [ ] tools 透传 + tool_calls 增量聚合
- [ ] developer role 映射
- [ ] 真实 DSH smoke 通过（一次 read 工具往返）
- [ ] SQLite 幂等键持久化（重启仍生效）
- [ ] 基础额度账本（estimate 级别即可）+ 账号表
- [ ] 凭证走 keyring，绝不落盘

**不做（放 P3+）**

- 多账号池复杂调度、平台余额自动同步
- 多协议（responses/messages/generateContent）
- Windows 服务安装包、托盘 UI
- GPU 调度、注册辅助、资源目录实时化
- 多用户/远程访问（永远不做，属产品边界）

### 12.2 后续迭代优先级

1. 第二家 provider（跨家冗余才有真实价值）
2. 平台侧余额/额度自动同步（需各平台 API，逐个评估）
3. 多协议端点（DSH 之外的客户端）
4. 资源目录实时化（把静态快照升级为"最后验证时间 + 来源"）
5. 可观测（/metrics + 简单面板）
6. 打包与服务化

### 12.3 MVP 验收指标（可量化）

| 指标 | 目标 |
|---|---|
| DSH 真实工具往返 | 3/3 次成功 |
| 默认配置外发连接 | 0（抓包/断网验证） |
| 20 并发流式 P95 首字节 | < 1.5s |
| 重启后幂等键命中 | 100% |
| 凭证泄漏扫描 | 日志/DB/仓库 0 命中 |
| 测试 | ≥ 200 条，三平台全绿 |

---

## 13. 给实施 AI 的执行约定

1. **每阶段一个 PR**，PR 描述必须包含"验收命令 + 实际输出"，不能只写"已实现"。
2. **不得删除既有拒绝分支**，只能新增；`providers.evaluate()` 的拒绝语义是资产。
3. 新增外部依赖必须在 PR 说明理由、许可证、版本锁定方式。
4. 不得修改 docs 中"未验证/未实现"的事实性声明，除非有实测证据并附命令输出。
5. 提交信息沿用现有 conventional commits 风格（`feat:`/`fix:`/`test:`/`docs:`）。
6. 任何密钥、token、路径不得写入仓库文件；测试用固定合成值。
7. 每完成一阶段，更新 `docs/production-roadmap.md` 的 checkbox 与本文件状态。
8. 若某决策与 I1/I2/I3 冲突，**停下来记录冲突**，不要自行放宽。

---

## 附录 A：目标文件结构

~~~text
tokensfree/
├── pyproject.toml
├── LICENSE
├── README.md
├── docs/
│   ├── production-roadmap.md      # 本文件
│   ├── real-provider-readiness.md # 已有，保留
│   └── ...
├── gateway/
│   ├── app.py              # FastAPI 应用装配
│   ├── config.py           # pydantic-settings，启动读一次
│   ├── edge/
│   │   ├── auth.py         # 本地 token + client_id
│   │   └── limits.py       # 体积/超时/配额
│   ├── router/
│   │   ├── candidates.py   # 纯函数：候选生成与排序
│   │   ├── admission.py    # 唯一闸门 check_eligibility/admit
│   │   └── execute.py      # 尝试/转移/取消/熔断
│   ├── adapters/
│   │   ├── base.py         # ChatAdapter 协议 + 错误归一化
│   │   ├── loopback.py     # 假上游（复用 protocol.py）
│   │   └── gemini.py       # 第一家真实 provider
│   ├── sse.py              # SSE 编解码与状态机
│   ├── store/
│   │   ├── db.py           # 连接/事务/WAL
│   │   ├── migrations/     # 0001_init.sql ...
│   │   └── repos.py        # accounts/ledger/idem/events
│   └── obs/
│       ├── log.py          # 结构化脱敏日志
│       └── metrics.py
├── client.py               # 保留，升级流式
├── server.py               # 兼容壳，逐步弃用
├── static/                 # 现有 UI，P5 再改
├── tests/                  # 新测试迁入
└── scripts/
    ├── dsh_smoke.py
    ├── loadtest.py
    └── export_audit.py
~~~

## 附录 B：错误码表（对外稳定契约）

| code | HTTP | retryable | 含义 |
|---|---|---|---|
| invalid_request | 400 | 否 | 请求形状非法 |
| unsupported_capability | 400 | 否 | 请求了未实现能力（stream/tools/多模态） |
| unauthorized | 401 | 否 | 本地 token 无效 |
| origin_rejected | 403 | 否 | Host/Origin 非法 |
| model_not_found | 404 | 否 | 模型名不可路由 |
| policy_blocked | 403 | 否 | 准入被拒（配置/授权/证据） |
| free_status_unverified | 403 | 否 | 免费资格未验证 |
| no_free_provider | 503 | 是（稍后） | 所有候选不可用 |
| upstream_rate_limited | 429 | 是 | 上游限流（带 Retry-After） |
| upstream_auth_failed | 502 | 否 | 上游凭证失效 |
| upstream_error | 502 | 是 | 上游 5xx |
| upstream_timeout | 504 | 是 | 上游超时 |
| upstream_disconnected | 502 | 否（已发流） | 流中断 |
| refusal | 422 | 否 | 上游安全拒绝（不切换） |
| canceled | 499 | 否 | 客户端取消 |
| internal_error | 500 | 是 | 未预期错误 |

## 附录 C：本方案与原始任务的映射

| 原始问题 | 对应章节 |
|---|---|
| 1. Mock → 真实 adapter 且不破坏 fail-closed | §5 |
| 2. SSE 与工具调用协议 | §6 |
| 3. 账号池/凭证隔离/额度账本持久化 | §7 |
| 4. 全局锁 → 每请求并发控制 | §8 |
| 5. DSH 接入路径与验收 | §9 |
| 6. MVP 范围与后续迭代 | §12 |
| 架构决策 / 接口草图 / 风险 / 技术栈 | §3、§5.2、§10、§11 |
