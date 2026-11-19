# tokensfree 0.6 · 本地路由实验台

**默认是安全的本地 mock 原型：没有外部模型请求，不需要任何账号或密钥。真实免费供应商通道已实现但默认关闭，必须同时满足三个条件才可能开启（见下文 P4）。未公开发布。**

## 启动
需要 Python 3.10 或更新版本，无第三方 Python 依赖。

Windows PowerShell，在解压目录运行：

```powershell
py -3 server.py
```

macOS / Linux：

```sh
python3 server.py
```

打开 `http://127.0.0.1:8765`。用 Ctrl+C 停止服务。端口被占用时加 `--port 8766` 并打开对应地址。不要双击 HTML，控制台依赖本地服务。

## 可以体验什么
- 极客风控制台：深色、网格、终端事件流。
- 十种模拟场景：正常、额度耗尽切换、全部耗尽停止、限流冷却、凭证失效、拒绝停止、服务故障。
- 每个请求最多尝试两个模拟供应商，每个候选一次，无付费回退。
- 限流 30 秒、故障 10 秒的模拟冷却；耗尽或凭证失效禁用至手动重置。重置场景可重新测试。
- `GET /v1/models` 与非流式 `POST /v1/chat/completions` 的**最小类 OpenAI 格式**，仅 `tokensfree-auto`、文本；不是完整 OpenAI 兼容实现。
- 资源目录沿用上一版的 10 项资源机会和本地申请记录，不是实时余额。

## 本地 API 示例
只为集成开发测试，不能处理真实模型任务。启动服务后在同一设备运行：

```python
import json, urllib.request
base = 'http://127.0.0.1:8765'
token = json.load(urllib.request.urlopen(base + '/api/session'))['token']
payload = {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'test'}]}
req = urllib.request.Request(base + '/v1/chat/completions', data=json.dumps(payload).encode(),
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
print(json.load(urllib.request.urlopen(req)))
```

本地临时访问 token 不是供应商 API Key，不应复制到公开日志；重启会轮换。该原型不能防御已经能够在本机运行程序的恶意软件。

## 安全与状态
- 仅绑定 `127.0.0.1`，不提供远程访问模式。拒绝非本机 Host 和跨源 Origin；POST 校验本地会话 token；无 CORS 放行。
- 不保存/输出请求正文，不收集真实凭证。事件仅存内存并限制为 100 条；服务重启清空。
- 浏览器展示使用文本节点，资源跳转由用户在外部官网操作。资源目录的申请记录仍在浏览器 localStorage，与路由状态不同。
- 不能用于生产或直接暴露到公网。Python 标准 HTTP 服务不是经过加固的生产服务器。

## 已实现 / 未实现（截至 2026-11-18）

新内核已接入 HTTP 边缘并可作为独立服务运行（`python -m tokensfree`，零第三方运行时依赖）：真实供应商适配器、DSH 端到端冒烟、多额度池（多 key 轮换 / 账号状态 / 配额账本 / 运维 CLI）均已实现。仍待完成（可选）：跨平台系统凭据库（Windows Credential Manager 已实现）、Windows 安装包与后台常驻、GPU 调度。真实供应商联调已于 2026-11-19 用智谱 glm-4.7-flash 验证通过。旧 `python server.py` 保留为冻结的 mock 参考实现。

**新内核（`tokensfree/`，零运行时依赖）**

| 模块 | 作用 |
|---|---|
| `config.py` | 不可变启动配置，只从显式映射读取 |
| `errors.py` | 稳定错误码契约（客户端唯一可见的错误形状） |
| `obs.py` | 白名单结构化日志 + Bearer 脱敏 |
| `adapters/base.py` | 适配器契约、上游错误归一化、不可伪造的 `Admission` |
| `adapters/loopback.py` / `transport.py` / `openai_compat.py` | 本地假上游的异步适配器与 stdlib SSE 传输 |
| `adapters/stream_parse.py` | 上游 SSE 严格解析（畸形帧必须失败，不静默丢帧） |
| `router/candidates.py` / `admission.py` / `execute.py` / `scheduling.py` / `account_pool.py` / `health.py` | 纯函数候选、唯一准入闸门、有界尝试（首字节后不换供应商） |
| `sse.py` | 客户端 SSE 帧编码 + 工具调用增量装配 |
| `edge/protocol.py` / `app_core.py` / `stream_core.py` | 框架无关的请求解析、应用核心、流式核心 |
| `runtime.py` | 把配置与适配器装配成可运行的服务依赖 |
| `store/` | SQLite 迁移、幂等键、只增账本、脱敏事件（已接入请求链路） |

