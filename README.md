# Python 命令行版 Coding Agent（基于 Codex SDK）

这是一个使用 `openai-codex`（Codex Python SDK）实现的命令行 coding agent 示例，满足以下能力：

1. 使用 Codex SDK 接入 Codex 模型  
2. 在命令行展示：用户输入、模型回复、工具调用过程、工具执行结果、权限确认信息、运行状态  
3. 通过开发者指令约束 Agent：遇到需要仓库上下文/执行结果的任务时，主动读文件、搜代码、跑命令并基于结果继续决策  
4. 支持多轮会话上下文，并将用户输入、模型输出、工具调用、工具结果、权限拒绝、错误信息统一写入会话历史  
5. 提供内置命令：帮助、清空会话、查看/切换模型、查看状态、退出  
6. 提供基础代码仓库操作能力（并限制在当前 git 仓库根目录内）：目录浏览、文件读取、文件匹配、内容搜索、文件写入/编辑、Shell 执行  
7. 程序启动时自动执行 Codex 登录（可配置登录方式）  

---

## 安装

> 需要 Python 3.10+。

```bash
python3 -m pip install -r requirements.txt
```

---

## 运行

```bash
python3 codex_cli_agent.py
```

可选参数：

```bash
python3 codex_cli_agent.py \
  --model gpt-5.4 \
  --sandbox workspace-write \
  --cwd . \
  --history-file .codex_cli_session.jsonl \
  --login-method auto
```

登录参数：

- `--login-method auto`（默认）：优先复用现有登录态；无登录态时优先尝试 API Key（环境变量），否则走 device-code 登录
- `--login-method api-key`：强制 API Key 登录
- `--login-method device-code`：强制设备码登录
- `--login-method chatgpt`：强制浏览器登录
- `--login-method none`：跳过启动登录
- `--api-key-env OPENAI_API_KEY`：指定 API Key 环境变量名

> 如果网络受限导致登录失败，可设置 API Key 环境变量后使用 `auto/api-key`，或在离线调试时临时使用 `--login-method none`。

---

## 内置命令

- `/help`：查看帮助  
- `/clear`：清空当前会话（历史 + 线程上下文）  
- `/model`：查看当前模型  
- `/model <name>`：切换当前模型（下一轮生效）  
- `/model list`：列出可用模型  
- `/repo ...`：执行仓库工具命令（见下）  
- `/status`：查看运行状态  
- `/exit` 或 `/quit`：退出程序  

### 仓库工具命令（`/repo`）

- `/repo ls [path]`：目录浏览
- `/repo read <file> [max_lines]`：读取文件
- `/repo glob <pattern> [base]`：文件匹配（glob）
- `/repo search <regex> [base] [--ignore-case]`：内容搜索
- `/repo write <file> <content>`：文件写入（覆盖）
- `/repo edit <file> <old> <new> [--all]`：文件编辑（替换）
- `/repo sh <command> [--cwd path]`：执行 Shell 命令

> 注意：`/repo` 的路径解析会被限制在当前 git 仓库根目录下，避免写到仓库外部。

---

## 会话历史

默认写入：`.codex_cli_session.jsonl`（JSONL 格式）。

历史会记录：

- `user_input`
- `assistant_reply`
- `tool_call`
- `tool_result`
- `approval_started` / `approval_completed`
- `permission_denied`
- `error`
- `turn_completed`

这使得后续做审计、重放或调试更容易。
