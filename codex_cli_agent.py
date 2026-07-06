#!/usr/bin/env python3
"""Command-line coding agent powered by OpenAI Codex Python SDK."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from openai_codex import ApprovalMode, Codex, Sandbox
from openai_codex.types import ThreadTokenUsageUpdatedNotification, TurnCompletedNotification


DEFAULT_MODEL = "gpt-5.4"
DEFAULT_HISTORY_FILE = ".codex_cli_session.jsonl"

BASE_DEVELOPER_INSTRUCTIONS = """
你是一个命令行 coding agent。
核心行为要求：
1) 当任务需要仓库上下文时，主动读取文件并搜索代码，不要等待用户逐步指挥。
2) 当任务需要运行结果时，主动执行必要命令，并根据结果继续推进后续动作。
3) 在动手前先做简短说明，执行后给出关键结论和下一步。
4) 对潜在风险操作保持谨慎，必要时请求权限确认并清楚说明原因。
5) 回答保持准确、可执行，并尽量给出验证步骤。
6) 你拥有并应当合理使用基础仓库操作能力：目录浏览、文件读取、文件匹配、内容搜索、文件写入/编辑、Shell 命令执行。
7) 任何代码生成与修改都应落在当前 git 仓库工作区内。
""".strip()

MAX_FILE_READ_LINES = 300
MAX_SEARCH_RESULTS = 120
MAX_SHELL_OUTPUT_CHARS = 8000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(value: Any) -> Any:
    """Best-effort conversion for JSON history logging."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", by_alias=True)
        except TypeError:
            return value.model_dump()
    if hasattr(value, "__dict__"):
        return {k: to_jsonable(v) for k, v in value.__dict__.items()}
    return str(value)


def shorten(text: str | None, max_len: int = 240) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}...(+{len(text) - max_len} chars)"


class SessionHistory:
    """In-memory + JSONL session history store."""

    def __init__(self, path: Path):
        self.path = path
        self.events: list[dict[str, Any]] = []

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        event = {"timestamp": now_iso(), "kind": kind, "payload": to_jsonable(payload)}
        self.events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False) + "\n")

    def clear(self) -> None:
        self.events.clear()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def summary(self) -> str:
        if not self.events:
            return "empty"
        return f"{len(self.events)} events, file={self.path}"


@dataclass
class RuntimeState:
    current_model: str
    status: str = "idle"
    thread_id: str = "-"
    waiting_approval: bool = False
    last_turn_status: str = "-"
    last_error: str | None = None
    last_usage: str = "-"


