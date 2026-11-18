# 供应商档案：智谱 BigModel（GLM）

> 状态：**文档与端点已核验（2026-11-18），真实调用尚未进行（等 API Key）**。
> 核验方式：官方文档（docs.bigmodel.cn）、定价页、以及对 `POST /api/paas/v4/chat/completions` 的无凭据探测（返回 401）。未使用任何真实密钥。

## 基础信息（已核验）

| 项 | 值 |
|---|---|
| 控制台 / 注册 | https://open.bigmodel.cn （文档在 docs.bigmodel.cn） |
| Base URL | `https://open.bigmodel.cn/api/paas/v4` |
| 对话端点 | `POST /chat/completions`（OpenAI 兼容） |
| 鉴权 | `Authorization: Bearer <API_KEY>` |
| 错误体形状 | `{"error":{"code":"401","message":"..."}}`（探测实测） |
| 响应头 | 含 `x-request-id` |

## 免费模型（定价页 2026-11-18 抓取）

| 模型 | 上下文 | 输入/输出价格 | 能力 |
|---|---|---|---|
| **GLM-4.7-Flash** | 200K | **免费 / 免费** | 流式、Function Calling、思考模式、结构化输出（模型页明示） |
| GLM-4.5-Flash | 128K | 免费 / 免费 | 文本 |
| GLM-4-Flash-250414 | 128K | 免费 / 免费 | 文本 |
| GLM-Z1-Flash | 128K | 免费 / 免费 | 推理 |

注意：**GLM-5.3-Flash 是付费模型**（0.8 / 2.8 元每百万 tokens），不要误选。GLM-4.7-Flash 页面标注"免费文本模型"，且具备 DSH 需要的工具调用与流式能力。

## 速率限制（官方文档）

| 错误码 | 含义 | 是否可重试 |
|---|---|---|
| 1302 | 触发用户速率限制（并发/频率） | **可重试**（退避后） |
| 1305 | 平台服务过载 | **可重试**（稍后） |
| 1113 | 余额不足 / 免费额度用尽 | 不可重试 |

本仓库的 OpenAI 兼容适配器会解析 429 响应体里的 `error.code`：1302/1305 → `rate_limited`（可重试），1113 → `quota_exhausted`（不可重试）。已用本地 fixture 覆盖这三类。

## 本仓库配置

```sh
export TOKENSFREE_REAL_PROVIDER=zhipu
export TOKENSFREE_REAL_BASE_URL=https://open.bigmodel.cn/api/paas/v4
export TOKENSFREE_REAL_MODEL=glm-4.7-flash
export TOKENSFREE_REAL_API_KEY=<你的 API Key>
export TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT=<证据到期时间，Unix 秒>
export TOKENSFREE_ENABLE_REAL=1

python -m tokensfree --port 8766 --real --db .\tokensfree.db
python scripts/real_smoke.py       # 真实验收（启动网关 + 真实 DSH）
```

多个免费额度的账号可以用 `TOKENSFREE_REAL_API_KEYS=key1,key2`（最多 8 个）同时接入：每个 key 是一个候选 provider，遇到可重试限流（1302/1305）会自动切到下一个 key。

免费证据建议每 30 天复核一次定价页后更新 `TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT`，并把 URL 与抓取时间记入 `.superpowers/` 下的进度账本（不进 Git 的内容）。

## 待办（拿到 Key 后）

1. `python scripts/real_probe.py --stream --tools`：用真实 Key 直接探针（非流式 / 流式 / 工具调用三条路径，成本极小），确认响应体形状、`usage` 与 `tool_calls`
2. `python scripts/evidence_snapshot.py --provider zhipu --model glm-4.7-flash`：抓取官方定价页、计算 sha256、确认输入/输出两列都是"免费"，把快照写到 `.superpowers/evidence/`（不进 Git），并给出 `TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT` 建议值
2. 跑 `scripts/real_smoke.py`（真实 DSH 走网关）
3. 若 429 出现，核对实际 `error.code` 与上表是否一致
4. 记录证据与验收结果
