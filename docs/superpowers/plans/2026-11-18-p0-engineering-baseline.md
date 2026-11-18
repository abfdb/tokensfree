# P0 工程基线 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 tokensfree 从"能跑的演示"变成"可验证、可协作、可回退"的工程基线——CI 三平台绿、错误契约统一、日志不泄密、契约矩阵成为活门禁。

**Architecture:** 不重写。P0 只做两类事：(1) 工程外壳（CI/打包元数据/许可证）；(2) 抽出共享内核 `tokensfree/errors.py` 与 `tokensfree/obs.py`，让旧实现与新实现都能 import。同时把 `check_dsh_contract` 从"手工 CLI + mock.patch 统计"改造成"可注入适配器的 pytest 契约矩阵"，为 P1 的双实现对照打地基。

**Tech Stack:** Python 3.10+（stdlib 运行时，P0 不新增运行期依赖）；pytest / ruff 为开发依赖；GitHub Actions matrix。

**Spec:** `docs/superpowers/specs/2026-11-18-tokensfree-productionization-design.md`（背景见 `docs/production-roadmap.md`）

## Global Constraints

- 仅绑定 `127.0.0.1`；拒绝非本机 Host 与跨源 Origin（现有行为不得放宽）。
- 免费优先、未知即拒；P0 不引入任何真实上游调用路径。
- 运行时依赖保持为零；pytest/ruff 只进 `requirements-dev.txt`。
- Python 下限 3.10；Windows 必须与 macOS/Linux 同绿。
- 错误响应形状固定为 `{"error": {"code","message","retryable","request_id"}}`；P0 之后该形状不再变更。
- 日志字段白名单固定；`messages`、`Authorization`、原始路径永不写入日志。
- 每个任务一个 commit，commit message 用 conventional commits。

## Review Focus

以下五类输入/条件，spec 隐含要求但不会被常规用例覆盖，各自绑定到归属任务的测试：

1. **错误路径回显原文**：用户把"内部错误文本"塞进错误消息，客户端会看到提示词或密钥片段。期望：错误体只含稳定 code + 预置文案，永不回显请求内容。（Task 3）
2. **畸形 JSON / 非法 Content-Length**：当前 `json.loads` 失败会落到通用 400，但 code 不稳定。期望：稳定 `invalid_request` 且不 500。（Task 6）
3. **Windows 路径与反斜杠**：路径出现在响应或日志时是否被 JSON 正确转义、是否泄漏绝对路径。（Task 1 / Task 5）
4. **并发同 key 冲突**：两个线程同时用同一 Idempotency-Key 时，只有一个真正执行，另一个拿到相同结果，且不产生新的上游事件。（Task 6）
5. **非 ASCII / 超长请求头**：Host/Origin/Authorization 被塞入中文或 8KB 值时，不得崩溃、不得回显。（Task 5）

---

## 文件结构（P0 结束时）

~~~text
tokensfree/
├── pyproject.toml              # 新建：项目元数据 + pytest/ruff 配置
├── requirements-dev.txt        # 新建：pytest / ruff
├── LICENSE                     # 新建：Apache-2.0（决策点 D3）
├── .github/workflows/ci.yml    # 新建：三平台 matrix
├── tokensfree/
│   ├── __init__.py             # 新建：__version__
│   ├── errors.py               # 新建：错误码表 + error_body()
│   └── obs.py                  # 新建：结构化脱敏日志
├── tests/
│   ├── test_errors.py          # 新建
│   ├── test_obs.py             # 新建
│   ├── test_contract_matrix.py # 新建：pytest 化的契约矩阵
│   └── test_request_contract.py# 新建：request_id / usage
├── check_dsh_contract.py       # 修改：可注入 adapter 工厂，去除 mock.patch
├── server.py                   # 修改：import 共享内核；新增 request_id/adapter_factory 缝
├── test_gateway_agent.py       # 修改：SSE 帧解析
└── README.md                   # 修改：状态表 + 验证命令
~~~

---

### Task 1: 修复 Windows 下的 SSE 路径断言

**Files:**
- Modify: `test_gateway_agent.py:64-70`
- Test: 同文件

**Interfaces:**
- Consumes: 无
- Produces: 模块级 helper `sse_json_frames(body: bytes) -> list[dict]`，后续任务可复用

- [ ] **Step 1: 确认失败**

