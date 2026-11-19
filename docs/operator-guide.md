# tokensfree 操作手册（一页纸）

> 适用版本：仓库 HEAD（P0–P4 已落地；真实供应商已用智谱 `glm-4.7-flash` 完成联调）。本文只写**现在能运行什么、怎么验收、出错怎么查**。

## 1. 环境

- Python 3.10+（当前验证过 3.10.8）
- 零运行时第三方依赖；开发依赖仅 pytest / ruff（`requirements-dev.txt`）
- 仅绑定 `127.0.0.1`，不提供远程访问

~~~sh
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt      # macOS/Linux: .venv/bin/pip
~~~

## 2. 三个可运行入口

### 2.1 旧 mock 网关（UI，行为冻结）

~~~sh
.venv\Scripts\python server.py
# 打开 http://127.0.0.1:8765
~~~

只有 mock 供应商；流式/工具请求会被明确拒绝。用于查看历史行为与 UI。

### 2.2 新后端（mock 运行时，无流式）

~~~powershell
$env:TOKENSFREE_LOCAL_TOKEN = "<32–128 位 [A-Za-z0-9_-] 的稳定令牌>"
.venv\Scripts\python -m tokensfree --port 8766          # 等价于 python -m tokensfree.edge.host
# 默认使用进程内 SQLite（:memory:）；要跨重启保留幂等与事件，指定文件：
.venv\Scripts\python -m tokensfree --port 8766 --db .\tokensfree.db
~~~

### Windows 桌面外壳

~~~powershell
.venv\Scripts\python -m tokensfree.desktop            # 控制面板（托盘 + 开机启动可勾选）
.venv\Scripts\python -m tokensfree.desktop --self-check  # 自检：起网关→校验→构造窗口→退出
powershell -ExecutionPolicy Bypass -File install\install.ps1            # 安装快捷方式（用户级，无管理员）
powershell -ExecutionPolicy Bypass -File install\install.ps1 -Autostart # 同时开机启动
powershell -ExecutionPolicy Bypass -File install\uninstall.ps1 -RemoveData
~~~

桌面面板：模式/端口/数据库设置、启动/停止网关、打开网页控制台、providers/账号/熔断状态、日志尾部；关闭窗口默认最小化到系统托盘（托盘菜单：显示面板 / 打开控制台 / 启停网关 / 退出）。配置在 `%APPDATA%\tokensfree\desktop.json`，只保存本地网关令牌，不保存供应商 key。

浏览器打开 `http://127.0.0.1:8766/` 使用**网页控制台**：选择模型、流式对话、查看 providers/账号/熔断状态、复制 Base URL 与本地令牌。

端点：`GET /v1/models`、`POST /v1/chat/completions`（非流式）、`GET /api/status`、`GET /api/session`（控制台获取本地令牌；仅 loopback + Host 校验，等价于旧控制台的 session 端点）。
`stream:true` 会得到 `400 unsupported_capability`——mock 运行时没有流式适配器，这是**有意的 fail-closed**。

### 2.3 新后端（loopback 流式运行时）

先起一个本地假 SSE 上游（任意实现），再：

~~~sh
.venv\Scripts\python -m tokensfree --port 8766 --loopback <上游端口>
~~~

流式响应使用 `Transfer-Encoding: chunked`，帧序列 role → content → finish → `[DONE]`。

### 2.4 真实供应商（默认关闭，已验证可用）

真实调用必须**三个条件同时成立**：`TOKENSFREE_ENABLE_REAL=1`、provider 在授权名单、`cost_evidence="free_verified"` 且证据未过期。默认不满足，所以默认零外发。

~~~powershell
# key 来源任选其一（五选一，互斥）
$env:TOKENSFREE_REAL_API_KEY_STORE = "zhipu"                 # Windows 凭据管理器（推荐）
# $env:TOKENSFREE_REAL_API_KEY_FILE = "D:\secrets\zhipu.key"
# $env:TOKENSFREE_REAL_API_KEYS = "key1,key2"                # 多 key 轮转
# $env:TOKENSFREE_REAL_API_KEY = "..."                       # 环境变量（最省事，会出现在进程环境）