### 新后端（可运行，mock 运行时）

新内核已经是一个**可启动的服务**，与旧 `server.py` 并存：

```sh
# Windows PowerShell
$env:TOKENSFREE_LOCAL_TOKEN = "<32–128 位 [A-Za-z0-9_-] 的稳定令牌>"
.venv\Scripts\python -m tokensfree.edge.host --port 8766
```

**自定义接入通道**：桌面面板"接入通道"页或 `providers.json` 可添加任意 OpenAI 兼容 endpoint（本机 Ollama / LM Studio / vLLM、远端 HTTPS），按 `free_verified` / `zero_cost_local` / `user_paid`（显式开关）策略安全接入；密钥只存 Windows 凭据管理器。

Windows 用户还可以用**桌面外壳**：`python -m tokensfree.desktop`（tkinter 控制面板 + 托盘 + 开机启动，安装用 `install\install.ps1`）。

启动后浏览器打开 `http://127.0.0.1:8766/` 就是**网页控制台**（模型选择、流式对话、运行时状态、连接信息复制）。

端点：`GET /v1/models`、`POST /v1/chat/completions`（非流式）、`GET /api/status`、`GET /api/session`（浏览器控制台用本地令牌）。Host/Origin/令牌校验与旧实现共用同一套函数。
mock 运行时没有流式适配器，因此 `stream:true` 会得到明确的 `400 unsupported_capability`（不静默降级）。
要演示完整流式链路，先起一个本地假 SSE 上游，再启动后端：

```sh
.venv\Scripts\python -m tokensfree.edge.host --port 8766 --loopback <上游端口>
```

流式响应使用 `Transfer-Encoding: chunked`，帧序列为 role → content → finish → `[DONE]`。
真实供应商的启动方式、环境变量与多 key 配置见 [操作手册](docs/operator-guide.md) 的「启用真实供应商」。
D1 已按零框架 stdlib host 落地；同一个 ASGI app 仍可换由 uvicorn/hypercorn 托管，aiohttp 后端只是另一个 `Backend` 实现。

### 真实 DSH 冒烟（合成上游）

`scripts/dsh_smoke.py` 用**真实打包的 DSH headless 客户端**打新后端：合成上游第一轮返回一次 `read` 工具调用，第二轮返回固定最终文本。

```sh
.venv\Scripts\python scripts/dsh_smoke.py <node.exe> <dsh/lib/bin.js>
```

已验证：DSH 首轮请求带 `stream:true` 与 23 个工具定义、网关转发成功、工具调用与结果各一次、最终文本匹配、DSH 退出码 0。
注意：打包版 DSH 在 `app.asar` 内，普通 node/Python 读不到；需先解包到真实目录，或用 Electron 的 node 运行（脚本会自动选择）。

> 新内核的验证：`python -m pytest -q` 共 630 项通过 / 2 项 opt-in 跳过（含真实本地 HTTP 假上游 → 传输 → 解析 → 流式核心 → 客户端帧的端到端用例）。

