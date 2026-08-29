"""自主 Coding Agent 的核心控制循环。"""

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from coding_agent.context import ContextManager
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, LLMError, ToolCall
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


DEFAULT_MAX_STEPS = 12
MIN_MAX_STEPS = 1
MAX_MAX_STEPS = 50
MAX_TOOL_CALLS_PER_STEP = 8
MAX_VERIFICATION_REMINDERS = 2
VERIFICATION_REMINDER = """Runtime verification requirement:

The latest source-code changes have not been verified.
Before completing the task, run an appropriate available test, build, or syntax-check command after the most recent modification.

If verification fails, continue repairing the project."""

ToolCallCallback = Callable[[int, ToolCall], None]
ToolResultCallback = Callable[[int, ToolCall, dict[str, Any]], None]


@dataclass(frozen=True)
class AgentResult:
    """一次 Agent 任务的最终结果。"""

    content: str
    stop_reason: str
    steps: int
    tool_calls: int = 0
    verification_status: str = VERIFICATION_NOT_REQUIRED


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
    ) -> None:
        if not MIN_MAX_STEPS <= max_steps <= MAX_MAX_STEPS:
            raise ValueError(
                f"max_steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}."
            )
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

    def run(self, user_input: str) -> AgentResult:
        """运行一个有最大步数边界的自主任务。"""
        self.conversation.add_user_message(user_input)
        self.reliability_tracker.reset_task()
        self.verification_tracker.reset_task()
        completed_steps = 0
        handled_tool_calls = 0
        verification_reminders = 0

        def build_result(content: str, stop_reason: str) -> AgentResult:
            """使用当前任务指标构造一致的 AgentResult。"""
            return AgentResult(
                content=content,
                stop_reason=stop_reason,
                steps=completed_steps,
                tool_calls=handled_tool_calls,
                verification_status=(
                    self.verification_tracker.verification_status
                ),
            )

        try:
            for step_number in range(1, self.max_steps + 1):
                try:
                    context = self.context_manager.build_context(
                        self.conversation.messages
                    )
                    response = self.retry_policy.execute(
                        lambda: self.llm_client.complete(
                            context,
                            tools=self.tool_registry.schemas,
                        ),
                        on_retry=self.on_llm_retry,
                    )
                except LLMError as error:
                    return build_result(f"LLM request failed: {error}", "llm_error")

                completed_steps = step_number
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
                    for tool_call in response.tool_calls:
                        handled_tool_calls += 1
                        if self.on_tool_call is not None:
                            self.on_tool_call(step_number, tool_call)
                        repeated_action = (
                            self.reliability_tracker.is_repeated_action(tool_call)
                        )
                        if repeated_action:
                            result = {
                                "success": False,
                                "error": "RepeatedAction",
                                "message": (
                                    "The same tool call has been requested repeatedly "
                                    "without an intervening action. Reconsider the approach."
                                ),
                            }
                        else:
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
                            repeated_action=repeated_action,
                        )
                        self.verification_tracker.record_tool_result(
                            tool_call,
                            result,
                        )
                        if self.on_tool_result is not None:
                            self.on_tool_result(step_number, tool_call, result)
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
                    if not self.verification_tracker.completion_blocked:
                        return build_result(response.content, "completed")
                    if verification_reminders >= MAX_VERIFICATION_REMINDERS:
                        return build_result(response.content, "verification_required")
                    verification_reminders += 1
                    self.conversation.add_system_message(VERIFICATION_REMINDER)
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
