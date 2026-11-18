# tokensfree 0.6 · 本地路由实验台

**这是 mock-only 开发原型，不是真实免费 API 网关。没有外部模型请求，不需要任何账号或密钥。未公开发布。**

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

## 尚未实现
真实供应商适配器、余额读取、账号授权、系统密钥库、模型能力匹配、持久化、流式输出、工具调用、真实客户端集成、精确 token 计量、GPU 调度、Windows 安装包、后台常驻服务。没有收集用户凭证或真实账户调用测试。付费保护必须在未来结合平台侧硬停止机制验证，不能仅靠本地估算保证零账单。

## 验证

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q          # 119 项，Windows/macOS/Linux 三平台一致
python check_dsh_contract.py # 8 项契约矩阵，退出 0 表示拒绝行为符合预期
```

窗口环境为 Python 3.10+。当前阶段：P0 工程基线（设计见 [docs/superpowers/specs/](docs/superpowers/specs/)，计划见 [docs/superpowers/plans/](docs/superpowers/plans/)）。JS 通过 `node --check static/app.js`。真实供应商接入、SSE 与工具调用仍未实现。

## 项目结构
`server.py` 路由与本地服务；`test_gateway.py` 自动测试；`static/` 极客控制台；`static/catalog/` 资源目录。运行状态不会写入源码。`package.json` 仅供开发预览，无 npm 依赖。

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