## 验证

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q          # 630 passed / 2 skipped（skip 为 opt-in 真实 DSH / 真实供应商用例）
python check_dsh_contract.py # 8 组拒绝行为矩阵，退出 0 表示符合预期
```

推荐在虚拟环境中安装开发依赖：`python -m venv .venv`，再 `.venv\Scripts\pip install -r requirements-dev.txt`（macOS/Linux 用 `.venv/bin/pip`）。之后的命令用 `.venv\Scripts\python` 代替 `python`。

一条命令验收 P0：`python scripts/verify_p0.py`（lint → 测试 → 契约矩阵 → 导入出口门禁 → 用户旅程 → 密钥扫描）。

启动方式、令牌配置、故障排查与已知限制见 [操作手册](docs/operator-guide.md)；旧实现与新内核的差异见 [对照表](docs/legacy-parity.md)；版本演进见 [CHANGELOG](CHANGELOG.md)。

| 阶段 | 状态 |
|---|---|
| P0 工程基线：CI（三平台 × 3 个 Python 版本，跑完整 `verify_p0` 门禁）、统一错误契约、结构化日志、契约矩阵可注入 | ✅ 完成 |
| P1 内核：配置 / 适配器契约 / 候选 / 准入闸门 / loopback 适配器 / OpenAI 兼容适配器（默认禁用） | ✅ 完成 |
| P1 6–7：HTTP 边缘（ASGI app + stdlib host，零框架依赖）+ 双实现契约对照 | ✅ 完成（D1 以 stdlib host 落地；ASGI app 可换 uvicorn/hypercorn） |
| P2 协议：SSE 编码与解析 / 工具增量装配 / 流式核心 / 上游传输 | ✅ 完成 |
| P2 真实 DSH headless 端到端（合成上游，工具往返） | ✅ 已验证 |
| P3 持久化：迁移 / 幂等 / 只增账本 / 事件 / 账号 / 熔断状态（`--db` 可跨重启） | ✅ 完成 |
| P3+ 多额度池：多 key / key 文件 / 轮转 / 熔断隔离 / 账号生命周期 / 配额账本 / 运维 CLI | ✅ 完成（等真实多账号联调） |
| P4 真实调用闸门（开关 + 授权 + 免费证据三条件）+ 环境变量装配（`--real`） | ✅ 默认关闭；已用智谱 glm-4.7-flash 实测通过（2026-11-19） |
| 真实供应商装配与验收脚本（`--real` / `real_probe.py` / `real_smoke.py` / 证据快照，默认关闭） | ✅ 已用智谱 glm-4.7-flash 验证 |
| 真实模型联调（智谱 glm-4.7-flash：探针 / 网关 / 真实 DSH） | ✅ 2026-11-19 验证通过 |
| 系统凭据库 | ✅ Windows Credential Manager 已实现并验证；跨平台 keyring ⛔ |

**当前验证状态（2026-11-18）**

- `scripts/verify_p0.py` → P0 OK（ruff / pytest / 契约矩阵 / 导入出口门禁 / **真实进程用户旅程** / 密钥扫描）
- 干净克隆 → venv → 安装依赖 → `verify_p0.py` → P0 OK（可复现；依赖安装需能访问 PyPI）
- 真实 DSH 0.2.2-rc.1 headless + 合成上游：流式、23 个工具透传、`read` 工具往返、最终文本、退出码 0 全部通过（`scripts/dsh_smoke.py`）
- P0–P4 内核、多额度池（多 key / key 文件 / 轮转 / 账号生命周期）、真实调用闸门、真实供应商装配与审计修复共 630 项测试通过（2 项 opt-in 用例默认跳过）
- 用户旅程脚本（真实起服务、幂等、跨重启重放）已作为 `verify_p0` 的一部分
- 真实供应商验收：`scripts/real_smoke.py --fixture` 与 `scripts/real_probe.py --fixture --stream --tools` 自检通过（本地假上游，无需 key、不联网）
- **真实供应商联调（2026-11-19，智谱 `glm-4.7-flash`，key 存于 Windows 凭据管理器）**：`real_probe.py --stream --tools` → `verified: true`（非流式 / 流式 / 工具调用均通过）；`real_smoke.py` 真实模式 → `verified: true`（真实网关 + 打包 DSH headless，退出码 0、最终文本非空）。免费额度高峰期会返回 1305（可重试），探针内置退避重试后成功

设计见 [docs/superpowers/specs/](docs/superpowers/specs/)，计划见 [docs/superpowers/plans/](docs/superpowers/plans/)（P0、P1、P2）。JS 通过 `node --check static/app.js`。旧 `server.py` 是冻结的 mock 参考实现；新内核通过 `tokensfree.edge.host` 或任意 ASGI 服务器提供 HTTP 服务，两者用契约测试对照。

## 项目结构
`server.py` 冻结的 mock 参考服务；`tokensfree/` 新内核（配置 / 适配器 / 路由 / 持久化 / 边缘）；`scripts/` 验收与 DSH 冒烟；`test_gateway.py` 旧实现自动测试；`static/` 极客控制台；`static/catalog/` 资源目录。运行状态不会写入源码。`package.json` 仅供开发预览，无 npm 依赖。

许可证：Apache-2.0（见仓库根目录 `LICENSE`）。本项目为本地原型，仍不应声称已具备生产可用性。


## 0.2.1：重复请求保护
`POST /v1/chat/completions` 可带 `Idempotency-Key`（1–128 位英文字母、数字、`.`、`_`、`:` 或 `-`）。同一键、同一内容在缓存有效期内返回原结果，不再次尝试供应商；同一键不同内容返回 409。失败结果也缓存，避免不确定重试消耗额度。

缓存只在当前进程内保留 5 分钟、最多 100 个键。容量满时拒绝新键请求，不执行供应商；重启或超时后缓存失效；重置测试场景不会清除它。新任务使用新键，重试同一任务保留原键。不承诺跨重启或真实上游的 exactly-once。缓存存请求 HMAC 而非明文提示词；目前响应仅为模拟固定文本。网页按钮目前每次点击仍是新请求，此能力供 API 客户端接入。

真实平台接入的安全与验收要求见 [接入门槛](docs/real-provider-readiness.md)。


## 0.3：供应商契约与严格策略
新增 `providers.py`，把供应商规格、调用适配器和调用前准入检查分开。0.3 阶段只有 MockAdapter；0.4 新增隔离的离线 HTTP 协议模块，仍没有真实凭据入口。

调用前必须通过：启用、授权名单、能力匹配、模拟免费证据有效期。未知、收费、过期均不能进入适配器；真实供应商即使配置成免费也被硬阻止。控制台增加“资格未知”“全部未知”“证据过期”三种演示场景。这里的证据是生成的模拟 fixture，不能代表任何真实账号的免费资格。

API 可选 `allowed_providers`，只能缩小到已配置的 mock-alpha / mock-beta 范围，例如 `["mock-alpha"]` 将禁止回退到 beta。它不授权任何真实平台。新增 17 项策略和集成测试，验证被拒绝的供应商不会触发适配器。全部测试均无需外部网络、账户或付费。未配置 CI。

本轮云端 Chrome 已验证全部免费资格未知时显示策略阻止并停止。独立审查发现的非法时钟边界已添加校验和回归测试。

## 0.4：离线协议与客户端闭环
新增最小本地 Python 客户端、服务端授权上限与离线 HTTP 协议验证。`python client.py` 可连接已启动的本地模拟网关；不自动重试，不调用真实平台。阶段能力、使用示例和真实接入门槛见 [阶段验收](docs/offline-protocol-stage.md)。

本阶段本地测试：66 项通过；协议 fixture 与客户端均有 loopback HTTP 测试，JS 语法检查通过。独立审查发现的无效请求键、超时后重试键可恢复性及并发键覆盖已修复并回归测试。

## 0.5：供应商配置预检
新增 Gemini Developer API 离线配置声明预检。`python preflight.py examples/gemini-preflight.json` 输出明确阻塞项；永远不启用真实调用，不读取账号或密钥。输入声明全部满足也不能证明免费资格。详见 [预检说明与正式接入门槛](docs/gemini-preflight.md)。

## 0.6：凭据隔离契约
新增默认拒绝的凭据 broker 与固定合成值测试后端，验证服务器侧供应商/端点/模型/时效绑定；不收集真实密钥。Windows 与跨平台系统库方案及安全边界见 [凭据边界](docs/credential-boundary.md)。系统库尚未接入、真实调用继续关闭。

## DSH 接入审计
`python check_dsh_contract.py` 可重复运行 8 项本地 HTTP 协议边界探测。当前不是 DSH 完整后端：SSE、工具循环与其他三类 API 尚未实现。参见 [差距与实验报告](docs/dsh-integration-gap.md)。该探测本身不启动 DSH，不读取用户配置或调用真实供应商。

## DSH 实验性 SSE + 工具闭环
新增独立 `agent_fixture.py` 合成模型端点与 `experiments/dsh_tool_roundtrip.py`。固定官方 DSH 已在无外网实验中完成一次 `read` 工具调用、结果回传和最终回复。默认网关不变，尚不支持真实供应商的流式/工具路由，不能用于实际开发任务。复现、安全边界与验收见 [实验说明](docs/dsh-sse-stage.md)。

## 本地网关合成 Agent 模式
`python server.py --agent-fixture` 可在现有端点启用 `tokensfree-fixture` 合成 SSE/read 工具模型。默认启动及 UI 不变；这不是实际模型或免费 API 路由。一次进程只提供一次受限实验会话，普通场景重置不会重新派发工具。详见 [启动与安全边界](docs/gateway-agent-mode.md)。

## 本地客户端传输加固
本地 Python 客户端增加每次 HTTP 交易的完整期限、严格响应格式与会话令牌检查。错误正文不回显，仍不自动重试。使用及限制见 [客户端安全说明](docs/local-client-safety.md)。
