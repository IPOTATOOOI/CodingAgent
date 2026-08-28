"""第四阶段文件操作与本地执行对话的命令行界面。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, LLMError, LLMResponse, ToolCall
from coding_agent.tools.registry import ToolRegistry, create_tool_registry


SYSTEM_PROMPT = """You are Mini Coding Agent, a programming assistant with access to the current workspace.
You may inspect project files, search text, create and edit UTF-8 text files, and execute non-interactive development commands using the provided tools.
Before modifying an existing file, inspect the relevant code first.
Use edit_file for existing files and write_file only for new files.
Use run_command when actual execution feedback is necessary, especially for tests, scripts, compilers, and builds.
Do not use shell operators, install packages, start interactive programs, or start background processes.
Never claim that you inspected a local file unless you actually obtained its content through a tool.
Never claim tests passed unless run_command actually produced evidence that they passed.
A successful file edit does not imply that the code is correct.
If execution reveals that additional repair is needed after the tool-round limit, explain the remaining issue instead of claiming success."""


class ToolResolutionLimitError(RuntimeError):
    """模型在第四次请求中再次调用工具时抛出的限制异常。"""


def resolve_user_turn(
    client: LLMClient,
    conversation: Conversation,
    registry: ToolRegistry,
) -> str:
    """完成一次用户回合，最多解析三轮明确受限的工具调用。"""
    first_response = client.complete(conversation.messages, tools=registry.schemas)
    if not first_response.tool_calls:
        content = _require_content(first_response)
        conversation.add_assistant_message(content)
        return content

    _resolve_tool_calls(first_response, conversation, registry)

    second_response = client.complete(conversation.messages, tools=registry.schemas)
    if not second_response.tool_calls:
        content = _require_content(second_response)
        conversation.add_assistant_message(content)
        return content

    _resolve_tool_calls(second_response, conversation, registry)

    third_response = client.complete(conversation.messages, tools=registry.schemas)
    if not third_response.tool_calls:
        content = _require_content(third_response)
        conversation.add_assistant_message(content)
        return content

    _resolve_tool_calls(third_response, conversation, registry)

    final_response = client.complete(conversation.messages, tools=registry.schemas)
    if final_response.tool_calls:
        message = (
            "Stage 4 tool round limit reached. The execution result may require "
            "additional work, but Stage 4 does not support iterative autonomous repair."
        )
        conversation.add_assistant_message(message)
        raise ToolResolutionLimitError(message)

    content = _require_content(final_response)
    conversation.add_assistant_message(content)
    return content


def run_cli(
    client: LLMClient | None = None,
    workspace_root: Path | None = None,
    registry: ToolRegistry | None = None,
) -> None:
    """运行带工作区文件操作与本地执行能力的多轮对话。"""
    workspace = (workspace_root or Path.cwd()).resolve()
    print("Mini Coding Agent")
    print("Stage 4 - Local command execution")
    print()
    _safe_print(f"Workspace: {workspace}")
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

    conversation = Conversation(SYSTEM_PROMPT)
    tool_registry = registry or create_tool_registry(workspace)

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

        conversation.add_user_message(user_input)
        try:
            response = resolve_user_turn(client, conversation, tool_registry)
        except LLMError as error:
            print(f"LLM request failed: {error}")
            continue
        except ToolResolutionLimitError as error:
            print(f"Tool resolution limit: {error}")
            continue

        print("Assistant:")
        _safe_print(response)


def main(argv: list[str] | None = None) -> None:
    """启动命令行界面。"""
    parser = argparse.ArgumentParser(description="Mini Coding Agent")
    parser.add_argument(
        "--workspace",
        default=".",
        help="workspace root available to file tools (default: current directory)",
    )
    arguments = parser.parse_args(argv)
    workspace = Path(arguments.workspace).resolve()
    if not workspace.exists():
        parser.error(f"workspace does not exist: {workspace}")
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    run_cli(workspace_root=workspace)


def _require_content(response: LLMResponse) -> str:
    """从无工具调用的响应中取得必要文本。"""
    if response.content is None:
        raise LLMError("the model returned no text.")
    return response.content


def _resolve_tool_calls(
    response: LLMResponse,
    conversation: Conversation,
    registry: ToolRegistry,
) -> None:
    """按顺序执行一个模型响应中的全部工具调用。"""
    conversation.add_assistant_tool_calls(response.content, response.tool_calls)
    for tool_call in response.tool_calls:
        _safe_print(_format_tool_trace(tool_call))
        result = registry.execute(tool_call.name, tool_call.arguments)
        conversation.add_tool_result(
            tool_call.id,
            json.dumps(result, ensure_ascii=False),
        )
        result_trace = _format_tool_result_trace(tool_call, result)
        if result_trace is not None:
            _safe_print(result_trace)


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
        value = arguments[name]
        displayed = repr(value)
        if len(displayed) > 80:
            displayed = f"{displayed[:77]}..."
        parts.append(f"{name}={displayed}")
    return f"[tool] {tool_call.name}({', '.join(parts)})"


def _format_tool_result_trace(
    tool_call: ToolCall, result: dict[str, Any]
) -> str | None:
    """为命令工具构造不包含完整输出的结果摘要。"""
    if tool_call.name != "run_command":
        return None
    if not result.get("success"):
        return f"[result] error={result.get('error', 'ToolExecutionError')}"

    data = result.get("data", {})
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


def _safe_print(text: str) -> None:
    """在终端编码无法表示模型字符时安全降级输出。"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        printable = text.encode(encoding, errors="replace").decode(encoding)
        print(printable)
