"""Agent Runtime 对外发布的类型化事件。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class RuntimeEventKind(str, Enum):
    """Runtime 生命周期中可被 CLI、GUI 或日志系统观察的事件类型。"""

    TASK_STARTED = "task_started"
    STEP_STARTED = "step_started"
    CONTEXT_BUILT = "context_built"
    LLM_REQUEST_STARTED = "llm_request_started"
    LLM_TEXT_DELTA = "llm_text_delta"
    LLM_RESPONSE_RECEIVED = "llm_response_received"
    LLM_RETRY = "llm_retry"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    STEERING_RECEIVED = "steering_received"
    VERIFICATION_CHANGED = "verification_changed"
    TASK_FINISHED = "task_finished"


@dataclass(frozen=True)
class RuntimeEvent:
    """不依赖 UI 框架的单个 Runtime 事件。"""

    kind: RuntimeEventKind
    step: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """转换成适合 JSON 持久化或结构化日志的普通对象。"""
        return {
            "kind": self.kind.value,
            "step": self.step,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


RuntimeEventCallback = Callable[[RuntimeEvent], None]
