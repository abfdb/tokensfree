# 参与贡献

感谢你愿意改进 tokensfree！这是一个注重**可验证性与安全性**的小项目，改动请尽量小而完整。

## 环境准备

```powershell
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # 仅开发/测试需要；运行时不装第三方包
.venv\Scripts\python -m pytest tests/ -q
```

Linux/macOS 把 `.venv\Scripts\` 换成 `.venv/bin/` 即可。

## 提交前必须通过

```powershell
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m pytest -o addopts= -q
.venv\Scripts\python scripts\verify_p0.py   # ruff + pytest + 契约 + import guard + journey + secret scan
```

- 不要为了通过测试而降低断言强度；不要跳过门禁。
- 新增行为请附测试（本仓库习惯先写失败测试）。
- 涉及真实供应商的改动，请不要在 CI/测试里发起真实计费调用；用 fixture 上游或 mock。

## 代码约定

- 运行时零第三方依赖：`tokensfree/` 只允许标准库；网络调用只出现在 `tokensfree/adapters/`、`tokensfree/edge/host.py`、`tokensfree/desktop/process.py`（由 `scripts/check_imports.py` 守护）。
- 密钥/令牌不得写入源码、配置、日志、测试或截图；测试里只用明显的假值。
- 错误响应遵守固定错误码契约（见 `docs/` 与 `tests/test_contract.py`），不要回显用户输入或上游报错原文。
- 保持提交聚焦：一个主题一次提交，信息用 `feat:` / `fix:` / `docs:` / `test:` / `refactor:` 前缀。

## 新增供应商 / 接入方式

1. 在 `tokensfree/adapters/` 实现适配器（或复用 `OpenAICompatAdapter`）并注册。
2. 在 `providers.py` 增加 `ProviderSpec`（模型、能力、证据、成本策略）。
3. 补测试：成功、鉴权失败、限流、超时、流式、idempotency。
4. 在 `docs/providers/` 记录验证日期与实际响应；不要只写营销文案。

## 报告问题

- 用 Issue 模板描述：复现步骤、期望/实际、`python -m tokensfree --version`、操作系统与 Python 版本。
- **不要**粘贴真实 API Key、本地令牌或完整请求头；需要时用 `sk-***` 打码。
- 安全敏感问题请先只描述影响范围，不要公开利用细节。

## PR 检查清单

- [ ] 改动聚焦，描述里说明动机与影响面
- [ ] 新增/修改的测试覆盖行为，`pytest` 全绿
- [ ] `verify_p0.py` 通过
- [ ] 文档/CHANGELOG 已更新（用户可见行为变化必须写）
- [ ] 没有提交密钥、数据库、日志、截图里的敏感信息
