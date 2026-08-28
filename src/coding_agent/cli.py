"""第二阶段只读工具对话的命令行界面。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, LLMError, LLMResponse, ToolCall
from coding_agent.tools.registry import ToolRegistry, create_tool_registry


SYSTEM_PROMPT = """You are Mini Coding Agent, a programming assistant with read-only access to the current workspace.
You may use the provided tools to inspect directories, read UTF-8 text files, and search literal text.
You cannot modify files or execute commands.
Never claim that you inspected a local file unless you actually obtained its content through a tool."""


class ToolResolutionLimitError(RuntimeError):
    """模型在第二次请求中再次调用工具时抛出的限制异常。"""


def resolve_user_turn(
    client: LLMClient,
    conversation: Conversation,
    registry: ToolRegistry,
) -> str:
    """完成一次用户回合，最多解析一轮只读工具调用。"""
    response = client.complete(conversation.messages, tools=registry.schemas)
    if not response.tool_calls:
        content = _require_content(response)
        conversation.add_assistant_message(content)
        return content

    conversation.add_assistant_tool_calls(response.content, response.tool_calls)
    for tool_call in response.tool_calls:
        _safe_print(_format_tool_trace(tool_call))
        result = registry.execute(tool_call.name, tool_call.arguments)
        conversation.add_tool_result(
            tool_call.id,
            json.dumps(result, ensure_ascii=False),
        )

    final_response = client.complete(conversation.messages, tools=registry.schemas)
    if final_response.tool_calls:
        message = "Stage 2 supports one tool-call round per user turn."
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
    """运行带工作区只读观察能力的多轮对话。"""
    workspace = (workspace_root or Path.cwd()).resolve()
    print("Mini Coding Agent")
    print("Stage 2 - Read-only tools")
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
        help="workspace root available to read-only tools (default: current directory)",
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


def _format_tool_trace(tool_call: ToolCall) -> str:
    """构造不包含工具结果或敏感请求数据的简洁轨迹。"""
    try:
        arguments: Any = json.loads(tool_call.arguments)
    except json.JSONDecodeError:
        return f"[tool] {tool_call.name}(<invalid JSON>)"
    if not isinstance(arguments, dict):
        return f"[tool] {tool_call.name}(<invalid arguments>)"

    parts = []
    for name, value in arguments.items():
        displayed = repr(value)
        if len(displayed) > 80:
            displayed = f"{displayed[:77]}..."
        parts.append(f"{name}={displayed}")
    return f"[tool] {tool_call.name}({', '.join(parts)})"


def _safe_print(text: str) -> None:
    """在终端编码无法表示模型字符时安全降级输出。"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        printable = text.encode(encoding, errors="replace").decode(encoding)
        print(printable)
