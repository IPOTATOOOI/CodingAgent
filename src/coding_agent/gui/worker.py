"""在 Qt 后台线程中复用现有 Agent Runtime。"""

from collections.abc import Callable
import difflib
from pathlib import Path
from threading import Event, Lock
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from coding_agent.agent import Agent, AgentResult
from coding_agent.approval import (
    ApprovalAction,
    ApprovalDecision,
    SafetyMode,
    approval_action,
)
from coding_agent.config import Settings
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEvent, RuntimeEventKind
from coding_agent.llm import LLMClient, ToolCall
from coding_agent.message_queue import AgentMessageQueue
from coding_agent.tools.registry import create_tool_registry
from coding_agent.verification import VerificationTracker


ClientFactory = Callable[[Settings], LLMClient]


class AgentWorker(QObject):
    """把阻塞的 LLM/工具循环封装为 Qt Worker，并只通过 Signal 更新界面。"""

    tool_started = Signal(int, object)
    tool_finished = Signal(int, object, object, str)
    llm_retry = Signal(int, int)
    runtime_event = Signal(object)
    approval_requested = Signal(object)
    result_ready = Signal(object)
    # 兼容已有集成；新代码应使用语义更准确的 result_ready。
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        task: str,
        settings: Settings,
        conversation: Conversation,
        workspace: Path,
        max_steps: int,
        client_factory: ClientFactory = LLMClient,
        safety_mode: SafetyMode = SafetyMode.AUTO,
    ) -> None:
        super().__init__()
        self.task = task
        self.settings = settings
        self.conversation = conversation
        self.workspace = workspace.resolve()
        self.max_steps = max_steps
        self.client_factory = client_factory
        self.safety_mode = safety_mode
        self._cancel_event = Event()
        self._approval_event = Event()
        self._approval_lock = Lock()
        self._approval_sequence = 0
        self._pending_approval_id: int | None = None
        self._approval_granted: bool | None = None
        self.message_queue = AgentMessageQueue()

    def request_cancel(self) -> None:
        """线程安全地请求 Agent 在下一个安全检查点停止。"""
        self._cancel_event.set()
        self._approval_event.set()

    def resolve_approval(self, request_id: int, approved: bool) -> None:
        """由 GUI 主线程安全地批准或拒绝当前待处理工具。"""
        with self._approval_lock:
            if request_id != self._pending_approval_id:
                return
            self._approval_granted = approved
            self._approval_event.set()

    def add_steering(self, content: str) -> None:
        """线程安全地把补充指令加入当前任务。"""
        self.message_queue.add_steering(content)

    def add_follow_up(self, content: str) -> None:
        """线程安全地把独立后续任务加入队列。"""
        self.message_queue.add_follow_up(content)

    @Slot()
    def run(self) -> None:
        """创建与当前 workspace 绑定的 Runtime 并执行一次用户任务。"""
        try:
            tracker = VerificationTracker()

            agent = Agent(
                llm_client=self.client_factory(self.settings),
                conversation=self.conversation,
                tool_registry=create_tool_registry(
                    self.workspace,
                    should_cancel=self._cancel_event.is_set,
                    approval_callback=self._authorize_tool,
                ),
                max_steps=self.max_steps,
                verification_tracker=tracker,
                should_cancel=self._cancel_event.is_set,
                on_event=self._forward_event,
                message_queue=self.message_queue,
            )
            result: AgentResult = agent.run(self.task)
            while not self._cancel_event.is_set():
                follow_up = self.message_queue.pop_follow_up()
                if follow_up is None:
                    break
                result = agent.run(follow_up)
            self.result_ready.emit(result)
            self.completed.emit(result)
        except Exception as error:  # Qt 线程边界必须把意外异常转换为 Signal。
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()

    def _authorize_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ApprovalDecision:
        """在工具 handler 或 subprocess 启动前执行 GUI Safety Mode。"""
        action = approval_action(self.safety_mode, tool_name)
        if action == ApprovalAction.ALLOW:
            return ApprovalDecision.allow()
        if action == ApprovalAction.DENY:
            return ApprovalDecision.reject(
                "ReadOnlyMode",
                "当前处于只读安全模式，工具已在执行前被阻止。",
            )

        with self._approval_lock:
            self._approval_sequence += 1
            request_id = self._approval_sequence
            self._pending_approval_id = request_id
            self._approval_granted = None
            self._approval_event.clear()
        self.approval_requested.emit(
            {
                "request_id": request_id,
                "tool_name": tool_name,
                "arguments": self._safe_approval_arguments(arguments),
                "preview": self._approval_preview(tool_name, arguments),
            }
        )
        while not self._approval_event.wait(0.1):
            if self._cancel_event.is_set():
                break
        with self._approval_lock:
            approved = self._approval_granted is True
            self._pending_approval_id = None
            self._approval_granted = None
            self._approval_event.clear()
        if approved and not self._cancel_event.is_set():
            return ApprovalDecision.allow()
        return ApprovalDecision.reject()

    @staticmethod
    def _safe_approval_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        """授权卡片只显示关键目标，不复制完整文件正文。"""
        safe: dict[str, Any] = {}
        for name, value in arguments.items():
            if name in {"content", "old_text", "new_text"}:
                safe[name] = f"<{len(value) if isinstance(value, str) else 0} 个字符>"
            else:
                safe[name] = value
        return safe

    @staticmethod
    def _approval_preview(tool_name: str, arguments: dict[str, Any]) -> str:
        """为授权卡片生成有界创建/编辑预览。"""
        path = str(arguments.get("path", "file"))
        if tool_name == "edit_file":
            lines = difflib.unified_diff(
                str(arguments.get("old_text", "")).splitlines(),
                str(arguments.get("new_text", "")).splitlines(),
                fromfile=f"{path}（修改前）",
                tofile=f"{path}（修改后）",
                lineterm="",
            )
            return "\n".join(list(lines)[:12])[:1_200]
        if tool_name == "write_file":
            content = str(arguments.get("content", ""))
            lines = [f"+++ {path}", *(f"+{line}" for line in content.splitlines()[:10])]
            return "\n".join(lines)[:1_200]
        return ""

    def _forward_event(self, event: RuntimeEvent) -> None:
        """统一发布事件，同时保留旧 Qt Signal 供现有集成兼容使用。"""
        self.runtime_event.emit(event)
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
                self.tool_started.emit(event.step or 0, tool_call)
            else:
                self.tool_finished.emit(
                    event.step or 0,
                    tool_call,
                    payload.get("result", {}),
                    str(payload.get("verification_status", "not_required")),
                )
        elif event.kind == RuntimeEventKind.LLM_RETRY:
            self.llm_retry.emit(
                int(payload.get("retry", 0)),
                int(payload.get("max_retries", 0)),
            )
