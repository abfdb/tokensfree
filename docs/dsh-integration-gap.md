# DSH 接入审计与离线契约实验

## 结论

DSH 可以作为中国用户优先评估的 harness 候选，但“harness 可本地运行”不代表任何供应商的免费 API、GPU 或地区资格已经成立。此版本尚不能作为 DSH agent 的完整模型后端。

官方资料基准：DeepSeek-ai/deepseek-harness `39163dd30f703a79079e2d2446545cff3c453738`（2026-11-17 查询 master）。

- [官方供应商配置](https://github.com/deepseek-ai/deepseek-harness/blob/39163dd30f703a79079e2d2446545cff3c453738/docs/user/guide/providers.md)
- [官方 DeepSeek adapter](https://github.com/deepseek-ai/deepseek-harness/blob/39163dd30f703a79079e2d2446545cff3c453738/packages/llm/llm-deepseek/README.md)

官方自定义模型 API 支持四种协议：`openai-completions`、`openai-responses`、`anthropic-messages`、`google-generative-ai`。协议必须与后端一致，不能用同一名字假装兼容。内置 DeepSeek adapter 使用 Messages 传输，因此不能把 tokensfree 当前 chat endpoint 直接填成内置 DeepSeek 服务。

优先路线是将来用独立的自定义 `openai-completions` provider 对接 tokensfree，而不是修改 DSH 全局内置供应商。配置中的 `supportsDeveloperRole: false` 与 `maxTokensField: max_tokens` 可调整请求形状，但不能补上 tokensfree 没实现的流式或工具调用。不要覆盖现有 cordis.patch.yml；官方说明整个 entry override 可能替换其他配置。

## 可重复实验

```sh
python check_dsh_contract.py
```

程序自启随机端口本地网关，执行真实 loopback HTTP 请求并清理服务。不会启动 DSH、联网供应商、读用户配置或写凭据。输出明确 `dsh_executed=false`。退出 0 只说明当前接受/拒绝行为符合预期，不表示 DSH 兼容。

| 契约项 | 实测 | 结论 |
|---|---|---|
| GET /v1/models | 200 | 基础列举有实现 |
| 非流式纯文本 chat | 200 模拟响应 | 仅有限文本格式 |
| chat stream=true | 400 | 尚无 SSE |
| chat tools | 400 | 尚无 agent 工具调用循环 |
| developer role | 400 | 需配置降级或实现 |
| /v1/responses | 404 | 未实现 |
| /v1/messages | 404 | 未实现 |
| Gemini generateContent | 404 | 未实现 |

所有拒绝场景均验证供应商尝试数为 0。该矩阵是根据官方支持协议构建的最小请求，不是从真实 DSH 捕获的完整请求，也不是每种协议全量测试。

## 到可用的差距

1. **流式与 agent 语义**：需 SSE 生命周期、工具参数增量、结束原因、取消与错误语义，再验证真实 DSH 的一轮工具调用/结果回传；不能偷偷丢弃 tools 来让请求成功。
2. **身份与重试**：现有本地 token 重启轮换，DSH 自定义 provider 保存方式和重新授权需设计；仅传本地网关令牌，不让 harness 接触供应商密钥。需核对 DSH 自动重试与网关去重，防止重复消耗。
3. **真实模型**：尚未选定中国可合法使用且免费边界可核验的具体账号/模型。DeepSeek harness 免费开源不等于 DeepSeek API 免费。
4. **注册辅助**：目前仅有资源目录，不具有注册执行、OAuth、验证码处理或账号接入能力。未来应引导官网，由用户处理必要条款与验证；不批量创建身份或规避额度。
5. **GPU**：算力租约/队列/任务部署与 token 路由不是同一协议，应分模块，不能用 chat provider 冒充 GPU 调度。
6. **Codex/Cursor**：本次未验证，两者需独立官方配置与真实客户端实验，不能由 DSH 结果外推。

现阶段 UI 完全未改，真实出口仍硬阻断，CI 暂停。

## 真实 DSH 客户端离线实验（与上面的契约探测不同）

已另外安装官方 npm `@deepseek-ai/dsh@0.2.2-rc.1`（禁用安装脚本），真实执行 headless 客户端，使用独立网络命名空间、临时 HOME/DSH_HOME、空工作目录和清空后的子进程环境。命名空间只有 loopback；未登录、未提供供应商密钥、未访问真实模型服务。

实际观察：主请求为 `stream:true`，带 **23 个工具定义**，被 tokensfree 以 **HTTP 400 / INVALID_REQUEST** 拒绝；另有一次不带工具的流式辅助请求。不能根据这条辅助请求认定 DSH 在自动重试。网关没有供应商执行事件。由此实证：当前版本不能直接作为 DSH 的可用 agent 后端，首先需要 SSE 与工具调用协议，而不是让用户填 API key。

复现入口（仅 Linux；不是 Windows 安装指南）：

```sh
python experiments/dsh_offline_probe.py /absolute/path/to/dsh/lib/bin.js
```

脚本要求自行准备上述固定官方版本，拒绝在没有隔离命名空间的情况下继续。不要替换成自己的现有 DSH home。输出仅记录请求形状，不记录提示正文，本地会话令牌会脱敏；依赖、临时 home 和运行日志未加入仓库。该实验不会改动已有 DSH 配置。隔离失败就停止，不使用普通网络重试。

官方 master 文档与固定 npm 版本不是同一版本号；文档只用于配置线索，以上实际请求形状以本次固定包运行观察为准。本轮 89 项 Python 测试通过；运行真实 DSH 是独立的有界实验，不在常规测试中自动安装或启动。没有证明流式/工具处理成功，更没有完成真实供应商联调或中国网络可达性测试。

独立审查后的复现脚本增加固定包名称/版本元数据检查、HTTP 状态采集、MockAdapter 实际调用计数，以及“带工具流式请求被 400 拒绝且零适配器调用”的判定；退出 0 只表示观察到这一**预期失败**，不是接入成功。包元数据检查不等于供应链完整性签名验证。网络命名空间不是文件系统沙箱：临时 HOME 可避免正常配置加载，但不能防御恶意依赖读取宿主其他可读文件，因此只运行可信的官方包。


## 2026-11-18 更新：已闭合与仍缺

新内核（`tokensfree/`）已经用**真实打包 DSH 0.2.2-rc.1 headless 客户端**在合成上游上完成一次闭环（`scripts/dsh_smoke.py`，opt-in 门禁 `TOKENSFREE_DSH_SMOKE=1`）。

**已闭合**

| 差距项 | 现状 |
|---|---|
| 流式（SSE） | 客户端帧编码、上游 SSE 严格解析、流式核心均实现并有测试 |
| 工具调用循环 | `tools` 透传、`tool_calls` 增量、工具结果回传；真实 DSH 完成一次 `read` 往返 |
| developer role | 显式映射为 system（策略可配置为拒绝） |
| 模型目录 | `/v1/models` 与 `edge/models.py` 同源 |
| 本地令牌 | 支持 `TOKENSFREE_LOCAL_TOKEN` 稳定令牌（供 DSH `apiKeyEnv` 使用） |
| 幂等 | 持久化幂等键已接入请求链路（跨重启回放） |

**仍缺**

| 差距项 | 阻塞于 |
|---|---|
| 真实供应商适配器与免费资格证据 | D2（首家供应商选择） |
| 系统凭据库（keyring）绑定 | D2 |
| 真实 DSH + 真实模型的联调 | D2 |
| 命名空间级"零外发"证明 | Windows 无 Linux `unshare`；当前由"仅 loopback baseURL + 断言"保证 |
| 多协议端点（`/v1/responses`、`/v1/messages`、Gemini 原生） | 未排期 |
| 账号池调度与额度平台信号同步 | 未排期 |
| 服务器框架替换（D1） | 当前 stdlib host 已可运行；D1 只影响托管方式 |
