# tokensfree 操作手册（一页纸）

> 适用版本：仓库 HEAD（P0–P3 内核已落地，真实供应商待 D2）。本文只写**现在能运行什么、怎么验收、出错怎么查**。

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

端点：`GET /v1/models`、`POST /v1/chat/completions`（非流式）。
`stream:true` 会得到 `400 unsupported_capability`——mock 运行时没有流式适配器，这是**有意的 fail-closed**。

### 2.3 新后端（loopback 流式运行时）

先起一个本地假 SSE 上游（任意实现），再：

~~~sh
.venv\Scripts\python -m tokensfree --port 8766 --loopback <上游端口>
~~~

流式响应使用 `Transfer-Encoding: chunked`，帧序列 role → content → finish → `[DONE]`。

## 3. 模型选择语义

- `model: "tokensfree-auto"`：由路由器按配置顺序选择（默认行为）
- `model: "<具体模型 id>"`：只使用 `/v1/models` 中该 id 对应的供应商；同 id 多供应商时按候选顺序回退
- 未知模型 id：`404 model_not_found`（不会静默路由到别的模型）

## 3. 令牌与环境变量

| 变量 | 作用 |
|---|---|
| `TOKENSFREE_LOCAL_TOKEN` | 新后端的稳定本地令牌（DSH 通过 `apiKeyEnv` 引用它） |
| `TOKENSFREE_ENABLE_REAL` | 真实调用开关，默认 `0`；当前没有真实适配器，打开也无路可走 |
| `TOKENSFREE_PORT` / `TOKENSFREE_MAX_BODY_BYTES` / `TOKENSFREE_TIMEOUT_S` | 启动参数（由 `load_config` 校验） |

令牌不是供应商 API Key；不要写进日志、Git 或聊天。

### 幂等请求

带 `Idempotency-Key` 头的相同请求只会真正执行一次，重放返回完全相同的响应体（跨重启也有效，前提是 `--db` 指向文件）：

```sh
curl -s http://127.0.0.1:8766/v1/chat/completions -H "Host: 127.0.0.1:8766" -H "Authorization: Bearer $TOKENSFREE_LOCAL_TOKEN" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"model":"tokensfree-auto","messages":[{"role":"user","content":"hi"}]}'
```

同一 key 换 payload → `409 idempotency_conflict`。

## 4. 验收

~~~sh
.venv\Scripts\python scripts/verify_p0.py     # lint → 测试 → 契约矩阵 → 导入门禁 → 密钥扫描
.venv\Scripts\python scripts/check_imports.py # 只跑静态出口门禁
~~~

真实 DSH 冒烟（需要 node + 已解包的 DSH；路径可用环境变量覆盖，见下）：

~~~sh
TOKENSFREE_DSH_SMOKE=1 .venv\Scripts\python -m pytest tests/test_dsh_smoke_gate.py -q
# 或直接：
.venv\Scripts\python scripts/dsh_smoke.py <node.exe> <dsh/lib/bin.js>
~~~

也可以用环境变量代替参数：`TOKENSFREE_DSH_BIN=<dsh/lib/bin.js>`、`TOKENSFREE_NODE_BIN=<node.exe>`（未设置时依次尝试本机解包副本与打包 app.asar 副本）。

**干净克隆自检**：`git clone` → 建 venv → `pip install -r requirements-dev.txt` → `python scripts/verify_p0.py` 应输出 `P0 OK`（已在 2026-11-18 实测通过，329 passed / 1 skipped）。

## 5. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 403 `origin_rejected` | Host/Origin 不是 `127.0.0.1:<port>` / `localhost:<port>`；检查反向代理或 Host 头 |
| 401 `unauthorized` | 缺少或错误的 `Authorization: Bearer <本地令牌>`（方案名大小写不敏感） |
| 400 `unsupported_capability` | 请求了当前运行时没有的能力（流式/工具）；换 `--loopback` 运行时或等待真实适配器 |
| 503 `no_free_provider` | 所有候选被准入拒绝/熔断/冷却；查看响应里的 `tokensfree.attempts` |
| 429 `upstream_rate_limited` | 上游 429 且带可解析的 `Retry-After`；这是可重试限流 |
| 502 `upstream_error` / `upstream_auth_failed` | 上游故障或凭据失效；后者会禁用该账号 |
| 500 `internal_error` | 内核 bug；host 会返回结构化错误而不是直接断连，先看服务端日志里的 `request_id` |\n| 启动即退出 | 未设置 `TOKENSFREE_LOCAL_TOKEN`；新后端要求稳定令牌 |

## 6. 已知限制（诚实清单）

- **没有启用真实供应商**：`adapters/openai_compat.py` 已实现；真实调用需要**三个条件同时成立**——`TOKENSFREE_ENABLE_REAL=1`、该 provider 在授权名单、`cost_evidence="free_verified"` 且证据未过期。默认全部不满足，所以仍然零外发。等你提供供应商与账号后即可开启
- **没有系统凭据库**：keyring 绑定未实现（等 D2）
- **零外发证明**：Windows 无 Linux 网络命名空间；当前靠"只配置 loopback baseURL + 断言"保证
- **无服务器框架选型**：当前 stdlib host 已能跑 DSH；D1 只影响托管方式
- **旧实现仍存在**：`server.py` 等为冻结的 mock 参考实现，与新内核通过契约测试对照

## 7. 目录地图

~~~text
tokensfree/           新内核（配置/错误/日志/适配器/路由/持久化/边缘）
tokensfree/edge/host.py     可运行 HTTP 服务（stdlib asyncio + ASGI）
tokensfree/edge/asgi.py     ASGI 适配器（可换 uvicorn/hypercorn 托管）
scripts/verify_p0.py        一条命令验收
scripts/dsh_smoke.py        真实 DSH 冒烟
docs/superpowers/           设计文档与实施计划
server.py / providers.py    冻结的旧 mock 实现
~~~