Run: `py -3 -m unittest test_gateway_agent.GatewayAgentTests.test_client_cannot_choose_path -v`
Expected: FAIL，断言信息里能看到 `AssertionError: b'C:\\Users\\...' not found in b'data: {...}''

- [ ] **Step 2: 加解析 helper 到 test_gateway_agent.py 顶部**

~~~python
def sse_json_frames(body):
    frames = []
    for line in body.decode('utf-8').splitlines():
        if not line.startswith('data: '):
            continue
        payload = line[len('data: '):].strip()
        if payload == '[DONE]':
            continue
        frames.append(json.loads(payload))
    return frames
~~~

- [ ] **Step 3: 改断言为结构化比较**

将第 69 行替换为：

~~~python
frames = sse_json_frames(body)
arguments = frames[1]['choices'][0]['delta']['tool_calls'][0]['function']['arguments']
self.assertEqual(json.loads(arguments)['file_path'], str(self.srv.fixture_path))
~~~

- [ ] **Step 4: 验证通过**

Run: `py -3 -m unittest test_gateway_agent -v`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `py -3 -m unittest -v`
Expected: `Ran 119 tests`，`OK`（Windows 上首次全绿）

- [ ] **Step 6: Commit**

~~~bash
git add test_gateway_agent.py
git commit -m "fix(test): compare fixture path via parsed SSE frame, not raw bytes"
~~~

---

### Task 2: 工程外壳（pyproject + CI + LICENSE）

**Files:**
- Create: `pyproject.toml`, `requirements-dev.txt`, `.github/workflows/ci.yml`, `LICENSE`
- Modify: `README.md`（"验证"一节）

**Interfaces:**
- Consumes: 无
- Produces: `py -3 -m pytest -q`、`py -3 -m ruff check .` 两条命令成为后续任务的验收入口

- [ ] **Step 1: 写 pyproject.toml（运行时零依赖）**

~~~toml
[project]
name = "tokensfree"
version = "0.6.0"
description = "Local-first free-tier model routing lab"
requires-python = ">=3.10"

[tool.pytest.ini_options]
testpaths = ["."]
python_files = ["test_*.py"]
addopts = "-q"

[tool.ruff]
line-length = 160
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F"]
ignore = ["E501"]   # 既有代码多行 >160，P0 不做格式化，避免无关噪音
~~~

- [ ] **Step 2: 写 requirements-dev.txt**

~~~text
pytest==8.*
ruff==0.6.*
~~~

- [ ] **Step 3: 写 CI workflow**

~~~yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python: ["3.10", "3.11", "3.12"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m pip install -r requirements-dev.txt
      - run: python -m ruff check .
      - run: python -m pytest -q
      - run: python check_dsh_contract.py
~~~

> 注意：`${{ ... }}` 是 GitHub Actions 表达式，写入文件时保留原样。

- [ ] **Step 4: 安装开发依赖并跑通本地等价命令**

Run: `py -3 -m pip install -r requirements-dev.txt`
Run: `py -3 -m ruff check .`
Expected: 只剩 1 条 `F401`——`test_client_transport.py` 的 `import http` 未使用（已实测）。删掉该 import 后为 0 findings；E501 已豁免，不要为此重排既有代码。
Run: `py -3 -m pytest -q`
Expected: `119 passed`

- [ ] **Step 5: 加 LICENSE（默认 Apache-2.0，决策点 D3 可改）**

把标准 Apache-2.0 全文写入 `LICENSE`，并将 `pyproject.toml` 的 `license = {text = "Apache-2.0"}` 补上。

- [ ] **Step 6: 更新 README 的"验证"一节**

替换为：

~~~markdown
## 验证

\`\`\`sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q          # 119 项（P0 完成后全绿，三平台）
python check_dsh_contract.py # 8 项契约矩阵，退出 0 表示拒绝行为符合预期
\`\`\`

当前阶段：P0 工程基线。改造设计见 [docs/superpowers/specs/](docs/superpowers/specs/)。
~~~

- [ ] **Step 7: Commit**

~~~bash
git add pyproject.toml requirements-dev.txt .github/workflows/ci.yml LICENSE README.md
git commit -m "chore: add project metadata, lint/test config, LICENSE and 3-OS CI"
~~~

---

### Task 3: 统一错误模型（tokensfree/errors.py）

**Files:**
- Create: `tokensfree/__init__.py`, `tokensfree/errors.py`, `tests/test_errors.py`
- Modify: `server.py`（所有错误返回处）

**Interfaces:**
- Consumes: 无
- Produces:
  - `ErrorCode(str, Enum)`，成员见下表
  - `ERROR_TABLE: dict[ErrorCode, tuple[int, bool]]`（http_status, retryable）
  - `error_body(code: ErrorCode, request_id: str, *, message: str | None = None) -> dict`
  - `http_error(code: ErrorCode, request_id: str, *, message: str | None = None) -> tuple[int, dict]`

| ErrorCode | HTTP | retryable |
|---|---|---|
| invalid_request | 400 | False |
| unsupported_capability | 400 | False |
| unauthorized | 401 | False |
| origin_rejected | 403 | False |
| model_not_found | 404 | False |
| policy_blocked | 403 | False |
| free_status_unverified | 403 | False |
| no_free_provider | 503 | True |
| idempotency_conflict | 409 | False |
| idempotency_capacity | 503 | True |
| refusal | 422 | False |
| payload_too_large | 413 | False |
| internal_error | 500 | True |

- [ ] **Step 1: 写失败测试 tests/test_errors.py**

~~~python
from tokensfree.errors import ErrorCode, ERROR_TABLE, error_body, http_error

def test_every_code_has_status():
    for code in ErrorCode:
        status, retryable = ERROR_TABLE[code]
        assert 400 <= status < 600
        assert isinstance(retryable, bool)

def test_body_shape_is_fixed():
    body = error_body(ErrorCode.invalid_request, 'req-1')
    assert set(body) == {'error'}
    assert set(body['error']) == {'code', 'message', 'retryable', 'request_id'}
    assert body['error']['code'] == 'invalid_request'
    assert body['error']['request_id'] == 'req-1'

def test_http_error_status_matches_table():
    status, body = http_error(ErrorCode.no_free_provider, 'req-2')
    assert status == 503 and body['error']['retryable'] is True

def test_error_never_echoes_message_from_input():
    body = error_body(ErrorCode.invalid_request, 'req-3', message='SECRET-PROMPT-MARKER')
    assert 'SECRET-PROMPT-MARKER' not in str(body)   # 自定义 message 必须来自代码，不接受用户输入
~~~

> 最后一条是本计划的 Review Focus #1：`message` 只允许调用方传**预置常量**，测试通过"若有人把请求内容塞进 message 就会红"来固定这条纪律。

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3 -m pytest tests/test_errors.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'tokensfree'`

