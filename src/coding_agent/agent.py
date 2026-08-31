"""自主 Coding Agent 的核心控制循环。"""

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from coding_agent.context import ContextManager
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEvent, RuntimeEventCallback, RuntimeEventKind
from coding_agent.llm import LLMClient, LLMError, LLMInterrupted, LLMResponse, ToolCall
from coding_agent.message_queue import AgentMessageQueue
from coding_agent.reliability import (
    LLMRetryPolicy,
    ReliabilityTracker,
    RetryCallback,
)
from coding_agent.tools.registry import ToolRegistry
from coding_agent.verification import (
    VerificationTracker,
    VERIFICATION_NOT_REQUIRED,
)


DEFAULT_MAX_STEPS = 20
MIN_MAX_STEPS = 1
MAX_TOOL_CALLS_PER_STEP = 8
MAX_VERIFICATION_REMINDERS = 2
VERIFICATION_REMINDER = """Runtime verification requirement:

The latest source-code changes have not been verified.
Before completing the task, run an appropriate available test, build, or syntax-check command after the most recent modification.

If verification fails, continue repairing the project."""
INSPECTION_REMINDER = """Runtime progress advisory:

You have made {count} different read-only inspection calls since the last file change or command execution.
Synthesize the observations you already have and choose a concrete next action: edit the implementation, run an appropriate verification command, or finish if the task is complete.
Do not re-read unchanged files. Inspect more only when a specific missing fact is required for the next action."""

ToolCallCallback = Callable[[int, ToolCall], None]
ToolResultCallback = Callable[[int, ToolCall, dict[str, Any]], None]
CancellationCallback = Callable[[], bool]


@dataclass(frozen=True)
class AgentResult:
    """一次 Agent 任务的最终结果。"""

    content: str
    stop_reason: str
    steps: int
    tool_calls: int = 0
    verification_status: str = VERIFICATION_NOT_REQUIRED
    verification_reminders: int = 0
    llm_retries: int = 0
    mutation_generations: int = 0