$env:TOKENSFREE_ENABLE_REAL = "1"
$env:TOKENSFREE_REAL_PROVIDER = "zhipu"
$env:TOKENSFREE_REAL_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
$env:TOKENSFREE_REAL_MODEL = "glm-4.7-flash"
$env:TOKENSFREE_REAL_EVIDENCE_SNAPSHOT = "D:\IFan\tokensfree\.superpowers\evidence\zhipu-1795001938.json"

.venv\Scripts\python -m tokensfree --check --real          # 先自检（不联网、不占端口）
.venv\Scripts\python scripts/real_probe.py --stream --tools # 真实探针（非流式/流式/工具）
.venv\Scripts\python -m tokensfree --real --port 8767 --db .\tokensfree.db
.venv\Scripts\python scripts/real_smoke.py                  # 真实网关 + 打包 DSH 验收
~~~

**2026-11-19 实测结果（智谱 glm-4.7-flash）**：

| 验收 | 结果 |
|---|---|
| `real_probe.py --stream --tools` | `verified: true`（非流式 / 流式 / 工具调用 `read` 均通过） |
| `real_smoke.py`（真实网关 + DSH headless） | `verified: true`（网关启动、真实模式上报、DSH 退出码 0、最终文本非空） |

实测注意：`glm-4.7-flash` 是**推理模型**，16 token 预算会被 `reasoning_content` 用尽导致正文为空；探针默认改为 512 token。高峰期常返回 429 `{error:{code:"1305"}}`，归为可重试限流，探针内置退避重试（5–25s）后成功。

## 3. 模型选择语义

- `model: "tokensfree-auto"`：由路由器按配置顺序选择（默认行为）
- `model: "<具体模型 id>"`：只使用 `/v1/models` 中该 id 对应的供应商；同 id 多供应商时按候选顺序回退
- 未知模型 id：`404 model_not_found`（不会静默路由到别的模型）

## 4. 令牌与环境变量

| 变量 | 作用 |
|---|---|
| `TOKENSFREE_LOCAL_TOKEN` | 新后端的稳定本地令牌（DSH 通过 `apiKeyEnv` 引用它） |
| `TOKENSFREE_ENABLE_REAL` | 真实调用开关，默认 `0`（真实适配器已实现，三条件闸门见 2.4） |
| `TOKENSFREE_REAL_PROVIDER` / `_BASE_URL` / `_MODEL` | 真实供应商身份（必须显式配置） |
| `TOKENSFREE_REAL_API_KEY` / `_API_KEYS` / `_API_KEY_FILE` / `_API_KEYS_FILE` / `_API_KEY_STORE` | 五个互斥的 key 来源：内联 / 多 key / 单行文件 / 每行一个的文件 / Windows 凭据管理器 |
| `TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT` / `_EVIDENCE_SNAPSHOT` | 免费证据：Unix 秒或 `evidence_snapshot.py` 生成的快照（校验 model/provider 绑定） |
| `TOKENSFREE_PORT` / `TOKENSFREE_MAX_BODY_BYTES` / `TOKENSFREE_TIMEOUT_S` | 启动参数（由 `load_config` 校验） |

启动前可自检：`.venv\Scripts\python -m tokensfree --check [--real]`——只校验配置（key 来源、证据、db），输出 JSON，**不绑定端口、不发起请求**。

### 系统凭据库（Windows）

~~~powershell
.venv\Scripts\python scripts/credential_store.py set --provider zhipu     # 隐藏输入
.venv\Scripts\python scripts/credential_store.py status --provider zhipu
.venv\Scripts\python scripts/credential_store.py clear --provider zhipu
~~~

命名空间 `tokensfree/<provider>/<alias>`，key 不落文件/环境变量；仅 Windows 实现，其他平台明确 fail-closed（不回退明文）。系统凭据库保护静态存储，但不隔离同一登录用户下的其他进程。

### 免费证据刷新

~~~sh
.venv\Scripts\python scripts/evidence_snapshot.py --provider zhipu --model glm-4.7-flash
# 然后把 TOKENSFREE_REAL_EVIDENCE_SNAPSHOT 指向生成的 JSON（或手工填 EXPIRES_AT）
~~~

快照包含来源 URL、sha256、模型行与到期时间；启动时校验快照里的 model/provider 与配置一致，过期或错配一律拒绝。