class RepositoryTools:
    """Basic repository operations restricted to git root."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def resolve_repo_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.repo_root / candidate).resolve()
        if not resolved.is_relative_to(self.repo_root):
            raise ValueError(f"path escapes repo root: {raw_path}")
        return resolved

    def list_directory(self, raw_path: str = ".") -> dict[str, Any]:
        path = self.resolve_repo_path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"not found: {path}")
        if not path.is_dir():
            raise NotADirectoryError(f"not a directory: {path}")

        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        formatted = []
        for entry in entries:
            rel = entry.relative_to(self.repo_root)
            marker = "/" if entry.is_dir() else ""
            formatted.append(f"{rel}{marker}")
        return {"path": str(path), "entries": formatted, "count": len(formatted)}

    def read_file(self, raw_path: str, limit_lines: int = MAX_FILE_READ_LINES) -> dict[str, Any]:
        path = self.resolve_repo_path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"not found: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"is a directory: {path}")

        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        shown = lines[:limit_lines]
        truncated = len(lines) > limit_lines
        numbered = [f"{idx}|{line}" for idx, line in enumerate(shown, start=1)]
        return {
            "path": str(path),
            "line_count": len(lines),
            "content": "\n".join(numbered),
            "truncated": truncated,
        }

    def match_files(self, pattern: str, raw_base: str = ".") -> dict[str, Any]:
        base = self.resolve_repo_path(raw_base)
        if not base.is_dir():
            raise NotADirectoryError(f"not a directory: {base}")
        matches = []
        for path in base.rglob(pattern):
            if ".git" in path.parts:
                continue
            matches.append(str(path.relative_to(self.repo_root)))
        matches.sort()
        return {"pattern": pattern, "base": str(base), "matches": matches, "count": len(matches)}

    def search_content(self, pattern: str, raw_base: str = ".", ignore_case: bool = False) -> dict[str, Any]:
        base = self.resolve_repo_path(raw_base)
        if not base.is_dir():
            raise NotADirectoryError(f"not a directory: {base}")

        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags=flags)
        hits: list[dict[str, Any]] = []

        for path in sorted(base.rglob("*")):
            if len(hits) >= MAX_SEARCH_RESULTS:
                break
            if not path.is_file():
                continue
            if ".git" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(
                        {
                            "path": str(path.relative_to(self.repo_root)),
                            "line": lineno,
                            "text": line,
                        }
                    )
                    if len(hits) >= MAX_SEARCH_RESULTS:
                        break
        return {
            "pattern": pattern,
            "base": str(base),
            "hits": hits,
            "count": len(hits),
            "truncated": len(hits) >= MAX_SEARCH_RESULTS,
        }

    def write_file(self, raw_path: str, content: str) -> dict[str, Any]:
        path = self.resolve_repo_path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"path": str(path), "bytes": len(content.encode("utf-8"))}

    def edit_file(self, raw_path: str, old: str, new: str, replace_all: bool = False) -> dict[str, Any]:
        path = self.resolve_repo_path(raw_path)
        if not path.exists():
            raise FileNotFoundError(f"not found: {path}")
        original = path.read_text(encoding="utf-8", errors="replace")
        if old not in original:
            raise ValueError("old text not found")
        updated = original.replace(old, new) if replace_all else original.replace(old, new, 1)
        path.write_text(updated, encoding="utf-8")
        replacement_count = original.count(old) if replace_all else 1
        return {
            "path": str(path),
            "replace_all": replace_all,
            "replacements": replacement_count,
        }

    def run_shell(self, command: str, raw_cwd: str = ".") -> dict[str, Any]:
        cwd = self.resolve_repo_path(raw_cwd)
        if not cwd.is_dir():
            raise NotADirectoryError(f"not a directory: {cwd}")
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=120,
        )
        output = f"{proc.stdout}{proc.stderr}"
        if len(output) > MAX_SHELL_OUTPUT_CHARS:
            output = f"{output[:MAX_SHELL_OUTPUT_CHARS]}...(+{len(output) - MAX_SHELL_OUTPUT_CHARS} chars)"
        return {"cwd": str(cwd), "command": command, "exit_code": proc.returncode, "output": output}


class CodingAgentCLI:
    def __init__(
        self,
        model: str,
        cwd: Path,
        sandbox: Sandbox,
        history_file: Path,
        developer_instructions: str,
    ) -> None:
        self.cwd = cwd
        self.repo_root = self._detect_repo_root(cwd)
        self.repo_tools = RepositoryTools(self.repo_root)
        self.sandbox = sandbox
        self.state = RuntimeState(current_model=model)
        self.history = SessionHistory(history_file)
        self._developer_instructions = developer_instructions
        self._codex: Codex | None = None
        self._thread = None
        self._assistant_buffer: list[str] = []
        self._assistant_stream_open = False
        self._latest_usage: ThreadTokenUsageUpdatedNotification | None = None

    def start(self) -> None:
        self._codex = Codex()
        self._start_new_thread(clear_history=True)

        print("Codex Coding Agent CLI")
        print(f"cwd> {self.cwd}")
        print(f"repo_root> {self.repo_root}")
        print(f"model> {self.state.current_model}")
        print(f"sandbox> {self.sandbox.value}")
        print(f"history> {self.history.path}")
        print("输入 /help 查看内置命令。")

        self.history.append(
            "session_started",
            {
                "cwd": str(self.cwd),
                "repo_root": str(self.repo_root),
                "model": self.state.current_model,
                "sandbox": self.sandbox.value,
            },
        )

    def close(self) -> None:
        self.state.status = "closing"
        if self._codex is not None:
            self._codex.close()
        self.history.append("session_closed", {"reason": "user_exit"})
        self.state.status = "closed"

    @staticmethod
    def _detect_repo_root(start: Path) -> Path:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
        return start

    def _current_git_branch(self) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            capture_output=True,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        return "-"

    def _start_new_thread(self, clear_history: bool) -> None:
        if self._codex is None:
            raise RuntimeError("Codex client not initialized")

        thread = self._codex.thread_start(
            model=self.state.current_model,
            sandbox=self.sandbox,
            approval_mode=ApprovalMode.auto_review,
            cwd=str(self.cwd),
            developer_instructions=self._developer_instructions,
        )
        self._thread = thread
        self.state.thread_id = thread.id
        self.state.status = "idle"
        self.state.waiting_approval = False
        self.state.last_turn_status = "-"
        self.state.last_error = None
        self.state.last_usage = "-"
        self._assistant_stream_open = False
        self._assistant_buffer = []
        self._latest_usage = None

        if clear_history:
            self.history.clear()
        self.history.append(
            "thread_started",
            {"thread_id": thread.id, "model": self.state.current_model},
        )

    def run_loop(self) -> None:
        while True:
            try:
                user_input = input("\nyou> ").strip()
            except EOFError:
                print("\nEOF received, exiting.")
                break
            except KeyboardInterrupt:
                print("\nInterrupted. 输入 /exit 退出。")
                continue

            if not user_input:
                continue

            if user_input.startswith("/"):
                if not self._handle_builtin_command(user_input):
                    break
                continue

            self._run_turn(user_input)

    def _handle_builtin_command(self, text: str) -> bool:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            print(f"command.error> {exc}")
            self.history.append("error", {"stage": "parse_command", "message": str(exc)})
            return True

        cmd = (parts[0] if parts else "").lower()
        args = parts[1:]
        self.history.append("builtin_command", {"command": cmd, "args": args})

        if cmd in {"/exit", "/quit"}:
            return False

        if cmd == "/help":
            self._print_help()
            return True

        if cmd == "/clear":
            self._start_new_thread(clear_history=True)
            print("status> session cleared, new thread created.")
            return True

        if cmd == "/status":
            self._print_status()
            return True

        if cmd == "/repo":
            self._handle_repo_command(args)
            return True

        if cmd == "/model":
            if not args:
                print(f"model> {self.state.current_model}")
                return True
            if args[0] == "list":
                self._list_models()
                return True

            old_model = self.state.current_model
            self.state.current_model = args[0]
            print(f"model> switched: {old_model} -> {self.state.current_model}")
            self.history.append(
                "model_switched",
                {"old_model": old_model, "new_model": self.state.current_model},
            )
            return True

        print(f"command.error> unknown command: {cmd}. 输入 /help 查看帮助。")
        self.history.append("error", {"stage": "builtin_command", "message": f"unknown {cmd}"})
        return True

    def _print_help(self) -> None:
        print(
            """
