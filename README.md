# Python 命令行版 Coding Agent（基于 Codex SDK）

这是一个使用 `openai-codex`（Codex Python SDK）实现的命令行 coding agent 示例，满足以下能力：

1. 使用 Codex SDK 接入 Codex 模型  
2. 在命令行展示：用户输入、模型回复、工具调用过程、工具执行结果、权限确认信息、运行状态  
3. 通过开发者指令约束 Agent：遇到需要仓库上下文/执行结果的任务时，主动读文件、搜代码、跑命令并基于结果继续决策  
4. 支持多轮会话上下文，并将用户输入、模型输出、工具调用、工具结果、权限拒绝、错误信息统一写入会话历史  
5. 提供内置命令：帮助、清空会话、查看/切换模型、查看状态、退出  

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
  --history-file .codex_cli_session.jsonl
```

---

## 内置命令

- `/help`：查看帮助  
- `/clear`：清空当前会话（历史 + 线程上下文）  
- `/model`：查看当前模型  
- `/model <name>`：切换当前模型（下一轮生效）  
- `/model list`：列出可用模型  
- `/status`：查看运行状态  
- `/exit` 或 `/quit`：退出程序  

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
