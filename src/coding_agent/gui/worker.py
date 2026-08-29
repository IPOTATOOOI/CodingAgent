"""在 Qt 后台线程中复用现有 Agent Runtime。"""

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from coding_agent.agent import Agent, AgentResult
from coding_agent.config import Settings
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, ToolCall
from coding_agent.tools.registry import create_tool_registry
from coding_agent.verification import VerificationTracker


ClientFactory = Callable[[Settings], LLMClient]


class AgentWorker(QObject):
    """把阻塞的 LLM/工具循环封装为 Qt Worker，并只通过 Signal 更新界面。"""

    tool_started = Signal(int, object)
    tool_finished = Signal(int, object, object, str)
    llm_retry = Signal(int, int)
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

    def request_cancel(self) -> None:
        """线程安全地请求 Agent 在下一个安全检查点停止。"""
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        """创建与当前 workspace 绑定的 Runtime 并执行一次用户任务。"""
        try:
            tracker = VerificationTracker()

            def tool_finished(
                step: int,
                tool_call: ToolCall,
                result: dict[str, Any],
            ) -> None:
                self.tool_finished.emit(
                    step,
                    tool_call,
                    result,
                    tracker.verification_status,
                )

            agent = Agent(
                llm_client=self.client_factory(self.settings),
                conversation=self.conversation,
                tool_registry=create_tool_registry(self.workspace),
                max_steps=self.max_steps,
                verification_tracker=tracker,
                on_tool_call=self.tool_started.emit,
                on_tool_result=tool_finished,
                on_llm_retry=self.llm_retry.emit,
                should_cancel=self._cancel_event.is_set,
            )
            result: AgentResult = agent.run(self.task)
            self.completed.emit(result)
        except Exception as error:  # Qt 线程边界必须把意外异常转换为 Signal。
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            self.finished.emit()
