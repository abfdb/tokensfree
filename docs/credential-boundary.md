# 0.6：凭据边界与系统存储决策

## 实现范围

`credentials.py` 定义可信服务端凭据绑定、默认关闭的系统后端和固定合成凭据的测试后端。没有 API key 输入框、导入命令、环境变量读取、文件读取、系统库调用或网络出口。真实供应商策略继续硬阻断。

服务器创建 `Binding(alias, provider, origin, model, expires_at)`，客户端不能创建或扩展绑定。测试解析必须精确匹配供应商、HTTPS origin 和模型，时间戳必须有限且未过期。只有 fixture 模式可解析固定无效字符串。真实模式即使配置完整也抛出 `real_credentials_disabled`。

`CredentialLease` 的字符串与 repr 脱敏，后端异常换成固定原因码，不把异常内容回传。它**不是**安全内存容器：同进程 Python 代码仍可读取内部属性，不能抵御本机恶意程序、调试器或内存转储，也不承诺字符串可靠清零。此版本的值不是真实密钥。

## Windows 选择

拟优先使用 Windows Win32 Credential Manager 的 generic credential，不采用随意可替换的文件加密方案。将来 target namespace 应固定为 tokensfree/provider/alias，精确查找而非枚举全部用户凭据；创建/变更需用户批准。持久化策略应显式本机，不能默认为跨设备漫游。**2026-11-18 更新：Windows 原生实现已完成**（`tokensfree/credstore.py`）：generic credential、target 固定为 `tokensfree/<provider>/<alias>`、`CRED_PERSIST_LOCAL_MACHINE`（不漫游）、精确查找不枚举；`scripts/credential_store.py` 提供 set/status/clear；`TOKENSFREE_REAL_API_KEY_STORE` 让运行时直接读取。本机 Windows 已实测写入/读取/删除/缺失 fail-closed，14 项测试覆盖（其他平台自动跳过）。**跨平台（macOS Keychain / Linux Secret Service / keyring）仍未实现**，非 Windows 平台会明确失败而不是回退明文。

Microsoft 文档说明 `CredReadW` 读取当前登录会话凭据集合，`CredWriteW` 创建/修改凭据；`CREDENTIALW` 区分凭据类型和持久化方式。它是 OS 存储保护，不等于每次读取都有用户确认，更不能假定相同登录身份下任意程序都无法访问。

## 跨平台选择

Python `keyring` 提供 Windows、macOS、Linux 后端接口，可减少平台代码，但不得自动选择任意安装的插件或失败后回退明文。拟固定受审查的后端类和依赖版本；Windows 用 WinVault/对应系统后端，macOS 用 Keychain，Linux 用 Secret Service。无已解锁系统服务、无允许的后端或权限失败时必须拒绝，不使用环境变量/配置文件兜底。

本阶段没有安装 keyring，也没有调用系统凭据服务。最终应按目标系统实际测试决定原生 Win32 还是固定 keyring 后端。不能把包的存在视为密钥库安全或可用。

## 联调前必须验收

- 用户批准在指定系统密钥库保存指定平台凭据，固定账户/项目/模型/host 绑定。
- Windows 真实设备测试创建、读取、删除、重启、锁屏、账户切换、访问失败与撤销；Linux/macOS 分别验证，不用模拟替代。
- 不通过命令行参数、日志、网页、环境变量、git 或共享文件传递原始凭据。
- 请求在解析凭据前已通过权限、地区、条款、免费硬边界验证；重定向不可携带凭据；真实请求只允许固定 HTTPS 主机及校验证书。
- 凭据撤销/轮换后未开始任务不能继续使用旧授权；已经发出的请求无法通过本地撤销召回。
- 真实密钥只在必要的本地传输边界短暂存在，错误和观测输出脱敏；系统存储与内存风险分别处理。

## 官方资料（2026-11-17 查阅）

- [Microsoft CredReadW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw)
- [Microsoft CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)
- [Microsoft CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)
- [Python keyring 文档和安全注意事项](https://keyring.readthedocs.io/en/stable/)

当前阶段的产物是可测试的隔离契约与明确的 OS 接入决策，不是可生产凭据管理器。

## 本阶段验收

88 项全套测试通过，其中 11 项凭据边界测试。独立审查发现并修复非字符串后端对象通过重载比较绕过合成值检查的问题。测试验证默认拒绝、作用域、过期、异常脱敏、值类型，以及本模块支持路径不依赖文件/环境变量/socket。测试没有接触真实凭据。
