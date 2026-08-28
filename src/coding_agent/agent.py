"""自主 Coding Agent 的核心控制循环。"""

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from coding_agent.conversation import Conversation
from coding_agent.llm import LLMClient, LLMError, ToolCall
from coding_agent.tools.registry import ToolRegistry


DEFAULT_MAX_STEPS = 12
MIN_MAX_STEPS = 1
MAX_MAX_STEPS = 50
MAX_TOOL_CALLS_PER_STEP = 8

ToolCallCallback = Callable[[int, ToolCall], None]
ToolResultCallback = Callable[[int, ToolCall, dict[str, Any]], None]


@dataclass(frozen=True)
class AgentResult:
    """一次 Agent 任务的最终结果。"""

    content: str
    stop_reason: str
    steps: int


class Agent:
    """让模型根据 Observation 自主选择下一步行动。"""

    def __init__(
        self,
        llm_client: LLMClient,
        conversation: Conversation,
        tool_registry: ToolRegistry,
        max_steps: int = DEFAULT_MAX_STEPS,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
    ) -> None:
        if not MIN_MAX_STEPS <= max_steps <= MAX_MAX_STEPS:
            raise ValueError(
                f"max_steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}."
            )
        self.llm_client = llm_client
        self.conversation = conversation
        self.tool_registry = tool_registry
        self.max_steps = max_steps
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result

    def run(self, user_input: str) -> AgentResult:
        """运行一个有最大步数边界的自主任务。"""
        self.conversation.add_user_message(user_input)
        completed_steps = 0

        try:
            for step_number in range(1, self.max_steps + 1):
                try:
                    response = self.llm_client.complete(
                        self.conversation.messages,
                        tools=self.tool_registry.schemas,
                    )
                except LLMError as error:
                    return AgentResult(
                        content=f"LLM request failed: {error}",
                        stop_reason="llm_error",
                        steps=completed_steps,
                    )

                completed_steps = step_number
                if response.tool_calls:
                    if len(response.tool_calls) > MAX_TOOL_CALLS_PER_STEP:
                        return AgentResult(
                            content=(
                                "ToolCallLimitExceeded: the model requested "
                                f"{len(response.tool_calls)} tool calls in one step; "
                                f"the limit is {MAX_TOOL_CALLS_PER_STEP}."
                            ),
                            stop_reason="invalid_response",
                            steps=completed_steps,
                        )

                    self.conversation.add_assistant_tool_calls(
                        response.content,
                        response.tool_calls,
                    )
                    for tool_call in response.tool_calls:
                        if self.on_tool_call is not None:
                            self.on_tool_call(step_number, tool_call)
                        result = self.tool_registry.execute(
                            tool_call.name,
                            tool_call.arguments,
                        )
                        self.conversation.add_tool_result(
                            tool_call.id,
                            json.dumps(result, ensure_ascii=False),
                        )
                        if self.on_tool_result is not None:
                            self.on_tool_result(step_number, tool_call, result)
                    continue

                if response.content:
                    self.conversation.add_assistant_message(response.content)
                    return AgentResult(
                        content=response.content,
                        stop_reason="completed",
                        steps=completed_steps,
                    )

                return AgentResult(
                    content="Model returned neither text nor tool calls.",
                    stop_reason="invalid_response",
                    steps=completed_steps,
                )
        except KeyboardInterrupt:
            return AgentResult(
                content="Agent task was interrupted.",
                stop_reason="interrupted",
                steps=completed_steps,
            )

        return AgentResult(
            content="Maximum agent steps reached before completion.",
            stop_reason="max_steps",
            steps=self.max_steps,
        )