令牌不是供应商 API Key；不要写进日志、Git 或聊天。

### 请求日志

每个请求向 stderr 打印一行结构化 JSON（不含 prompt 与回复内容）：

```json
{"ts": 1700000000.123, "level": "info", "event": "http_request", "method": "POST",
 "path": "/v1/chat/completions", "latency_ms": 12, "status": 200, "request_id": "req-..."}
```

每个响应都带 `x-request-id` 头，排障时把该头的值报给日志即可精确定位。

### 运维状态

`GET /api/status` 返回当前运行时的真实状态：

```json
{"version": "0.6.0", "uptime_s": 12.3, "simulation": true,
 "persistence": {"idempotency": true, "ledger": true, "events": true},
 "accounts": [{"provider": "zhipu", "status": "active", "cooldown_until": null, "requests_charged": 12}],
 "breakers": {"provider-a": {"state": "open", "fail_streak": 3, "window_fails": 3, "open_until": 1700000000.0}},
 "providers": [{"name": "mock-alpha", "model": "mock-a", "simulation": true,
                "state": "eligible", "policy_reason": "admitted",
                "evidence_expires_at": 1797593938.0}]}
```

`simulation` 为 false 表示配置了真实 provider（是否可调用仍由三条件闸门决定）；`breakers` 为空表示没有配置熔断器。

### 幂等请求

带 `Idempotency-Key` 头的相同请求只会真正执行一次，重放返回完全相同的响应体（跨重启也有效，前提是 `--db` 指向文件）：

```sh
curl -s http://127.0.0.1:8766/v1/chat/completions -H "Host: 127.0.0.1:8766" -H "Authorization: Bearer $TOKENSFREE_LOCAL_TOKEN" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"model":"tokensfree-auto","messages":[{"role":"user","content":"hi"}]}'
```

同一 key 换 payload → `409 idempotency_conflict`。`stream: true` 的请求**不支持**幂等键（流无法逐字节重放，带键返回 400）。

### 账号与账本

用 `--real --db` 启动时，每个 key 自动登记为账号（只存 sha256 指纹，不存 key）：成功/失败请求写入 `quota_ledger` 与 `events`（流式请求同样记账）。账号级失败会更新状态并影响路由：

| 上游错误 | 账号动作 | 路由行为 |
|---|---|---|
| `quota_exhausted` | 记 `platform_signal` + `exhausted` | 准入阶段跳过该 key |
| `auth_invalid` | 置 `invalid` | 准入阶段跳过该 key |
| `rate_limited` + Retry-After | 设置 `cooldown_until` | 冷却期内跳过该 key |
| `refusal` 等内容级错误 | 不动账号 | 立即终止，不在别的 key 上重放 |

替换 key（指纹变化）会自动把账号重置为 `active` 并清冷却；同一 key 重启不会重置状态。熔断状态也持久化在 `--db` 中。记账写库失败只记录 `bookkeeping_error`，不会把成功响应变成 500。

### 账号运维命令

~~~sh
.venv\Scripts\python scripts/accounts_admin.py --db .\tokensfree.db                 # 列出账号/状态/已计费请求数
.venv\Scripts\python scripts/accounts_admin.py --db .\tokensfree.db activate zhipu  # 重新激活
.venv\Scripts\python scripts/accounts_admin.py --db .\tokensfree.db reset-cooldown  # 清除所有冷却
~~~

### 数据库路径

`--db` 指向文件时父目录会自动创建、`~` 会展开；目标无法打开时输出单行 `cannot open db ...` 并退出（无 traceback）。`--port 0` 会由系统分配端口并在启动日志打印真实端口。

## 5. 验收

~~~sh
.venv\Scripts\python scripts/verify_p0.py     # lint → 测试 → 契约矩阵 → 导入门禁 → 用户旅程 → 密钥扫描
.venv\Scripts\python scripts/journey_smoke.py  # 只跑"文档用户旅程"：启动服务→models→chat→幂等→status→重启重放
.venv\Scripts\python scripts/check_imports.py # 只跑静态出口门禁
~~~

真实 DSH 冒烟（需要 node + 已解包的 DSH；路径可用环境变量覆盖，见下）：

