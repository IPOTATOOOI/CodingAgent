"""把底层 Runtime 事件转换为普通用户也能理解的执行说明。"""

from dataclasses import dataclass
import difflib
import json
from typing import Any

from coding_agent.llm import ToolCall


TOOL_LABELS = {
    "read_file": "Read",
    "search_text": "Search",
    "list_directory": "List",
    "write_file": "Create",
    "edit_file": "Edit",
    "run_command": "Run",
}


@dataclass(frozen=True)
class TracePresentation:
    """一条兼顾用户说明和技术详情的 Trace 展示数据。"""

    label: str
    title: str
    summary: str
    details: str
    tone: str = "normal"
    preview: str = ""


def format_tool_call(tool_call: ToolCall) -> TracePresentation:
    """解释 Agent 为什么执行当前工具，同时隐藏文件正文和替换文本。"""
    label = TOOL_LABELS.get(tool_call.name, tool_call.name)
    arguments = _arguments(tool_call.arguments)
    if arguments is None:
        return TracePresentation(
            label,
            "无法解析这一步",
            "模型提供的工具参数格式不正确，Runtime 将返回错误并要求调整。",
            "工具参数不是有效的 JSON 对象。",
            "error",
        )

    path = str(arguments.get("path", "."))
    if tool_call.name == "list_directory":
        title = "浏览项目文件" if path == "." else f"查看目录 {path}"
        summary = "了解项目结构，确定接下来需要检查哪些文件。"
    elif tool_call.name == "read_file":
        title = f"读取文件 {path}"
        summary = "查看实际代码内容，为定位问题或准备修改收集依据。"
    elif tool_call.name == "search_text":
        query = _bounded(str(arguments.get("query", "")), 60)
        location = "整个项目" if path == "." else path
        title = "在项目中搜索代码"
        summary = f"在 {location} 中查找“{query}”，快速定位相关实现。"
    elif tool_call.name == "write_file":
        title = f"创建新文件 {path}"
        summary = "为任务添加一个新的项目文件；现有文件不会被覆盖。"
    elif tool_call.name == "edit_file":
        title = f"修改文件 {path}"
        summary = "替换已经定位到的代码片段，落实本轮修复或实现。"
    elif tool_call.name == "run_command":
        command = _command(arguments)
        kind = _command_kind(command)
        title, purpose = {
            "test": ("运行项目测试", "用真实测试结果检查当前实现是否正确。"),
            "syntax": ("检查代码语法", "确认最近修改的代码可以被解释器正常解析。"),
            "build": ("构建项目", "运行项目构建流程，检查代码能否成功生成产物。"),
            "version": ("检查开发环境", "确认所需命令或运行时在当前环境中可用。"),
            "command": ("运行开发命令", "执行项目命令，并根据真实输出决定下一步。"),
        }[kind]
        command_text = _bounded(" ".join(command) or "<empty command>", 120)
        summary = f"{purpose} 命令：{command_text}"
    else:
        title = f"执行工具 {tool_call.name}"
        summary = "Agent 正在调用 Runtime 提供的项目工具。"

    visible = {
        key: value
        for key, value in arguments.items()
        if key not in {"content", "old_text", "new_text"}
    }
    details = (
        f"工具：{label} ({tool_call.name})\n"
        f"参数：\n{json.dumps(visible, ensure_ascii=False, indent=2)}"
    )
    return TracePresentation(label, title, summary, details, "info")


