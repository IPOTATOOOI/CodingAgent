"""与界面无关的工具授权模式和决策类型。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class SafetyMode(str, Enum):
    """用户可选择的工具授权策略。"""

    ASK = "ask"
    AUTO_EDIT = "auto_edit"
    AUTO = "auto"
    READ_ONLY = "read_only"


class ApprovalAction(str, Enum):
    """某次工具调用在指定模式下应采取的动作。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


READ_ONLY_TOOLS = {"list_directory", "read_file", "search_text"}
EDIT_TOOLS = {"create_directory", "write_file", "edit_file"}
EXECUTION_TOOLS = {"run_command"}


@dataclass(frozen=True)
class ApprovalDecision:
    """ToolRegistry 在执行 handler 前消费的授权结果。"""

    approved: bool
    error: str = "ApprovalRejected"
    message: str = "The user rejected this tool call."

    @classmethod
    def allow(cls) -> "ApprovalDecision":
        return cls(True, "", "")

    @classmethod
    def reject(
        cls,
        error: str = "ApprovalRejected",
        message: str = "The user rejected this tool call.",
    ) -> "ApprovalDecision":
        return cls(False, error, message)


ApprovalCallback = Callable[[str, dict[str, Any]], ApprovalDecision]


def approval_action(mode: SafetyMode, tool_name: str) -> ApprovalAction:
    """把四种用户模式映射为确定性的执行前策略。"""
    if tool_name in READ_ONLY_TOOLS:
        return ApprovalAction.ALLOW
    if mode == SafetyMode.AUTO:
        return ApprovalAction.ALLOW
    if mode == SafetyMode.READ_ONLY:
        return ApprovalAction.DENY
    if mode == SafetyMode.AUTO_EDIT and tool_name in EDIT_TOOLS:
        return ApprovalAction.ALLOW
    if tool_name in EDIT_TOOLS | EXECUTION_TOOLS:
        return ApprovalAction.ASK
    return ApprovalAction.DENY
