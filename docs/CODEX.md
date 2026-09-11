# 使用已登录的 Codex 模型

这是供本人使用的本机客户端，通过官方 `codex exec` 调用模型。它不把当前聊天窗口变成 API，也不读取、复制或提交登录令牌。使用 ChatGPT 登录时调用计入该账号的 Codex 额度；不是无限免费 API。

## 启动

需要 Python 3.11+、官方 Codex CLI，以及可用的模型权限。先停止占用 8765 端口的旧应用实例，然后在仓库目录运行：

```powershell
codex login status
# 如未登录，执行 codex login，在官方浏览器流程中登录。
.\start-codex.ps1
```

打开 http://127.0.0.1:8765，在问答或会议页选择「真实模型」。脚本默认 `gpt-6-astra`，权限或额度不足会明确失败。普通 `start.ps1` 不主动开启模型，但会继承当前终端已经设置的环境变量；需要离线运行时先设置 `$env:LLM_ENABLED='0'`。

如果 CLI 不在 PATH，先把 `$env:CODEX_BIN` 设为官方 `codex.exe` 的完整路径。要指定账号可用的其他模型，可在启动前自行设置 `LLM_PROVIDER=codex`、`LLM_MODEL`、`LLM_ENABLED=1` 后执行普通 `start.ps1`。

真实评测使用同样配置：

```powershell
$env:LLM_PROVIDER = 'codex'
$env:LLM_MODEL = 'gpt-6-astra'
$env:LLM_ENABLED = '1'
.\.venv\Scripts\python.exe evaluate.py --live --allow-model-calls
```

## 调用边界

- 仅绑定回环地址，不公开此入口、不共享本人登录状态。公网或多用户版本应接独立模型服务。
- 每次创建临时会话，忽略用户 config.toml，从 `data/codex` 工作目录执行；不复用历史问答。关闭项目文档注入和主机技能发现，不把 WORK2 或应用数据库作为模型上下文。
- 使用只读沙箱和拒绝提权策略；关闭终端执行、图片文件读取、浏览器、电脑操作、插件/连接器、网页搜索、子代理、hooks、记忆等能力，并禁用 Code Mode host。结果流若出现工具执行等意外事件则拒绝输出。此配置依赖官方 CLI 的实现，不宣称能安全执行任意不可信代码。
- 参数通过 Python subprocess 参数数组传入，问题和资料走 stdin，没有 shell 字符串拼接。应用不读取凭据，也不把 CLI stderr 返回前端。
- 单次进程限时 55 秒；应用没有自动重试。CLI 自身可能处理网络重连。进程失败或超时也可能已经消耗额度。
- 同一服务进程仅同时运行一次 Codex 推理；忙时返回 429。不要用多个后端 worker 绕开本机限制。
- 该路线比直接模型 API 多一层 CLI 启动与系统提示开销，适合单人演示。升级 CLI 后应重新运行契约测试与真实小样本；未知配置会失败，不静默放宽权限。
- 引用逐字核验、负责人/日期核验和人工确认流程与 HTTP 模型路线共用。模型仍可能给出语义错误；生成草稿不会直接创建待办。

2026-09-12 已在本机以 ChatGPT 登录运行 `gpt-6-astra`，获取真实 JSON 与 usage。完整应用评测另见 `reports/live-*.json`；自动化契约测试中的替身不计入模型表现。

参考：[官方非交互调用说明](https://learn.chatgpt.com/docs/non-interactive-mode)、[官方 SDK 说明](https://learn.chatgpt.com/docs/codex-sdk)、[登录方式与额度归属](https://learn.chatgpt.com/docs/auth)。