def format_tool_result(
    tool_call: ToolCall,
    result: dict[str, Any],
) -> TracePresentation:
    """解释执行结果对任务的意义，并仅保留有界技术输出。"""
    label = TOOL_LABELS.get(tool_call.name, tool_call.name)
    if not result.get("success"):
        error = str(result.get("error", "ToolExecutionError"))
        message = _bounded(str(result.get("message", "")), 700)
        title, summary, tone = _friendly_error(error)
        details = f"错误类型：{error}\n\nRuntime 信息：\n{message or error}"
        return TracePresentation(label, title, summary, details, tone)

    data = result.get("data", {})
    if not isinstance(data, dict):
        return TracePresentation(
            label,
            "操作已完成",
            "Runtime 报告该操作执行成功。",
            "success=true",
            "success",
        )

    arguments = _arguments(tool_call.arguments) or {}
    path = str(data.get("path") or arguments.get("path", "."))
    if tool_call.name == "run_command":
        return _format_command_result(label, arguments, data)
    if tool_call.name == "list_directory":
        count = len(data.get("entries", []))
        title = "目录查看完成"
        summary = f"发现 {count} 个文件或目录，Agent 将从中选择相关内容继续检查。"
    elif tool_call.name == "read_file":
        title = "文件读取完成"
        start = data.get("start_line")
        end = data.get("end_line")
        summary = f"已读取 {path} 的第 {start}–{end} 行，Agent 可以据此分析代码。"
    elif tool_call.name == "search_text":
        count = len(data.get("matches", []))
        if count:
            title = "已找到相关代码"
            summary = f"找到 {count} 处匹配，Agent 将检查这些位置。"
        else:
            title = "没有找到匹配内容"
            summary = "当前关键词没有结果，Agent 需要调整搜索方式。"
    elif tool_call.name == "write_file":
        title = "新文件创建成功"
        summary = f"已经创建 {path}；如果它属于代码，后续还需要运行验证。"
        preview, change_details = _create_preview(arguments, path)
    elif tool_call.name == "edit_file":
        title = "代码修改成功"
        location = _edit_location(data)
        summary = (
            f"已经修改 {path}{location}；这只代表写入成功，仍需通过测试或语法检查。"
        )
        preview, change_details = _edit_diff(arguments, path, data)
    else:
        title = "操作已完成"
        summary = "Runtime 报告该操作执行成功。"

    details = _bounded(json.dumps(data, ensure_ascii=False, indent=2), 2400)
    if tool_call.name in {"write_file", "edit_file"}:
        details = f"{details}\n\n修改内容\n{change_details}"
    return TracePresentation(label, title, summary, details, "success", preview)


def _format_command_result(
    label: str,
    arguments: dict[str, Any],
    data: dict[str, Any],
) -> TracePresentation:
    command = _command(arguments)
    kind = _command_kind(command)
    exit_code = data.get("exit_code")
    timed_out = bool(data.get("timed_out"))
    cancelled = bool(data.get("cancelled"))
    if cancelled:
        title = "命令已停止"
        summary = "用户请求停止任务，Runtime 已终止当前命令。"
        tone = "warning"
    elif timed_out:
        title = "命令执行超时"
        summary = "命令没有在规定时间内结束，Agent 将根据已有输出调整策略。"
        tone = "error"
    elif exit_code == 0 and kind == "test":
        title = "项目测试通过"
        summary = "测试成功，为当前代码状态提供了真实的通过证据。"
        tone = "success"
    elif exit_code != 0 and kind == "test":
        title = "项目测试失败"
        summary = f"测试发现问题（退出码 {exit_code}），Agent 将根据错误继续定位和修复。"
        tone = "error"
    elif exit_code == 0 and kind == "syntax":
        title = "语法检查通过"
        summary = "最近修改的代码能够被解释器正常解析。"
        tone = "success"
    elif exit_code != 0 and kind == "syntax":
        title = "语法检查失败"
        summary = f"代码仍有语法问题（退出码 {exit_code}），需要继续修复。"
        tone = "error"
    elif exit_code == 0:
        title = "命令执行成功"
        summary = "命令正常结束，Agent 将使用这个结果判断下一步。"
        tone = "success"
    else:
        title = "命令执行失败"
        summary = f"命令返回退出码 {exit_code}；这是一条观察结果，Agent 可以继续处理。"
        tone = "error"

    command_text = " ".join(command) or "<empty command>"
    details = (
        f"命令：{command_text}\n"
        f"工作目录：{data.get('cwd', arguments.get('cwd', '.'))}\n"
        f"退出码：{exit_code}\n"
        f"是否超时：{'是' if timed_out else '否'}\n\n"
        f"是否由用户停止：{'是' if cancelled else '否'}\n\n"
        f"标准输出预览：\n{_bounded(str(data.get('stdout', '')), 1200)}\n\n"
        f"错误输出预览：\n{_bounded(str(data.get('stderr', '')), 1200)}"
    )
    return TracePresentation(label, title, summary, details, tone)


