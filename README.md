# tokensfree 0.2.1 · 本地路由实验台

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
- 七种模拟场景：正常、额度耗尽切换、全部耗尽停止、限流冷却、凭证失效、拒绝停止、服务故障。
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
python -m unittest -v
```

26 项单元/HTTP 测试通过（路由、耗尽、拒绝、冷却、验证、并发、Host/Origin、访问令牌、静态路由和路径限制）。JS 通过 `node --check static/app.js`。云端 Chrome 实测了备用成功与全部耗尽停止，查看桌面布局。未完成 Windows、手机、第三方客户端与真实平台测试。导入导出保留旧原型实现，未在本轮重新做浏览器全链路测试。

## 项目结构
`server.py` 路由与本地服务；`test_gateway.py` 自动测试；`static/` 极客控制台；`static/catalog/` 资源目录。运行状态不会写入源码。`package.json` 仅供开发预览，无 npm 依赖。

开源许可证尚待决定。当前无 LICENSE，不应声称已作为开源项目发布。


## 0.2.1：重复请求保护
`POST /v1/chat/completions` 可带 `Idempotency-Key`（1–128 位英文字母、数字、`.`、`_`、`:` 或 `-`）。同一键、同一内容在缓存有效期内返回原结果，不再次尝试供应商；同一键不同内容返回 409。失败结果也缓存，避免不确定重试消耗额度。

缓存只在当前进程内保留 5 分钟、最多 100 个键。容量满时拒绝新键请求，不执行供应商；重启或超时后缓存失效；重置测试场景不会清除它。新任务使用新键，重试同一任务保留原键。不承诺跨重启或真实上游的 exactly-once。缓存存请求 HMAC 而非明文提示词；目前响应仅为模拟固定文本。网页按钮目前每次点击仍是新请求，此能力供 API 客户端接入。

真实平台接入的安全与验收要求见 [接入门槛](docs/real-provider-readiness.md)。