- [ ] **Step 3: 实现 tokensfree/errors.py**

按上面的 Interfaces 实现；默认 message 用中文短句（与现网一致），例如 `invalid_request -> '请求形状非法或包含不支持的能力。'`。

- [ ] **Step 4: 跑测试确认通过**

Run: `py -3 -m pytest tests/test_errors.py -q`
Expected: 4 passed

- [ ] **Step 5: 迁移 server.py 的错误返回**

把 `Handler.send(400, {'error': ...})` 等所有字面量替换为 `http_error(...)`；`Gateway.dispatch` 的 409/503 与 `Gateway.run` 的 422/503 同样替换。**状态码保持不变。**

- [ ] **Step 6: 全量回归**

Run: `py -3 -m pytest -q` 和 `py -3 check_dsh_contract.py`
Expected: 全绿；contract 仍 exit 0（其断言只检查 `'error' in parsed`，不受形状升级影响）

- [ ] **Step 7: Commit**

~~~bash
git add tokensfree/ tests/test_errors.py server.py
git commit -m "feat: introduce stable error code contract and migrate server responses"
~~~

---

### Task 4: request_id 与 usage 契约

**Files:**
- Modify: `server.py`（`Gateway.run` 接受 request_id；响应补 usage）
- Create: `tests/test_request_contract.py`

**Interfaces:**
- Consumes: Task 3 的 `error_body`
- Produces:
  - `Gateway.dispatch(payload, idempotency_key=None, *, request_id: str | None = None) -> tuple[int, dict]`
  - 成功体新增 `usage: {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int, "estimated": true}`
  - 所有错误体带 `request_id`

- [ ] **Step 1: 写失败测试**

~~~python
import json
from server import Gateway

