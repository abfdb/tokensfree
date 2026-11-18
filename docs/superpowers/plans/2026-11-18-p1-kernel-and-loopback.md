# P1 内核与 loopback 适配器 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不引入任何真实供应商、不新增第三方依赖的前提下，先把新内核的"决策层"建好：配置、适配器契约、错误归一化、候选生成、准入闸门、loopback 适配器。这样 D1（HTTP 框架）确定后，剩下的只是把 HTTP 边缘接上去。

**Architecture:** 纯 Python + stdlib（asyncio 可用）。所有网络与框架依赖被推迟到 Task 6（标记 BLOCKED，等 D1）。新代码全部位于 `tokensfree/` 包内，旧 `server.py` 不动，两者通过同一套契约测试对照（P2 完成）。

**Tech Stack:** Python 3.10+，stdlib only（`asyncio`, `http.client`, `dataclasses`），pytest 为开发依赖。

**Spec:** `docs/superpowers/specs/2026-11-18-tokensfree-productionization-design.md`

## Global Constraints

- 零运行时依赖：本计划不 import 任何第三方包；`aiohttp` 只在 Task 6 引入（BLOCKED）。
- 默认拒绝：`enable_real=False` 时任何真实供应商都不可达。
- 适配器不得自行重试或回退；重试只属于 router。
- 错误只用 `tokensfree/errors.py` 的 `ErrorCode`；归一化后的 `NormalizedError.code` 另有一套上游语义码，二者不混用。
- 每个任务一个 commit；TDD：先看见失败。

## Review Focus

1. **配置来源被请求污染**：`load_config` 只接受显式映射，绝不读 `os.environ` 隐式全局；请求处理中不得再读环境。（Task 1）
2. **429 语义误判**：`Retry-After` 超过 60s 的 429 不是"限流稍后重试"，而是额度耗尽信号，不得进入重试路径。（Task 2）
3. **准入被绕过**：任何适配器调用都必须拿 `Admission`；无 `Admission` 的调用在类型与方法签名上写不出来。（Task 4）
4. **候选顺序不稳定**：候选排序必须确定（按配置顺序），否则同一请求在不同时刻走不同供应商，账本无法复现。（Task 3）
5. **loopback 泄漏**：本地假上游只允许连接字面 `127.0.0.1`，禁止重定向、代理、任意 URL。（Task 5）

---

## 文件结构

~~~text
tokensfree/
├── config.py                 # 新建：不可变 AppConfig + load_config(env)
├── adapters/
│   ├── __init__.py
│   ├── base.py               # 新建：ChatRequest/ChatResult/NormalizedError/classify_status/Admission
│   └── loopback.py           # 新建：async 适配器，包装 protocol.LoopbackFixtureAdapter
├── router/
│   ├── __init__.py
│   ├── candidates.py         # 新建：纯函数候选生成与排序
│   └── admission.py          # 新建：唯一闸门 admit()
tests/
├── test_config.py
├── test_adapter_base.py
├── test_candidates.py
└── test_admission.py
~~~


### Task 1: 不可变配置（tokensfree/config.py）

