"""自主 Coding Agent 的命令行界面。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from coding_agent.agent import (
    Agent,
    AgentResult,
    DEFAULT_MAX_STEPS,
    MAX_MAX_STEPS,
    MIN_MAX_STEPS,
)
from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEvent, RuntimeEventKind
from coding_agent.llm import LLMClient, ToolCall
from coding_agent.session import SessionStore
from coding_agent.tools.registry import ToolRegistry, create_tool_registry


SYSTEM_PROMPT = """You are Mini Coding Agent, an autonomous programming assistant operating under a bounded local runtime with access to the current workspace.
Use the available tools to inspect the project, search text, create or edit UTF-8 files, and run non-interactive development commands.
Work iteratively using actual tool observations, and avoid repeating an action when it has not produced new information.
Choose actions dynamically from the current conversation instead of following a fixed read-edit-run workflow.
Before modifying an existing file, inspect the relevant code first.
Use edit_file for existing files and write_file only for new files.
Do not use shell operators, start interactive programs, or start background processes.
Package installation, environment mutation, and direct destructive commands are blocked by the local runtime; use the existing project environment.
If a tool reports RepeatedAction, CommandBlocked, or another structured error, change strategy instead of retrying the same request.
Never claim that you inspected a local file unless you actually obtained its content through a tool.
Never claim tests passed unless run_command actually produced evidence that they passed.
A successful file edit does not imply that the code is correct.
After modifying source code or project configuration, verify the latest changes before finishing.
Use an available test, build, or syntax-check command after the most recent modification.
If verification fails, continue repairing the project.
Do not install packages only for verification; use the existing environment.
If only a weaker verification such as syntax checking is available, state that limitation accurately.
After a recognized verification command succeeds and the requested work is complete, return the final answer immediately without extra inspection or cleanup.
If a tool fails or a command exits unsuccessfully, treat the result as an observation and decide whether another action can make progress.
When the task is complete, stop calling tools and return a concise final answer.
Do not continue performing unrelated cleanup or additional exploration after the requested task has been completed."""


def run_cli(
    client: LLMClient | None = None,
    workspace_root: Path | None = None,
    registry: ToolRegistry | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    session_store: SessionStore | None = None,
    resume_session: bool = False,
    save_session: bool = False,
) -> None:
    """运行具有自主工具循环的多轮命令行对话。"""
    workspace = (workspace_root or Path.cwd()).resolve()
    print("Mini Coding Agent")
    print("Stage 7 - Verification and evaluation")
    print()
    _safe_print(f"Workspace: {workspace}")
    _safe_print(f"Max steps per task: {max_steps}")
    print()
    print("Type your message.")
    print("Type /exit to quit.")
    print()

    if client is None:
        try:
            client = LLMClient(Settings.from_env())
        except ConfigurationError as error:
            print(f"Configuration error: {error}")
            return

    store = session_store or SessionStore()
    conversation = Conversation(SYSTEM_PROMPT)
    if resume_session:
        try:
            snapshot = store.load(workspace)
        except ValueError as error:
            _safe_print(f"Saved session could not be restored: {error}")
        else:
            if snapshot is not None:
                conversation = snapshot.to_conversation()
                _safe_print("Restored the latest session for this workspace.")
    tool_registry = registry or create_tool_registry(workspace)
    agent = Agent(
        llm_client=client,
        conversation=conversation,
        tool_registry=tool_registry,
        max_steps=max_steps,
        on_event=_print_runtime_event,
    )

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print("Exiting Mini Coding Agent.")
            return

        if user_input in {"/exit", "/quit"}:
            print("Exiting Mini Coding Agent.")
            return
        if not user_input:
            continue

        result = agent.run(user_input)
        _print_agent_result(result)
        if resume_session or save_session:
            try:
                client_model = getattr(client, "_model", "")
                model = client_model if isinstance(client_model, str) else ""
                store.save(conversation, workspace, model)
            except (OSError, ValueError) as error:
                _safe_print(f"Session save failed: {error}")


def main(argv: list[str] | None = None) -> None:
    """解析参数并启动命令行界面。"""
    parser = argparse.ArgumentParser(description="Mini Coding Agent")
    parser.add_argument(
        "--workspace",
        default=".",
        help="workspace root available to local tools (default: current directory)",
    )
    parser.add_argument(
        "--resume-session",
        action="store_true",
        help="restore and continue the latest session for this workspace",
    )
    parser.add_argument(
        "--save-session",
        action="store_true",
        help="atomically save the conversation after each task",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=(
            "maximum LLM decision steps per task "
            f"(default: {DEFAULT_MAX_STEPS}, range: {MIN_MAX_STEPS}-{MAX_MAX_STEPS})"
        ),
    )
    arguments = parser.parse_args(argv)
    workspace = Path(arguments.workspace).resolve()
    if not workspace.exists():
        parser.error(f"workspace does not exist: {workspace}")
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    if not MIN_MAX_STEPS <= arguments.max_steps <= MAX_MAX_STEPS:
        parser.error(
            f"max-steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}"
        )
    run_cli(
        workspace_root=workspace,
        max_steps=arguments.max_steps,
        resume_session=arguments.resume_session,
        save_session=arguments.save_session,
    )


def _print_tool_call(step_number: int, tool_call: ToolCall) -> None:
    """输出不包含敏感参数内容的工具调用摘要。"""
    _safe_print(f"[step {step_number}] {_format_tool_trace(tool_call)}")


def _print_runtime_event(event: RuntimeEvent) -> None:
    """让 CLI 与 GUI 消费同一种 Runtime Event，同时保持原有输出格式。"""
    payload = event.payload
    if event.kind in {
        RuntimeEventKind.TOOL_STARTED,
        RuntimeEventKind.TOOL_FINISHED,
    }:
        tool_call = ToolCall(
            str(payload.get("tool_call_id", "")),
            str(payload.get("tool_name", "")),
            str(payload.get("arguments", "{}")),
        )
        if event.kind == RuntimeEventKind.TOOL_STARTED:
            _print_tool_call(event.step or 0, tool_call)
        else:
            _print_tool_result(
                event.step or 0,
                tool_call,
                payload.get("result", {}),
            )
    elif event.kind == RuntimeEventKind.LLM_RETRY:
        _print_llm_retry(
            int(payload.get("retry", 0)),
            int(payload.get("max_retries", 0)),
        )


def _print_llm_retry(retry_number: int, max_retries: int) -> None:
    """输出不含请求正文或凭据的 LLM 重试摘要。"""
    _safe_print(
        f"[llm] transient error, retry {retry_number}/{max_retries}"
    )


def _print_tool_result(
    step_number: int,
    tool_call: ToolCall,
    result: dict[str, Any],
) -> None:
    """输出工具结果摘要，不展示完整文件或命令输出。"""
    del step_number
    _safe_print(_format_tool_result_trace(tool_call, result))


def _print_agent_result(result: AgentResult) -> None:
    """根据任务停止原因输出最终文本。"""
    if result.stop_reason == "completed":
        print("Assistant:")
    elif result.stop_reason == "max_steps":
        print("Agent stopped: maximum step limit reached.")
    elif result.stop_reason == "interrupted":
        print("Agent interrupted by user.")
    elif result.stop_reason == "llm_error":
        print("Agent stopped: LLM request failed.")
    elif result.stop_reason == "no_progress":
        print("Agent stopped: no meaningful progress detected.")
    elif result.stop_reason == "verification_required":
        print("Agent stopped: latest code changes still require verification.")
    else:
        print("Agent stopped: invalid model response.")
    _safe_print(result.content)


def _format_tool_trace(tool_call: ToolCall) -> str:
    """构造不包含工具结果或敏感请求数据的简洁轨迹。"""
    try:
        arguments: Any = json.loads(tool_call.arguments)
    except json.JSONDecodeError:
        return f"[tool] {tool_call.name}(<invalid JSON>)"
    if not isinstance(arguments, dict):
        return f"[tool] {tool_call.name}(<invalid arguments>)"

    visible_names = {
        "list_directory": ("path",),
        "read_file": ("path", "start_line", "end_line"),
        "search_text": ("query", "path", "max_results"),
        "write_file": ("path",),
        "edit_file": ("path",),
        "run_command": ("command", "cwd", "timeout_seconds"),
    }.get(tool_call.name, ())
    parts = []
    for name in visible_names:
        if name not in arguments:
            continue
        displayed = repr(arguments[name])
        if len(displayed) > 80:
            displayed = f"{displayed[:77]}..."
        parts.append(f"{name}={displayed}")
    return f"[tool] {tool_call.name}({', '.join(parts)})"


def _format_tool_result_trace(
    tool_call: ToolCall,
    result: dict[str, Any],
) -> str:
    """构造不包含完整工具输出的结果摘要。"""
    if not result.get("success"):
        if result.get("error") == "CommandBlocked":
            return "[result] blocked by runtime policy"
        if result.get("error") == "RepeatedAction":
            return "[warning] repeated action detected"
        return f"[result] error={result.get('error', 'ToolExecutionError')}"

    data = result.get("data", {})
    if tool_call.name == "run_command":
        stdout_length = len(data.get("stdout", ""))
        stderr_length = len(data.get("stderr", ""))
        if data.get("timed_out"):
            return (
                "[result] timed_out=true, "
                f"stdout={stdout_length} chars, stderr={stderr_length} chars"
            )
        return (
            f"[result] exit_code={data.get('exit_code')}, "
            f"stdout={stdout_length} chars, stderr={stderr_length} chars"
        )
    if tool_call.name == "list_directory":
        return f"[result] entries={len(data.get('entries', []))}"
    if tool_call.name == "read_file":
        return (
            f"[result] lines={data.get('start_line')}-{data.get('end_line')}, "
            f"total={data.get('total_lines')}"
        )
    if tool_call.name == "search_text":
        return f"[result] matches={len(data.get('matches', []))}"
    if tool_call.name == "write_file":
        return f"[result] created={data.get('path')}"
    if tool_call.name == "edit_file":
        return f"[result] modified={data.get('path')}"
    return "[result] success=true"


def _safe_print(text: str) -> None:
    """在终端编码无法表示模型字符时安全降级输出。"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        printable = text.encode(encoding, errors="replace").decode(encoding)
        print(printable)