class Agent:
    """让模型根据 Observation 自主选择下一步行动。"""

    def __init__(
        self,
        llm_client: LLMClient,
        conversation: Conversation,
        tool_registry: ToolRegistry,
        max_steps: int = DEFAULT_MAX_STEPS,
        context_manager: ContextManager | None = None,
        reliability_tracker: ReliabilityTracker | None = None,
        retry_policy: LLMRetryPolicy | None = None,
        verification_tracker: VerificationTracker | None = None,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
        on_llm_retry: RetryCallback | None = None,
        should_cancel: CancellationCallback | None = None,
        on_event: RuntimeEventCallback | None = None,
        message_queue: AgentMessageQueue | None = None,
    ) -> None:
        if max_steps < MIN_MAX_STEPS:
            raise ValueError(f"max_steps must be at least {MIN_MAX_STEPS}.")
        self.llm_client = llm_client
        self.conversation = conversation
        self.tool_registry = tool_registry
        self.max_steps = max_steps
        self.context_manager = context_manager or ContextManager()
        self.reliability_tracker = reliability_tracker or ReliabilityTracker()
        self.retry_policy = retry_policy or LLMRetryPolicy()
        self.verification_tracker = verification_tracker or VerificationTracker()
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.on_llm_retry = on_llm_retry
        self.should_cancel = should_cancel
        self.on_event = on_event
        self.message_queue = message_queue or AgentMessageQueue()

    def run(self, user_input: str) -> AgentResult:
        """运行一个有最大步数边界的自主任务。"""
        self.conversation.add_user_message(user_input)
        self.reliability_tracker.reset_task()
        self.verification_tracker.reset_task()
        completed_steps = 0
        handled_tool_calls = 0
        verification_reminders = 0
        llm_retries = 0
        self._emit(
            RuntimeEventKind.TASK_STARTED,
            payload={"task": user_input, "max_steps": self.max_steps},
        )

        def handle_llm_retry(retry_number: int, max_retries: int) -> None:
            """统计 LLM Retry，并继续调用外部观察回调。"""
            nonlocal llm_retries
            llm_retries += 1
            if self.on_llm_retry is not None:
                self.on_llm_retry(retry_number, max_retries)
            self._emit(
                RuntimeEventKind.LLM_RETRY,
                step=completed_steps + 1,
                payload={"retry": retry_number, "max_retries": max_retries},
            )

        def build_result(content: str, stop_reason: str) -> AgentResult:
            """使用当前任务指标构造一致的 AgentResult。"""
            result = AgentResult(
                content=content,
                stop_reason=stop_reason,
                steps=completed_steps,
                tool_calls=handled_tool_calls,
                verification_status=(
                    self.verification_tracker.verification_status
                ),
                verification_reminders=verification_reminders,
                llm_retries=llm_retries,
                mutation_generations=(
                    self.verification_tracker.mutation_generation
                ),
            )
            self._emit(
                RuntimeEventKind.TASK_FINISHED,
                step=completed_steps or None,
                payload={
                    "content": result.content,
                    "stop_reason": result.stop_reason,
                    "steps": result.steps,
                    "tool_calls": result.tool_calls,
                    "verification_status": result.verification_status,
                    "llm_retries": result.llm_retries,
                },
            )
            return result

        try:
            for step_number in range(1, self.max_steps + 1):
                if self._cancellation_requested():
                    return build_result("Agent task was interrupted.", "interrupted")
                self._apply_steering(step_number)
                self._emit(RuntimeEventKind.STEP_STARTED, step=step_number)
                try:
                    context = self.context_manager.build_context(
                        self.conversation.messages
                    )
                    stats = self.context_manager.last_stats
                    self._emit(
                        RuntimeEventKind.CONTEXT_BUILT,
                        step=step_number,
                        payload={
                            "input_messages": stats.input_messages,
                            "output_messages": stats.output_messages,
                            "input_chars": stats.input_chars,
                            "output_chars": stats.output_chars,
                            "input_tokens": stats.input_tokens,
                            "output_tokens": stats.output_tokens,
                            "compacted_tool_results": stats.compacted_tool_results,
                            "dropped_groups": stats.dropped_groups,
                        },
                    )
                    self._emit(RuntimeEventKind.LLM_REQUEST_STARTED, step=step_number)
                    response = self.retry_policy.execute(
                        lambda: self._complete(context, step_number),
                        on_retry=handle_llm_retry,
                    )
                except LLMInterrupted:
                    return build_result("Agent task was interrupted.", "interrupted")
                except LLMError as error:
                    return build_result(f"LLM request failed: {error}", "llm_error")

                # 同步 Client 返回后仍需检查；流式 Client 还会在数据块之间检查取消。
                if self._cancellation_requested():
                    return build_result("Agent task was interrupted.", "interrupted")

                completed_steps = step_number
                self._emit(
                    RuntimeEventKind.LLM_RESPONSE_RECEIVED,
                    step=step_number,
                    payload={
                        "content": response.content,
                        "tool_call_count": len(response.tool_calls),
                    },
                )
                if response.tool_calls:
                    if len(response.tool_calls) > MAX_TOOL_CALLS_PER_STEP:
                        return build_result(
                            (
                                "ToolCallLimitExceeded: the model requested "
                                f"{len(response.tool_calls)} tool calls in one step; "
                                f"the limit is {MAX_TOOL_CALLS_PER_STEP}."
                            ),
                            "invalid_response",
                        )

                    self.conversation.add_assistant_tool_calls(
                        response.content,
                        response.tool_calls,
                    )
                    self.reliability_tracker.start_step()
                    cancelled_during_tools = False
                    for tool_call in response.tool_calls:
                        handled_tool_calls += 1
                        if self.on_tool_call is not None:
                            self.on_tool_call(step_number, tool_call)
                        self._emit(
                            RuntimeEventKind.TOOL_STARTED,
                            step=step_number,
                            payload=self._tool_payload(tool_call),
                        )
                        repeated_observation = False
                        if self._cancellation_requested():
                            repeated_action = False
                            cancelled_during_tools = True
                            result = {
                                "success": False,
                                "error": "Cancelled",
                                "message": "Tool call skipped because the task was stopped.",
                            }
                        else:
                            repeated_action = (
                                self.reliability_tracker.is_repeated_action(tool_call)
                            )
                            repeated_observation = (
                                not repeated_action
                                and self.reliability_tracker.is_repeated_observation(
                                    tool_call
                                )
                            )
                        if not cancelled_during_tools and repeated_action:
                            result = {
                                "success": False,
                                "error": "RepeatedAction",
                                "message": (
                                    "The same tool call has been requested repeatedly "
                                    "without an intervening action. Reconsider the approach."
                                ),
                            }
                        elif not cancelled_during_tools and repeated_observation:
                            result = {
                                "success": False,
                                "error": "RepeatedObservation",
                                "message": (
                                    "This exact read-only tool call already succeeded "
                                    "and the workspace has not changed since then. Use "
                                    "the existing observation or take a concrete next action."
                                ),
                            }
                        elif not cancelled_during_tools:
                            result = self.tool_registry.execute(
                                tool_call.name,
                                tool_call.arguments,
                            )
                        self.conversation.add_tool_result(
                            tool_call.id,
                            json.dumps(result, ensure_ascii=False),
                        )
                        self.reliability_tracker.record_tool_result(
                            tool_call,
                            result,
                            repeated_action=(
                                repeated_action or repeated_observation
                            ),
                        )
                        previous_verification = (
                            self.verification_tracker.verification_status
                        )
                        self.verification_tracker.record_tool_result(
                            tool_call,
                            result,
                        )
                        if (
                            self.verification_tracker.verification_status
                            != previous_verification
                        ):
                            self._emit(
                                RuntimeEventKind.VERIFICATION_CHANGED,
                                step=step_number,
                                payload={
                                    "status": (
                                        self.verification_tracker.verification_status
                                    )
                                },
                            )
                        if self.on_tool_result is not None:
                            self.on_tool_result(step_number, tool_call, result)
                        self._emit(
                            RuntimeEventKind.TOOL_FINISHED,
                            step=step_number,
                            payload={
                                **self._tool_payload(tool_call),
                                "result": result,
                                "verification_status": (
                                    self.verification_tracker.verification_status
                                ),
                            },
                        )
                        if self._cancellation_requested():
                            cancelled_during_tools = True
                    if cancelled_during_tools:
                        return build_result("Agent task was interrupted.", "interrupted")
                    inspection_count = (
                        self.reliability_tracker.take_inspection_reminder()
                    )
                    if inspection_count:
                        self.conversation.add_system_message(
                            INSPECTION_REMINDER.format(count=inspection_count)
                        )
                        self._emit(
                            RuntimeEventKind.PROGRESS_WARNING,
                            step=step_number,
                            payload={"inspection_calls": inspection_count},
                        )
                    if self.reliability_tracker.finish_step():
                        return build_result(
                            (
                                "Agent stopped because no meaningful progress "
                                "was detected."
                            ),
                            "no_progress",
                        )
                    continue

                if response.content:
                    self.conversation.add_assistant_message(response.content)
                    # 模型生成最终文本期间若收到补充指令，先进入下一 Step 处理，
                    # 避免用户刚点击“追加”任务就已经结束而丢失消息。
                    if self.message_queue.pending_steering:
                        continue
                    if not self.verification_tracker.completion_blocked:
                        return build_result(response.content, "completed")
                    if verification_reminders >= MAX_VERIFICATION_REMINDERS:
                        return build_result(response.content, "verification_required")
                    verification_reminders += 1
                    self.conversation.add_system_message(VERIFICATION_REMINDER)
                    self._emit(
                        RuntimeEventKind.VERIFICATION_CHANGED,
                        step=step_number,
                        payload={
                            "status": self.verification_tracker.verification_status,
                            "reminder": verification_reminders,
                        },
                    )
                    continue

                return build_result(
                    "Model returned neither text nor tool calls.",
                    "invalid_response",
                )
        except KeyboardInterrupt:
            return build_result("Agent task was interrupted.", "interrupted")

        completed_steps = self.max_steps
        return build_result(
            "Maximum agent steps reached before completion.",
            "max_steps",
        )

    def _cancellation_requested(self) -> bool:
        """在不影响 CLI 默认行为的前提下查询协作式取消状态。"""
        return self.should_cancel is not None and self.should_cancel()

    def _complete(self, context: list[dict[str, Any]], step_number: int) -> LLMResponse:
        """优先使用显式声明支持的流式接口，并兼容原有同步 Client。"""
        if getattr(self.llm_client, "supports_streaming", False) is True:
            return self.llm_client.complete_stream(
                context,
                tools=self.tool_registry.schemas,
                on_text_delta=lambda delta: self._emit(
                    RuntimeEventKind.LLM_TEXT_DELTA,
                    step=step_number,
                    payload={"delta": delta},
                ),
                should_cancel=self._cancellation_requested,
            )
        return self.llm_client.complete(context, tools=self.tool_registry.schemas)

    def _apply_steering(self, step_number: int) -> None:
        """在 LLM 请求前把运行中追加的指令安全写入 Conversation。"""
        for message in self.message_queue.drain_steering():
            self.conversation.add_user_message(message)
            self._emit(
                RuntimeEventKind.STEERING_RECEIVED,
                step=step_number,
                payload={"content": message},
            )

    def _emit(
        self,
        kind: RuntimeEventKind,
        *,
        step: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """发布观察事件；观察者异常不得改变 Agent 核心行为。"""
        if self.on_event is None:
            return
        try:
            self.on_event(RuntimeEvent(kind=kind, step=step, payload=payload or {}))
        except Exception:
            return

    @staticmethod
    def _tool_payload(tool_call: ToolCall) -> dict[str, Any]:
        return {
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
        }
