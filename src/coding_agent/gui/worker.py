"""在 Qt 后台线程中复用现有 Agent Runtime。"""

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from coding_agent.agent import Agent, AgentResult
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
    ) -> None:
        super().__init__()
        self.task = task
        self.settings = settings
        self.conversation = conversation
        self.workspace = workspace.resolve()
        self.max_steps = max_steps
        self.client_factory = client_factory
        self._cancel_event = Event()
        self.message_queue = AgentMessageQueue()

    def request_cancel(self) -> None:
        """线程安全地请求 Agent 在下一个安全检查点停止。"""
        self._cancel_event.set()

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
            self.completed.emit(result)
        except Exception as error:  # Qt 线程边界必须把意外异常转换为 Signal。
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()

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