def _friendly_error(error: str) -> tuple[str, str, str]:
    messages = {
        "CommandBlocked": (
            "命令已被安全策略阻止",
            "Runtime 在进程启动前拦截了不允许的命令，Agent 需要改用安全方案。",
            "warning",
        ),
        "RepeatedAction": (
            "检测到重复操作",
            "相同操作连续出现且没有带来新信息，Runtime 要求 Agent 调整策略。",
            "warning",
        ),
        "Cancelled": (
            "操作已取消",
            "用户请求停止任务，后续尚未开始的工具操作已跳过。",
            "warning",
        ),
        "FileNotFound": (
            "目标文件不存在",
            "Agent 使用的路径没有对应文件，需要重新查看项目结构。",
            "error",
        ),
        "FileAlreadyExists": (
            "文件已经存在",
            "创建操作没有覆盖现有文件，Agent 应改用 Edit 或选择新路径。",
            "warning",
        ),
        "PathOutsideWorkspace": (
            "操作超出 Workspace",
            "安全边界拒绝了 Workspace 之外的路径。",
            "warning",
        ),
    }
    return messages.get(
        error,
        (
            "这一步没有完成",
            f"Runtime 返回 {error}，Agent 会把它作为观察结果并决定是否继续。",
            "error",
        ),
    )


def _edit_diff(
    arguments: dict[str, Any],
    path: str,
    data: dict[str, Any],
) -> tuple[str, str]:
    old_text = str(arguments.get("old_text", ""))
    new_text = str(arguments.get("new_text", ""))
    raw_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"{path}（修改前）",
            tofile=f"{path}（修改后）",
            lineterm="",
        )
    )
    body = [line for line in raw_lines if not line.startswith("@@")]
    location = _edit_location(data).lstrip("，") or "修改片段"
    lines = [location, *body]
    return (
        _bounded_diff(lines, max_lines=9, max_chars=1200),
        _bounded_diff(lines, max_lines=80, max_chars=8000),
    )


def _create_preview(
    arguments: dict[str, Any],
    path: str,
) -> tuple[str, str]:
    content = str(arguments.get("content", ""))
    lines = [f"{path}：原文件不存在 → 创建新文件"]
    lines.extend(f"+ {line}" for line in content.splitlines())
    if not content:
        lines.append("+ <空文件>")
    return (
        _bounded_diff(lines, max_lines=9, max_chars=1200),
        _bounded_diff(lines, max_lines=80, max_chars=8000),
    )


def _edit_location(data: dict[str, Any]) -> str:
    start = data.get("start_line")
    old_end = data.get("old_end_line")
    new_end = data.get("new_end_line")
    if not all(isinstance(value, int) for value in (start, old_end, new_end)):
        return ""
    if new_end < start:
        return f"，删除第 {start}–{old_end} 行"
    old_range = str(start) if old_end == start else f"{start}–{old_end}"
    new_range = str(start) if new_end == start else f"{start}–{new_end}"
    return f"，第 {old_range} 行 → 第 {new_range} 行"


def _bounded_diff(lines: list[str], max_lines: int, max_chars: int) -> str:
    visible = [_bounded(line, 240) for line in lines[:max_lines]]
    text = "\n".join(visible)
    omitted = len(lines) - len(visible)
    if omitted > 0:
        text += f"\n…（另有 {omitted} 行修改，请点击查看详情）"
    return _bounded(text, max_chars)


def _command(arguments: dict[str, Any]) -> list[str]:
    value = arguments.get("command", [])
    if not isinstance(value, list):
        return []
    return [str(part) for part in value]


def _command_kind(command: list[str]) -> str:
    lowered = [part.casefold() for part in command]
    joined = " ".join(lowered)
    if any(
        marker in joined
        for marker in (
            "pytest",
            "unittest",
            "npm test",
            "npm run test",
            "pnpm test",
            "yarn test",
            "cargo test",
            "go test",
            "dotnet test",
        )
    ):
        return "test"
    if "py_compile" in lowered or "compileall" in lowered:
        return "syntax"
    if any(marker in joined for marker in ("npm run build", "cargo build", "dotnet build")):
        return "build"
    if any(part in {"--version", "-v"} for part in lowered):
        return "version"
    return "command"


def _arguments(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（另有 {len(text) - limit} 个字符未显示）"
