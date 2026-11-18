# P2 流式闭环与 DSH 接入 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把一个真实 HTTP 后端接到已经完成的内核上，让 DSH 能用 `stream:true` + 工具定义完成一次 `read` 工具往返（MVP 完成线）。

**Architecture:** 内核（`tokensfree/edge/app_core.py`、`stream_core.py`、`router/*`、`adapters/*`、`store/*`）已完成且与框架无关。本计划只加"搬字节"的边缘层与验收脚本；所有策略判断仍在内核中，边缘层不得做任何路由决定。

**Tech Stack:** 待 D1 决定（aiohttp 推荐 / FastAPI / 纯 stdlib）。除依赖选择外，本计划不引入其他技术。

**Spec:** `docs/superpowers/specs/2026-11-18-tokensfree-productionization-design.md`

## Global Constraints

- 边缘层只做三件事：解析传输、调用内核、写回字节。**不得**出现候选选择、准入、重试逻辑。
- 仅绑定 `127.0.0.1`；Host/Origin/本地令牌校验保持现状。
- 流式响应在发出首字节后不得切换供应商（由 `stream_core` 保证，边缘层不得绕过）。
- 错误响应必须来自 `tokensfree/errors.py` 的 `ErrorCode`。
- 每个任务一个 commit，TDD，全量回归绿。

## Review Focus

1. **边缘层偷做决策**：出现"如果 provider A 失败就换 B"的代码即违规——这必须由 `execute`/`iter_frames` 决定。（Task 1）
2. **HTTP 200 已发出后改状态码**：任何"流中出错就返回 500"的实现都是错的；只能发 SSE error 帧。（Task 2）
3. **双实现行为漂移**：旧 server 拒绝的行为，新后端必须逐条一致，且上游尝试计数为 0。（Task 3）
4. **DSH 请求形状假设**：DSH 会带 20+ 工具定义与 `developer` 角色；不得硬编码工具名或悄悄丢弃 `tools`。（Task 4）
5. **本地令牌轮换**：DSH 配置里 `apiKeyEnv` 需要稳定令牌，不能用重启即轮换的随机值。（Task 4）

---

### Task 1: HTTP 边缘适配器（BLOCKED — 等 D1）

**Files:** Create `tokensfree/edge/__main__.py`（或 `tokensfree/server_main.py`）、`tests/test_edge_server.py`

**Interfaces:**
- Consumes: `tokensfree.edge.app_core.handle_chat`、`tokensfree.edge.stream_core.prepare_stream/iter_frames`、`tokensfree.runtime.build_*`
- Produces: 可启动的本地服务，端点 `GET /v1/models`、`POST /v1/chat/completions`

- [ ] Step 1 失败测试：启动后端 → `GET /v1/models` 200；非流式 chat 200 且响应形状与旧实现一致
- [ ] Step 2 实现：路由 + 本地令牌校验 + 调用 `handle_chat`
- [ ] Step 3 通过 + 全量回归
- [ ] Step 4 commit

### Task 2: 流式端点（BLOCKED — 依赖 Task 1）

- [ ] Step 1 失败测试：`stream:true` 返回 `text/event-stream`，帧序列为 role→content→finish→`[DONE]`
- [ ] Step 2 实现：`prepare_stream` 失败 → 普通 JSON 错误（此时还没发 200）；成功 → 逐帧写出并 flush
- [ ] Step 3 失败测试：上游在首帧后断流 → 客户端只收到一个 `event: error`，无 `[DONE]`，且不切换供应商
- [ ] Step 4 实现/通过/commit

### Task 3: 双实现契约对照（BLOCKED — 依赖 Task 1）

- [ ] Step 1 把 `tests/test_contract_matrix.py` 参数化为 `backend_factory`（旧 `server.make_server` / 新后端）
- [ ] Step 2 两种后端跑同一组用例：拒绝行为、上游尝试数为 0、错误 code 一致
- [ ] Step 3 差异修复 / commit

### Task 4: DSH 冒烟（BLOCKED — 依赖 Task 2 与 D2 或本地 mock 上游）

- [ ] Step 1 写 `scripts/dsh_smoke.py`：临时 HOME/DSH_HOME、空工作目录、固定 DSH 版本、自定义 provider 指向本地网关
- [ ] Step 2 断言：请求含 `stream:true` 与 `tools`；网关转发；`tool_calls` 下发；工具结果回传；最终 assistant 文本非空；除本地网关外外发连接为 0
- [ ] Step 3 令牌：网关支持 `TOKENSFREE_LOCAL_TOKEN` 固定令牌（已具备 `load_config` 能力，需接到边缘层）
- [ ] Step 4 记录命令与输出到 `docs/dsh-sse-stage.md`
- [ ] Step 5 commit

### Task 5: 真实供应商接入（BLOCKED — 等 D2）

- [ ] Step 1 写第一家 adapter（OpenAI 兼容，能力位 text/stream/tools），默认禁用
- [ ] Step 2 keyring 凭据绑定（复用 `credentials.py` 语义，替换 `DisabledSystemVault`）
- [ ] Step 3 用本地假上游覆盖 401/403/429/5xx/断流；再按 `docs/real-provider-readiness.md` 用真实账号人工验收
- [ ] Step 4 commit

---

## MVP 验收（对应设计文档 §2 G1–G7）

| 目标 | 验收命令 |
|---|---|
| G1 DSH 工具往返 | `python scripts/dsh_smoke.py` 退出 0 |
| G2 默认零外发 | 断网环境下跑全量测试 + 契约矩阵 |
| G3 流式协议 | Task 2 的 6 类流式用例 |
| G4 幂等跨重启 | 重启后用同 key 重放，适配器/上游调用数不增加 |
| G5 凭证零落盘 | `scripts/verify_p0.py` 的密钥扫描 + keyring 断言 |
| G6 三平台 | CI matrix 全绿 |
| G7 审计可重建 | 账本重放测试 |