~~~sh
TOKENSFREE_DSH_SMOKE=1 .venv\Scripts\python -m pytest tests/test_dsh_smoke_gate.py -q
# 或直接：
.venv\Scripts\python scripts/dsh_smoke.py <node.exe> <dsh/lib/bin.js>
~~~

也可以用环境变量代替参数：`TOKENSFREE_DSH_BIN=<dsh/lib/bin.js>`、`TOKENSFREE_NODE_BIN=<node.exe>`（未设置时依次尝试本机解包副本与打包 app.asar 副本）。

**干净克隆自检**：`git clone` → 建 venv → `pip install -r requirements-dev.txt` → `python scripts/verify_p0.py` 应输出 `P0 OK`（依赖安装需能访问 PyPI）。

**真实供应商验收**（需要 key，见 2.4）：`real_probe.py --fixture --stream --tools`、`real_smoke.py --fixture` 可在无 key 时自检；真实模式命令见 2.4，2026-11-19 已用智谱 glm-4.7-flash 实测通过。

## 6. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 403 `origin_rejected` | Host/Origin 不是 `127.0.0.1:<port>` / `localhost:<port>`；检查反向代理或 Host 头 |
| 401 `unauthorized` | 缺少或错误的 `Authorization: Bearer <本地令牌>`（方案名大小写不敏感） |
| 400 `unsupported_capability` | 请求了当前运行时没有的能力（流式/工具）；换 `--loopback` 运行时或等待真实适配器 |
| 503 `no_free_provider` | 所有候选被准入拒绝/熔断/冷却；查看响应里的 `tokensfree.attempts` |
| 429 `upstream_rate_limited` | 上游 429（智谱免费层常见 `1305` 访问量过大）；可重试，`real_probe.py` 已内置退避重试 |
| 502 `upstream_auth_failed` | 上游 401/403；账号会被标记 `invalid`，准入阶段跳过，换 key 后自动恢复 |
| 502 `upstream_error` / `upstream_auth_failed` | 上游故障或凭据失效；后者会禁用该账号 |
| 500 `internal_error` | 内核 bug；host 会返回结构化错误而不是直接断连，先看服务端日志里的 `request_id` |
| 启动即退出 | 未设置 `TOKENSFREE_LOCAL_TOKEN`；新后端要求稳定令牌 |

## 7. 已知限制（诚实清单）

- **真实供应商默认关闭，但已验证可用**：`adapters/openai_compat.py` 已用智谱 `glm-4.7-flash` 完成真实联调（2026-11-19）。真实调用仍需**三个条件同时成立**——`TOKENSFREE_ENABLE_REAL=1`、该 provider 在授权名单、`cost_evidence="free_verified"` 且证据未过期；默认全部不满足，所以默认零外发
- **免费额度高峰限流**：智谱免费模型常返回 429 `{error:{code:"1305"}}`（当前访问量过大）；网关把它归为可重试的 `upstream_rate_limited`，`real_probe.py` 已内置退避重试；单次请求仍可能遇到 429
- **系统凭据库仅 Windows**：Windows Credential Manager 已实现并本机验证；macOS/Linux 未实现（明确 fail-closed，不回退明文）。其他平台仍可用 key 文件或环境变量
- **跨平台零外发证明**：Windows 无 Linux 网络命名空间；当前靠"只配置受控 baseURL + 配置断言"保证，无 key 时默认零外发
- **零外发证明**：Windows 无 Linux 网络命名空间；当前靠"只配置 loopback baseURL + 断言"保证
- **无服务器框架依赖**：D1 以零框架 stdlib host 落地；同一 ASGI app 仍可交给 uvicorn/hypercorn 托管
- **旧实现仍存在**：`server.py` 等为冻结的 mock 参考实现，与新内核通过契约测试对照

## 8. 目录地图

~~~text
tokensfree/           新内核（配置/错误/日志/适配器/路由/持久化/边缘）
tokensfree/edge/host.py     可运行 HTTP 服务（stdlib asyncio + ASGI）
tokensfree/edge/asgi.py     ASGI 适配器（可换 uvicorn/hypercorn 托管）
scripts/verify_p0.py        一条命令验收
scripts/dsh_smoke.py        真实 DSH 冒烟
docs/superpowers/           设计文档与实施计划
server.py / providers.py    冻结的旧 mock 实现
~~~