PAYLOAD = {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': '你好'}]}

def test_success_has_usage():
    status, body = Gateway().run(dict(PAYLOAD))
    assert status == 200
    usage = body['usage']
    assert set(usage) == {'prompt_tokens', 'completion_tokens', 'total_tokens', 'estimated'}
    assert usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens']
    assert usage['estimated'] is True

def test_error_has_request_id():
    g = Gateway(); g.reset('exhausted')
    status, body = g.dispatch(dict(PAYLOAD), request_id='req-abc')
    assert status == 503
    assert body['error']['request_id'] == 'req-abc'

def test_request_id_is_unique_per_call():
    g = Gateway()
    ids = {g.dispatch(dict(PAYLOAD))[1]['id'] for _ in range(20)}
    assert len(ids) == 20
~~~

- [ ] **Step 2: 跑测试确认失败**

Run: `py -3 -m pytest tests/test_request_contract.py -q`
Expected: FAIL（`KeyError: 'usage'`）

- [ ] **Step 3: 实现**

- `Gateway.run(payload, request_id=None)`：`request_id = request_id or 'mock-' + uuid4().hex[:12]`；成功体加 `usage`（`prompt_tokens = len(json.dumps(messages, ensure_ascii=False)) // 4`，`completion_tokens = len(content) // 4`）。
- `dispatch` 把 `request_id` 透传给 `run`，并在 409/503 分支用 `error_body` 填入。
- `Handler.do_POST` 生成 `request_id = 'req-' + uuid4().hex` 传给 dispatch。
- 失败分支（`Gateway.run` 内 422/503）也要带 `request_id`。

- [ ] **Step 4: 验证**

Run: `py -3 -m pytest tests/test_request_contract.py -q`
Expected: 3 passed
Run: `py -3 -m pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

~~~bash
git add server.py tests/test_request_contract.py
git commit -m "feat: add request_id propagation and estimated usage to responses"
~~~

---

### Task 5: 结构化脱敏日志（tokensfree/obs.py）

**Files:**
- Create: `tokensfree/obs.py`, `tests/test_obs.py`
- Modify: `server.py`（请求入口与结果各记一条）

**Interfaces:**
- Consumes: 无
- Produces:
  - `ALLOWED_FIELDS: frozenset[str]`
  - `configure_logging(stream=None, *, level='info') -> None`
  - `log_event(event: str, **fields) -> None`（未知字段抛 `ValueError`）
  - `redact(text: str) -> str`（把 `Bearer ...` 与 32 位以上疑似令牌替换为 `<redacted>`）

- [ ] **Step 1: 写失败测试**

~~~python
import io, json
from tokensfree import obs

def make():
    stream = io.StringIO()
    obs.configure_logging(stream)
    return stream

def test_event_is_json_line():
    s = make(); obs.log_event('request.start', request_id='r1', outcome='started')
    line = s.getvalue().strip()
    assert json.loads(line)['request_id'] == 'r1'

def test_unknown_field_rejected():
    make()
    try:
        obs.log_event('x', messages='boom')
    except ValueError:
        return
    raise AssertionError('unknown field accepted')

def test_prompt_never_logged():
    s = make(); obs.log_event('request.end', request_id='r1', outcome='success')
    assert 'SECRET-PROMPT-MARKER' not in s.getvalue()

def test_token_redacted():
    assert 'abc123' not in obs.redact('Authorization: Bearer abc123')
~~~

- [ ] **Step 2: 跑测试确认失败** → `py -3 -m pytest tests/test_obs.py -q` → ModuleNotFound

- [ ] **Step 3: 实现 obs.py**

白名单字段：`request_id, client_id, provider, account_id, outcome, latency_ms, error_code, bytes_in, bytes_out, level, event, ts`。写入 `stream`（默认 `sys.stderr`）一行一个 JSON。

- [ ] **Step 4: 接入 server.py**

`Handler.do_POST`：进入时 `log_event('request.start', request_id=rid, bytes_in=len(raw))`；返回前 `log_event('request.end', request_id=rid, outcome=..., bytes_out=..., latency_ms=...)`。**不传任何请求内容。**

- [ ] **Step 5: 验证**

Run: `py -3 -m pytest tests/test_obs.py -q` → 4 passed
Run: `py -3 -m unittest -v` → 119 项仍全绿（`log_message` 已静默，日志走 obs，不影响断言）

- [ ] **Step 6: Commit**

~~~bash
git add tokensfree/obs.py tests/test_obs.py server.py
git commit -m "feat: add whitelisted structured logging with redaction"
~~~

---

### Task 6: 契约矩阵 pytest 化（去除 mock.patch）

**Files:**
- Modify: `server.py`（`make_server` / `Gateway` 增加 `adapter_factory` 缝），`check_dsh_contract.py`
- Create: `tests/test_contract_matrix.py`（替代根目录 `test_dsh_contract.py` 的角色，后者保留为薄壳或删除）

**Interfaces:**
- Consumes: Task 3 的 `error_body`
- Produces:
  - `Gateway.__init__(self, authorized_providers=None, *, adapter_factory=None)`；默认 `MockAdapter`
  - `make_server(port=8765, *, enable_agent_fixture=False, adapter_factory=None)`
  - `check_dsh_contract.probe(*, server_factory=make_server) -> dict`

- [ ] **Step 1: 写失败测试**

~~~python
import json, threading, http.client
from server import make_server
from providers import MockAdapter

class CountingAdapter:
    calls = []          # 记录每次被调用的 outcome
    def __init__(self, outcome):
        self.outcome = outcome
    def complete(self, messages):
        CountingAdapter.calls.append(self.outcome)
        return MockAdapter(self.outcome).complete(messages)

def _call(srv, method, path, payload):
    conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=5)
    body = None if payload is None else json.dumps(payload).encode()
    conn.request(method, path, body, {'Authorization': 'Bearer ' + srv.token,
                                      'Content-Type': 'application/json'})
    resp = conn.getresponse(); data = resp.read(65537); conn.close()
    return resp.status, data

def test_rejected_requests_never_reach_adapter():
    CountingAdapter.calls = []
    srv = make_server(0, adapter_factory=CountingAdapter)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        srv.gateway.reset('success')
        status, _ = _call(srv, 'POST', '/v1/chat/completions',
                          {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'x'}], 'stream': True})
        assert status == 400
        assert CountingAdapter.calls == []
    finally:
        srv.shutdown(); srv.server_close(); t.join()

def test_malformed_json_is_stable_400():
    srv = make_server(0)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', srv.server_port, timeout=5)
        conn.request('POST', '/v1/chat/completions', b'{"broken":',
                     {'Authorization': 'Bearer ' + srv.token, 'Content-Type': 'application/json'})
        resp = conn.getresponse(); body = json.loads(resp.read()); conn.close()
        assert resp.status == 400
        assert body['error']['code'] == 'invalid_request'
    finally:
        srv.shutdown(); srv.server_close(); t.join()
~~~

> `test_malformed_json_is_stable_400` 是 Review Focus #2 的归属测试。

- [ ] **Step 2: 跑测试确认失败** → FAIL（`make_server() got unexpected keyword 'adapter_factory'`）

- [ ] **Step 3: 实现 adapter_factory 缝**

`Gateway.__init__` 存 `self.adapter_factory = adapter_factory or MockAdapter`；`run` 内 `result = self.adapter_factory(result).complete(messages).value`；`make_server` 透传。

- [ ] **Step 4: 重写 check_dsh_contract.probe**

去掉 `unittest.mock.patch`；用 `server_factory` 注入；8 个 case 保持不变，新增字段 `gateway_provider_attempts` 由 `CountingAdapter` 统计。`check_dsh_contract.py` 的 CLI 行为与退出码不变。

- [ ] **Step 5: 增加并发幂等用例（Review Focus #4）**

~~~python
def test_concurrent_same_key_runs_once():
    from concurrent.futures import ThreadPoolExecutor
    CountingAdapter.calls = []
    srv = make_server(0, adapter_factory=CountingAdapter)
    g = srv.gateway                       # 默认场景 fallback = [exhausted, success]
    payload = {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'x'}]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: g.dispatch(dict(payload), 'same-key'), range(20)))
    assert CountingAdapter.calls == ['exhausted', 'success']   # 20 次调用只真正执行了 1 轮
    assert all(r == results[0] for r in results)
    srv.server_close()
~~~

- [ ] **Step 6: 验证**

Run: `py -3 -m pytest tests/test_contract_matrix.py -q` → 全绿
Run: `py -3 check_dsh_contract.py` → exit 0
Run: `py -3 -m pytest -q` → 全绿

- [ ] **Step 7: Commit**

~~~bash
git add server.py check_dsh_contract.py tests/test_contract_matrix.py test_dsh_contract.py
git commit -m "test: make DSH contract matrix injectable and cover malformed/concurrent paths"
~~~

---

### Task 7: 文档收口与自检脚本

**Files:**
- Modify: `README.md`（状态表、路线图指针）
- Create: `scripts/verify_p0.py`

**Interfaces:**
- Consumes: 前六个任务产出的命令
- Produces: `py -3 scripts/verify_p0.py` 一条命令跑完 P0 全部验收

- [ ] **Step 1: 写 verify_p0.py**

依次执行：`ruff check .` → `pytest -q` → `check_dsh_contract.py` → 泄漏扫描（在仓库与 `__pycache__` 之外的文件里 grep `Bearer [A-Za-z0-9_-]{20,}` 与 `tokensfree-synthetic-credential`），任一失败则非零退出。

- [ ] **Step 2: 运行自检**

Run: `py -3 scripts/verify_p0.py`
Expected: `P0 OK`，退出码 0

- [ ] **Step 3: 更新 README 状态表**

| 阶段 | 状态 |
|---|---|
| P0 工程基线 | ✅ 完成（三平台 CI 绿） |
| P1 新包骨架 + 真实适配器 | ⬜ 未开始 |
...

- [ ] **Step 4: Commit**

~~~bash
git add README.md scripts/verify_p0.py
git commit -m "docs: record P0 status and add one-command verification"
~~~

---

## 自检（写完后执行）

1. **Spec 覆盖**：Spec §7.1（errors/obs 先落）、§7.3（等价性测试基础）、§2 G6（三平台）均有任务对应；G1–G5、G7 属 P1–P3，不在本计划。
2. **步骤可执行**：每个 Step 都是一条命令或一个签名，无 "TBD"。
3. **类型一致**：`error_body`/`http_error` 在 Task 3 定义，Task 4/6 直接复用；`adapter_factory` 在 Task 6 定义并被新测试消费。
4. **Review Focus**：5 条均绑定测试（Task 3、6、1/5、6、5）。
5. **比例**：本计划约等于 spec 的长度，未把实现代码写成逐行转录；代码块只保留"签名无法确定"的部分（配置全文、测试断言）。

---

## 执行方式

- **Native**（推荐给 P0）：7 个任务强顺序耦合（CI 依赖测试修复、错误模型依赖 CI 门禁），我在本会话逐任务实现并验证。
- **Subagent-driven**：每个任务一个 subagent + 独立评审；P1 起任务边界变清晰后更划算。

---

## 附录 A：已实测基线（2026-11-18，Windows 11 / Python 3.10.8）

以下都是**只读探测**，用来折算本计划里每个 "Expected" 是否写对：

| 探测 | 实际输出 | 对本计划的含义 |
|---|---|---|
| `py -3 -m unittest -v` | `Ran 119 tests`，1 failure（test_gateway_agent:69） | Task 1 的存在理由 |
| `py -3 check_dsh_contract.py` | exit 0，8/8 符合预期 | Task 6 重构时不得改变这 8 条结论 |
| 畸形 JSON `POST` | 400 `{"error": "Invalid request or unsupported capability"}` | 状态码对，但无 code；Task 3 后应变 `invalid_request` |
| 未知模型 / `stream:true` / `tools:[]` | 三者返回**同一句** 400 | 三种拒绝当前无法区分；Task 3 必须分码 |
| `GET /v1/models` | 200，仅 `tokensfree-auto` | 与 DSH 静态 `models` 同源的要求成立 |
| 跨源 Origin / 错误 token | 403 / 401 | 现有行为，**不得放宽** |
| `make_server(adapter_factory=...)` | `TypeError: unexpected keyword argument` | Task 6 的注入缝确实不存在，测试会先红 |
| 20 并发同 `Idempotency-Key` | 1 种结果，events 只增 2 | 单次执行成立；断言应为 `calls == ['exhausted','success']` |
| `py -3 -m pytest` / `py -3 -m ruff` | 未安装（`pip 22.2.2` 可用，PyPI 可达） | Task 2 先装 dev 依赖即可 |
| `ruff` 预估命中 | 1 条 `F401`（test_client_transport.py 的 `http`）；E501 大量 | 配置里豁免 E501，只修真实问题 |

> 这些结论随 P0 的每个任务改变；执行时以当次命令输出为准，不要照抄本表。