**Files:** Create `tokensfree/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `AppConfig`（frozen dataclass）、`load_config(env: Mapping[str,str]) -> AppConfig`
- 字段：`port:int=8765`、`local_token:str|None=None`、`enable_real:bool=False`、`max_body_bytes:int=262144`、`default_timeout_s:float=60.0`

- [ ] Step 1 写失败测试：默认值；`TOKENSFREE_ENABLE_REAL` 只接受 `0/1`；端口越界抛 `ValueError`；`TOKENSFREE_LOCAL_TOKEN` 合法性（32–128 位 `[A-Za-z0-9_-]`）；**函数不接受 `None` 以外的环境来源**（调用方必须显式传入映射）。
- [ ] Step 2 `python -m pytest tests/test_config.py -q` → 失败（ModuleNotFound）
- [ ] Step 3 实现 `load_config`（纯函数，不触碰 `os.environ`）
- [ ] Step 4 测试通过 + 全量回归
- [ ] Step 5 commit `feat: add immutable startup config`


### Task 2: 适配器契约与上游错误归一化（tokensfree/adapters/base.py）

**Files:** Create `tokensfree/adapters/__init__.py`, `tokensfree/adapters/base.py`, `tests/test_adapter_base.py`

**Interfaces:**
- Produces: `ChatRequest`(frozen)、`ChatResult`(frozen)、`NormalizedError`、`classify_status(status:int, retry_after_s:float|None=None) -> NormalizedError|None`、`Admission`(frozen, 仅 `admission.py` 可构造)、`ChatAdapter(Protocol)`
- 归一化表：401/403→auth_invalid(不可重试)；402→billing_required；3xx→redirect_rejected；5xx→upstream_error(可重试)；429 且 Retry-After≤60→rate_limited(可重试)；429 且 >60 或缺头→quota_exhausted(不可重试)；其余 4xx→invalid_request_upstream

- [ ] Step 1 写失败测试：表驱动覆盖全部状态码；`Admission` 直接构造必须失败；`NormalizedError` 的 `str()` 只含 code 不含上游正文。
- [ ] Step 2 跑测试看失败
- [ ] Step 3 实现（`Admission` 用模块私有哨兵对象守住构造函数）
- [ ] Step 4 通过 + 全量回归
- [ ] Step 5 commit


### Task 3: 候选生成（tokensfree/router/candidates.py）

**Files:** Create `tokensfree/router/__init__.py`, `tokensfree/router/candidates.py`, `tests/test_candidates.py`

**Interfaces:**
- Produces: `build_candidates(specs, *, allowed: frozenset, required: frozenset) -> tuple[ProviderSpec, ...]`
- 规则：保持配置顺序（稳定）；过滤 `allowed` 之外的；过滤能力不足的；**不过滤免费资格**（那是 admission 的事）

- [ ] Step 1 失败测试：顺序稳定、幂等（重复调用同结果）、过滤 allowed、过滤 capability
- [ ] Step 2 跑失败 → Step 3 实现 → Step 4 通过
- [ ] Step 5 commit


### Task 4: 唯一准入闸门（tokensfree/router/admission.py）

**Files:** Create `tokensfree/router/admission.py`, `tests/test_admission.py`

**Interfaces:**
- Consumes: `providers.evaluate`、`AppConfig`、`Admission`
- Produces: `admit(spec, config, authorized, now, required=frozenset({'text'})) -> Admission`；不允许时抛 `AdmissionDenied(reason)`
- 规则：先 `evaluate`（免费证据/授权/能力/时钟），再 `config.enable_real` 二次确认；两条任一不过 → 拒绝

- [ ] Step 1 失败测试：mock 通过；未知免费证据拒绝；`simulation=False` 即使 `enable_real=True` 仍拒绝（P1 阶段尚无真实适配器）；`enable_real=False` 时真实供应商被拒且理由稳定
- [ ] Step 2 跑失败 → Step 3 实现 → Step 4 通过
- [ ] Step 5 commit


### Task 5: loopback 异步适配器（tokensfree/adapters/loopback.py）

**Files:** Create `tokensfree/adapters/loopback.py`, `tests/test_adapter_loopback.py`

**Interfaces:**
- Consumes: `protocol.LoopbackFixtureAdapter`、`ChatRequest/Result`、`classify_status`
- Produces: `LoopbackAdapter(port:int, timeout:float=2.0)`，`async def complete(request, admission) -> ChatResult`

- [ ] Step 1 失败测试：起一个 stdlib 假上游（`http.server`），断言成功路径映射、429 映射为 `rate_limited`、非 127.0.0.1 端口参数被拒绝
- [ ] Step 2 跑失败 → Step 3 实现（`asyncio.to_thread` 包装同步协议层；`admission` 必须传入）→ Step 4 通过
- [ ] Step 5 commit


### Task 6: HTTP 边缘（BLOCKED — 等 D1 确认）

依赖选择（aiohttp / FastAPI / 其他）确认后，才能写 `tokensfree/edge/app.py` 与契约对照夹具。在 D1 确认前不动。

### Task 7: 双实现契约对照（BLOCKED — 跟随 Task 6）

把 `tests/test_contract_matrix.py` 参数化为 `backend_factory`，同时跑旧 `server.make_server` 与新 `edge.app`，逐条断言拒绝行为与上游尝试计数一致。

---

## 自检

- Spec 覆盖：§7.1 模块边界（config/adapters/router）、§7.3 fail-closed 机制（Admission + 唯一闸门）、§7.2 依赖隔离（Task 6 之前零依赖）均有任务。
- 步骤可执行：每步一条命令或一个签名；BLOCKED 任务明确不写代码。
- Review Focus 五条分别绑定 Task 1/2/4/3/5。
