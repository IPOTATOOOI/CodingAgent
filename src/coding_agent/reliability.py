"""自主循环的重复行为、无进展与 LLM 重试策略。"""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any, TypeVar

from coding_agent.llm import LLMError, ToolCall


REPEATED_ACTION_LIMIT = 3
NO_PROGRESS_LIMIT = 4
MAX_LLM_RETRIES = 2
DEFAULT_RETRY_DELAYS = (0.5, 1.0)

ResultType = TypeVar("ResultType")
RetryCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class LLMRetryPolicy:
    """只重试明确标记为临时性的 LLM 错误。"""

    max_retries: int = MAX_LLM_RETRIES
    delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        """验证重试次数和退避配置。"""
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative.")
        if self.max_retries > 0 and not self.delays:
            raise ValueError("delays must not be empty when retries are enabled.")
        if any(delay < 0 for delay in self.delays):
            raise ValueError("retry delays must not be negative.")

    def execute(
        self,
        request: Callable[[], ResultType],
        on_retry: RetryCallback | None = None,
    ) -> ResultType:
        """使用固定退避执行请求，耗尽或永久错误时重新抛出。"""
        retries = 0
        while True:
            try:
                return request()
            except LLMError as error:
                if not error.transient or retries >= self.max_retries:
                    raise
                retries += 1
                if on_retry is not None:
                    on_retry(retries, self.max_retries)
                delay = self.delays[min(retries - 1, len(self.delays) - 1)]
                self.sleeper(delay)


class ReliabilityTracker:
    """维护一个用户任务内的连续 Action 与进展状态。"""

    def __init__(
        self,
        repeated_action_limit: int = REPEATED_ACTION_LIMIT,
        no_progress_limit: int = NO_PROGRESS_LIMIT,
    ) -> None:
        if repeated_action_limit < 2:
            raise ValueError("repeated_action_limit must be at least 2.")
        if no_progress_limit < 1:
            raise ValueError("no_progress_limit must be positive.")
        self.repeated_action_limit = repeated_action_limit
        self.no_progress_limit = no_progress_limit
        self.reset_task()

    def reset_task(self) -> None:
        """重置当前用户任务的短期可靠性状态。"""
        self._last_action_signature: str | None = None
        self._consecutive_action_count = 0
        self._last_observation_fingerprint: str | None = None
        self._step_has_progress = False
        self._step_has_result = False
        self.consecutive_no_progress_steps = 0

    def start_step(self) -> None:
        """开始记录一个新的 Agent Step。"""
        self._step_has_progress = False
        self._step_has_result = False

    def is_repeated_action(self, tool_call: ToolCall) -> bool:
        """记录 Action，并判断是否达到连续相同调用阈值。"""
        signature = self.action_signature(tool_call)
        if signature == self._last_action_signature:
            self._consecutive_action_count += 1
        else:
            self._last_action_signature = signature
            self._consecutive_action_count = 1
        return self._consecutive_action_count >= self.repeated_action_limit

    def record_tool_result(
        self,
        tool_call: ToolCall,
        result: dict[str, Any],
        repeated_action: bool = False,
    ) -> None:
        """根据 Workspace 修改或 Observation 变化更新本 Step 进展。"""
        self._step_has_result = True
        if self._is_successful_mutation(tool_call, result):
            self._step_has_progress = True
            self._last_observation_fingerprint = None
            return
        if repeated_action or result.get("error") in {
            "RepeatedAction",
            "CommandBlocked",
        }:
            return

        fingerprint = self.result_fingerprint(tool_call, result)
        if fingerprint != self._last_observation_fingerprint:
            self._step_has_progress = True
            self._last_observation_fingerprint = fingerprint

    def finish_step(self) -> bool:
        """结束当前 Step，并返回是否达到无进展停止阈值。"""
        if self._step_has_progress:
            self.consecutive_no_progress_steps = 0
        elif self._step_has_result:
            self.consecutive_no_progress_steps += 1
        return self.consecutive_no_progress_steps >= self.no_progress_limit

    @staticmethod
    def action_signature(tool_call: ToolCall) -> str:
        """使用规范化 JSON 参数生成稳定 Tool Action Signature。"""
        try:
            arguments: Any = json.loads(tool_call.arguments)
        except (json.JSONDecodeError, TypeError):
            canonical_arguments = str(tool_call.arguments)
        else:
            canonical_arguments = json.dumps(
                arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return f"{tool_call.name}|{canonical_arguments}"

    @classmethod
    def result_fingerprint(
        cls,
        tool_call: ToolCall,
        result: dict[str, Any],
    ) -> str:
        """为 Action 与关键结果生成跨进程稳定的 SHA256 指纹。"""
        normalized_result = cls._normalized_result(tool_call, result)
        payload = json.dumps(
            {
                "action": cls.action_signature(tool_call),
                "result": normalized_result,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_successful_mutation(
        tool_call: ToolCall,
        result: dict[str, Any],
    ) -> bool:
        """识别真正改变 Workspace 的成功文件操作。"""
        if not result.get("success") or tool_call.name not in {
            "write_file",
            "edit_file",
        }:
            return False
        data = result.get("data", {})
        if not isinstance(data, dict):
            return False
        return bool(data.get("created") or data.get("modified"))

    @staticmethod
    def _normalized_result(
        tool_call: ToolCall,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """排除耗时等噪声，只保留判断 Observation 是否变化的字段。"""
        if tool_call.name != "run_command" or not result.get("success"):
            return result
        data = result.get("data", {})
        if not isinstance(data, dict):
            return result
        return {
            "success": True,
            "exit_code": data.get("exit_code"),
            "timed_out": data.get("timed_out"),
            "stdout": data.get("stdout", ""),
            "stderr": data.get("stderr", ""),
            "stdout_truncated": data.get("stdout_truncated", False),
            "stderr_truncated": data.get("stderr_truncated", False),
        }