内置命令:
  /help                查看帮助
  /clear               清空当前会话（历史 + 线程上下文）
  /model               查看当前模型
  /model <name>        切换当前模型（下一轮生效）
  /model list          列出当前可用模型
  /repo ...            仓库工具（ls/read/glob/search/write/edit/sh）
  /status              查看当前运行状态
  /exit  或 /quit      退出程序

/repo 子命令:
  /repo ls [path]
  /repo read <file> [max_lines]
  /repo glob <pattern> [base]
  /repo search <regex> [base] [--ignore-case]
  /repo write <file> <content>
  /repo edit <file> <old> <new> [--all]
  /repo sh <command> [--cwd path]
""".strip()
        )

    def _print_status(self) -> None:
        print("status>")
        print(f"  state: {self.state.status}")
        print(f"  repo_root: {self.repo_root}")
        print(f"  git_branch: {self._current_git_branch()}")
        print(f"  model: {self.state.current_model}")
        print(f"  thread: {self.state.thread_id}")
        print(f"  waiting_approval: {self.state.waiting_approval}")
        print(f"  last_turn_status: {self.state.last_turn_status}")
        print(f"  last_error: {self.state.last_error or '-'}")
        print(f"  last_usage: {self.state.last_usage}")
        print(f"  session_history: {self.history.summary()}")

    def _handle_repo_command(self, args: list[str]) -> None:
        if not args:
            print("repo.error> missing subcommand, use /help")
            self.history.append("error", {"stage": "repo_command", "message": "missing subcommand"})
            return

        sub = args[0].lower()
        call_info: dict[str, Any] = {"tool": sub, "args": args[1:]}
        self.history.append("repo_tool_call", call_info)

        try:
            if sub == "ls":
                target = args[1] if len(args) >= 2 else "."
                result = self.repo_tools.list_directory(target)
                print(f"repo.ls> {result['count']} entries")
                for entry in result["entries"]:
                    print(f"  {entry}")
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                return

            if sub == "read":
                if len(args) < 2:
                    raise ValueError("usage: /repo read <file> [max_lines]")
                max_lines = int(args[2]) if len(args) >= 3 else MAX_FILE_READ_LINES
                result = self.repo_tools.read_file(args[1], max_lines)
                print(f"repo.read> {result['path']} ({result['line_count']} lines)")
                if result["content"]:
                    print(result["content"])
                if result["truncated"]:
                    print(f"... truncated to {max_lines} lines")
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                return

            if sub == "glob":
                if len(args) < 2:
                    raise ValueError("usage: /repo glob <pattern> [base]")
                base = args[2] if len(args) >= 3 else "."
                result = self.repo_tools.match_files(args[1], base)
                print(f"repo.glob> {result['count']} matches")
                for item in result["matches"]:
                    print(f"  {item}")
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                return

            if sub == "search":
                if len(args) < 2:
                    raise ValueError("usage: /repo search <regex> [base] [--ignore-case]")
                ignore_case = "--ignore-case" in args
                filtered = [x for x in args[1:] if x != "--ignore-case"]
                pattern = filtered[0]
                base = filtered[1] if len(filtered) >= 2 else "."
                result = self.repo_tools.search_content(pattern, base, ignore_case=ignore_case)
                print(f"repo.search> {result['count']} hits")
                for hit in result["hits"]:
                    print(f"  {hit['path']}:{hit['line']}:{hit['text']}")
                if result["truncated"]:
                    print(f"... truncated to first {MAX_SEARCH_RESULTS} hits")
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                return

            if sub == "write":
                if len(args) < 3:
                    raise ValueError("usage: /repo write <file> <content>")
                target = args[1]
                content = " ".join(args[2:])
                result = self.repo_tools.write_file(target, content)
                print(f"repo.write> wrote {result['bytes']} bytes to {result['path']}")
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                return

            if sub == "edit":
                if len(args) < 4:
                    raise ValueError("usage: /repo edit <file> <old> <new> [--all]")
                replace_all = "--all" in args[4:]
                result = self.repo_tools.edit_file(args[1], args[2], args[3], replace_all=replace_all)
                print(
                    f"repo.edit> {result['path']} replacements={result['replacements']} "
                    f"(replace_all={result['replace_all']})"
                )
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                return

            if sub == "sh":
                if len(args) < 2:
                    raise ValueError("usage: /repo sh <command> [--cwd path]")
                command_tokens = args[1:]
                cwd = "."
                if "--cwd" in command_tokens:
                    idx = command_tokens.index("--cwd")
                    if idx + 1 >= len(command_tokens):
                        raise ValueError("usage: /repo sh <command> [--cwd path]")
                    cwd = command_tokens[idx + 1]
                    command_tokens = command_tokens[:idx]
                if not command_tokens:
                    raise ValueError("shell command cannot be empty")
                command = " ".join(command_tokens)
                result = self.repo_tools.run_shell(command, cwd)
                print(f"repo.sh> exit={result['exit_code']} cwd={result['cwd']}")
                if result["output"]:
                    print(result["output"])
                self.history.append("repo_tool_result", {"tool": sub, "result": result})
                if result["exit_code"] != 0:
                    self.history.append(
                        "error",
                        {
                            "stage": "repo_shell",
                            "message": f"non-zero exit: {result['exit_code']}",
                            "command": command,
                        },
                    )
                return

            raise ValueError(f"unknown repo subcommand: {sub}")
        except Exception as exc:  # noqa: BLE001
            print(f"repo.error> {exc}")
            self.history.append(
                "error",
                {"stage": "repo_command", "tool": sub, "message": str(exc), "type": type(exc).__name__},
            )

    def _list_models(self) -> None:
        if self._codex is None:
            print("model.error> codex client not initialized")
            return
        try:
            response = self._codex.models()
            data = getattr(response, "data", None) or []
            if not data:
                print("model.list> (empty)")
                return
            print("model.list>")
            for item in data:
                item_id = getattr(item, "id", None)
                if item_id:
                    print(f"  - {item_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"model.error> {exc}")
            self.history.append("error", {"stage": "model_list", "message": str(exc)})

    def _run_turn(self, user_text: str) -> None:
        if self._thread is None:
            raise RuntimeError("Thread not initialized")

        self.history.append("user_input", {"text": user_text})
        self.state.status = "running"
        self.state.waiting_approval = False
        self.state.last_error = None
        self._assistant_buffer = []
        self._assistant_stream_open = False
        self._latest_usage = None

        print(f"run.status> running (model={self.state.current_model})")
        print("assistant> ", end="", flush=True)

        try:
            turn = self._thread.turn(
                user_text,
                model=self.state.current_model,
                sandbox=self.sandbox,
                approval_mode=ApprovalMode.auto_review,
                cwd=str(self.cwd),
            )
            self.history.append("turn_requested", {"input": user_text})

            for event in turn.stream():
                self._handle_event(event)
        except Exception as exc:  # noqa: BLE001
            if self._assistant_stream_open:
                print()
            self.state.status = "idle"
            self.state.last_turn_status = "failed"
            self.state.last_error = str(exc)
            print(f"assistant.error> {exc}")
            self.history.append(
                "error",
                {"stage": "turn_stream", "message": str(exc), "type": type(exc).__name__},
            )
            return

        if self._assistant_stream_open:
            print()
        assistant_text = "".join(self._assistant_buffer).strip()
        self.history.append("assistant_reply", {"text": assistant_text})

        self.state.status = "idle"
        if self._latest_usage is not None:
            usage_text = self._format_usage(self._latest_usage.token_usage)
            self.state.last_usage = usage_text
            print(f"assistant.usage> {usage_text}")
            self.history.append("usage", {"text": usage_text})

        print(f"run.status> {self.state.last_turn_status}")

    def _handle_event(self, event: Any) -> None:
        method = getattr(event, "method", "")
        payload = getattr(event, "payload", None)

        if method == "item/agentMessage/delta":
            delta = getattr(payload, "delta", "")
            if delta:
                self._assistant_stream_open = True
                self._assistant_buffer.append(delta)
                print(delta, end="", flush=True)
            return

        if method == "item/started":
            self._handle_item_started(payload)
            return

        if method == "item/completed":
            self._handle_item_completed(payload)
            return

        if method == "item/commandExecution/outputDelta":
            delta = getattr(payload, "delta", "")
            if delta:
                print(f"\n[tool-output] {delta}", end="", flush=True)
                self.history.append(
                    "tool_stream",
                    {"tool": "commandExecution", "item_id": getattr(payload, "item_id", None), "delta": delta},
                )
            return

        if method == "item/fileChange/outputDelta":
            delta = getattr(payload, "delta", "")
            if delta:
                print(f"\n[file-change] {delta}", end="", flush=True)
                self.history.append(
                    "tool_stream",
                    {"tool": "fileChange", "item_id": getattr(payload, "item_id", None), "delta": delta},
                )
            return

        if method == "item/mcpToolCall/progress":
            message = getattr(payload, "message", "")
            print(f"\n[mcp-progress] {message}")
            self.history.append(
                "tool_progress",
                {"tool": "mcpToolCall", "item_id": getattr(payload, "item_id", None), "message": message},
            )
            return

        if method == "item/commandExecution/terminalInteraction":
            stdin = getattr(payload, "stdin", "")
            process_id = getattr(payload, "process_id", "")
            print(f"\n[terminal-interaction] process={process_id} input={stdin!r}")
            self.history.append(
                "terminal_interaction",
                {"process_id": process_id, "stdin": stdin, "item_id": getattr(payload, "item_id", None)},
            )
            return

        if method == "item/autoApprovalReview/started":
            self.state.waiting_approval = True
            self._log_approval_started(payload)
            return

        if method == "item/autoApprovalReview/completed":
            self.state.waiting_approval = False
            self._log_approval_completed(payload)
            return

        if isinstance(payload, ThreadTokenUsageUpdatedNotification):
            self._latest_usage = payload
            return

        if isinstance(payload, TurnCompletedNotification):
            status = payload.turn.status.value
            self.state.last_turn_status = status
            self.history.append("turn_completed", {"status": status, "turn": payload.turn})
            if status == "failed":
                message = getattr(getattr(payload.turn, "error", None), "message", None) or "unknown error"
                self.state.last_error = message
                self.history.append("error", {"stage": "turn_completed", "message": message})
                print(f"\nassistant.error> {message}")
            return

        self.history.append(
            "event",
            {"method": method, "payload": payload},
        )

    def _handle_item_started(self, payload: Any) -> None:
        root = getattr(getattr(payload, "item", None), "root", None)
        if root is None:
            self.history.append("event", {"method": "item/started", "payload": payload})
            return

        item_type = getattr(root, "type", "unknown")
        item_id = getattr(root, "id", None)
        info = {"item_type": item_type, "item_id": item_id}

        if item_type == "commandExecution":
            info.update({"command": getattr(root, "command", ""), "cwd": str(getattr(root, "cwd", ""))})
            print(f"\n[tool-call] shell: {info['command']} (cwd={info['cwd']})")
        elif item_type == "mcpToolCall":
            info.update(
                {
                    "server": getattr(root, "server", ""),
                    "tool": getattr(root, "tool", ""),
                    "arguments": getattr(root, "arguments", None),
                }
            )
            print(f"\n[tool-call] mcp: {info['server']}::{info['tool']} args={to_jsonable(info['arguments'])}")
        elif item_type == "webSearch":
            info.update({"query": getattr(root, "query", "")})
            print(f"\n[tool-call] web_search: {info['query']}")
        elif item_type == "dynamicToolCall":
            info.update({"tool": getattr(root, "tool", ""), "arguments": getattr(root, "arguments", None)})
            print(f"\n[tool-call] dynamic_tool: {info['tool']} args={to_jsonable(info['arguments'])}")
        elif item_type == "fileChange":
            print("\n[tool-call] file_change: applying patch")

        self.history.append("tool_call", info)

    def _handle_item_completed(self, payload: Any) -> None:
        root = getattr(getattr(payload, "item", None), "root", None)
        if root is None:
            self.history.append("event", {"method": "item/completed", "payload": payload})
            return

        item_type = getattr(root, "type", "unknown")
        item_id = getattr(root, "id", None)
        result: dict[str, Any] = {"item_type": item_type, "item_id": item_id}

        if item_type == "agentMessage":
            text = getattr(root, "text", "")
            # When no delta stream appears (rare), fallback to completed message text.
            if text and not self._assistant_buffer:
                self._assistant_buffer.append(text)
                print(text, end="", flush=True)
            return

        if item_type == "commandExecution":
            status = getattr(getattr(root, "status", None), "value", getattr(root, "status", "unknown"))
            exit_code = getattr(root, "exit_code", None)
            aggregated = getattr(root, "aggregated_output", None)
            result.update(
                {
                    "status": status,
                    "exit_code": exit_code,
                    "duration_ms": getattr(root, "duration_ms", None),
                    "aggregated_output": aggregated,
                }
            )
            print(
                "\n[tool-result] shell "
                f"status={status} exit={exit_code} duration_ms={result.get('duration_ms')}"
            )
            if aggregated:
                print(f"[tool-result.output] {shorten(aggregated)}")
            if status == "declined":
                reason = "command execution declined (likely permission denied)"
                print(f"[approval-denied] {reason}")
                self.history.append("permission_denied", {"reason": reason, "item_id": item_id})

        elif item_type == "mcpToolCall":
            status = getattr(getattr(root, "status", None), "value", getattr(root, "status", "unknown"))
            result.update(
                {
                    "status": status,
                    "server": getattr(root, "server", ""),
                    "tool": getattr(root, "tool", ""),
                    "error": getattr(root, "error", None),
                    "result": getattr(root, "result", None),
                }
            )
            print(f"\n[tool-result] mcp {result['server']}::{result['tool']} status={status}")
            if result["error"]:
                print(f"[tool-result.error] {shorten(str(to_jsonable(result['error'])))}")

        elif item_type == "fileChange":
            status = getattr(getattr(root, "status", None), "value", getattr(root, "status", "unknown"))
            result.update({"status": status, "changes": getattr(root, "changes", [])})
            change_count = len(result["changes"]) if isinstance(result["changes"], list) else "?"
            print(f"\n[tool-result] file_change status={status} changes={change_count}")

        elif item_type == "dynamicToolCall":
            status = getattr(getattr(root, "status", None), "value", getattr(root, "status", "unknown"))
            result.update(
                {
                    "status": status,
                    "tool": getattr(root, "tool", ""),
                    "duration_ms": getattr(root, "duration_ms", None),
                    "success": getattr(root, "success", None),
                }
            )
            print(f"\n[tool-result] dynamic_tool {result['tool']} status={status}")

        elif item_type == "webSearch":
            action = getattr(root, "action", None)
            result.update({"query": getattr(root, "query", ""), "action": action})
            print(f"\n[tool-result] web_search query={result['query']}")

        self.history.append("tool_result", result)

    def _log_approval_started(self, payload: Any) -> None:
        action_obj = getattr(getattr(payload, "action", None), "root", None)
        action_type = getattr(action_obj, "type", "unknown")
        msg = f"[approval] review started: action={action_type}"
        command = getattr(action_obj, "command", None)
        if command:
            msg += f", command={command}"
        print(f"\n{msg}")

        self.history.append(
            "approval_started",
            {
                "review_id": getattr(payload, "review_id", None),
                "action_type": action_type,
                "action": action_obj,
                "review": getattr(payload, "review", None),
                "target_item_id": getattr(payload, "target_item_id", None),
            },
        )

    def _log_approval_completed(self, payload: Any) -> None:
        review = getattr(payload, "review", None)
        status = getattr(getattr(review, "status", None), "value", getattr(review, "status", "unknown"))
        rationale = getattr(review, "rationale", None)
        risk_level = getattr(getattr(review, "risk_level", None), "value", getattr(review, "risk_level", None))

        print(f"\n[approval] review completed: status={status}, risk={risk_level}, rationale={shorten(rationale, 160)}")
        self.history.append(
            "approval_completed",
            {
                "review_id": getattr(payload, "review_id", None),
                "status": status,
                "decision_source": getattr(getattr(payload, "decision_source", None), "root", None),
                "review": review,
                "target_item_id": getattr(payload, "target_item_id", None),
            },
        )
        if status in {"denied", "timedOut", "aborted"}:
            self.history.append(
                "permission_denied",
                {
                    "status": status,
                    "review_id": getattr(payload, "review_id", None),
                    "rationale": rationale,
                },
            )

    @staticmethod
    def _format_usage(token_usage: Any) -> str:
        if token_usage is None:
            return "-"
        last = getattr(token_usage, "last", None)
        total = getattr(token_usage, "total", None)
        if last is None or total is None:
            return str(to_jsonable(token_usage))
        return (
            "last("
            f"in={getattr(last, 'input_tokens', '?')}, "
            f"out={getattr(last, 'output_tokens', '?')}, "
            f"reason={getattr(last, 'reasoning_output_tokens', '?')}, "
            f"cached={getattr(last, 'cached_input_tokens', '?')}, "
            f"total={getattr(last, 'total_tokens', '?')}) "
            "total("
            f"in={getattr(total, 'input_tokens', '?')}, "
            f"out={getattr(total, 'output_tokens', '?')}, "
            f"reason={getattr(total, 'reasoning_output_tokens', '?')}, "
            f"cached={getattr(total, 'cached_input_tokens', '?')}, "
            f"total={getattr(total, 'total_tokens', '?')})"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLI coding agent powered by OpenAI Codex SDK")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model to use (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--sandbox",
        default="workspace-write",
        choices=["read-only", "workspace-write", "full-access"],
        help="Sandbox mode for Codex tool execution",
    )
    parser.add_argument("--cwd", default=".", help="Working directory passed to Codex thread")
    parser.add_argument(
        "--history-file",
        default=DEFAULT_HISTORY_FILE,
        help=f"JSONL session history file (default: {DEFAULT_HISTORY_FILE})",
    )
    parser.add_argument(
        "--extra-instructions",
        default="",
        help="Extra developer instructions appended to the default behavior contract",
    )
    return parser.parse_args()


def sandbox_from_arg(raw: str) -> Sandbox:
    mapping = {
        "read-only": Sandbox.read_only,
        "workspace-write": Sandbox.workspace_write,
        "full-access": Sandbox.full_access,
    }
    return mapping[raw]


def main() -> int:
    args = parse_args()
    cwd = Path(args.cwd).expanduser().resolve()
    history_file = Path(args.history_file).expanduser().resolve()
    developer_instructions = BASE_DEVELOPER_INSTRUCTIONS
    if args.extra_instructions:
        developer_instructions = f"{developer_instructions}\n\n额外约束:\n{args.extra_instructions.strip()}"

    app = CodingAgentCLI(
        model=args.model,
        cwd=cwd,
        sandbox=sandbox_from_arg(args.sandbox),
        history_file=history_file,
        developer_instructions=developer_instructions,
    )
    try:
        app.start()
        app.run_loop()
    except Exception as exc:  # noqa: BLE001
        print(f"fatal> {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            app.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
