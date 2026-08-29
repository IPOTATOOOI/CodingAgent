"""为 LLM 请求构造有界且协议完整的上下文视图。"""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any

from coding_agent.conversation import Message


DEFAULT_MAX_CONTEXT_CHARS = 60_000
DEFAULT_MAX_CONTEXT_TOKENS = 16_000
DEFAULT_RECENT_GROUPS = 12
MAX_COMPACT_ASSISTANT_CONTENT_CHARS = 500


@dataclass(frozen=True)
class ContextStats:
    """最近一次上下文构造的大小变化统计。"""

    input_messages: int
    output_messages: int
    input_chars: int
    output_chars: int
    compacted_tool_results: int
    dropped_groups: int
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _MessageGroup:
    """在裁剪时不可拆分的一组协议消息。"""

    kind: str
    messages: list[Message]


class ContextManager:
    """保留完整 Conversation，同时生成确定性的有界请求视图。"""

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
        recent_groups: int = DEFAULT_RECENT_GROUPS,
        max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive.")
        if recent_groups < 1:
            raise ValueError("recent_groups must be positive.")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive.")
        self.max_chars = max_chars
        self.recent_groups = recent_groups
        self.max_tokens = max_tokens
        self.last_stats = ContextStats(0, 0, 0, 0, 0, 0)

    def build_context(self, messages: list[Message]) -> list[Message]:
        """按消息组压缩旧 Observation，并优先保留当前任务与最近历史。"""
        original = deepcopy(messages)
        input_chars = self._serialized_chars(original)
        input_tokens = self._estimated_tokens(original)
        if input_chars <= self.max_chars and input_tokens <= self.max_tokens:
            self.last_stats = ContextStats(
                input_messages=len(original),
                output_messages=len(original),
                input_chars=input_chars,
                output_chars=input_chars,
                compacted_tool_results=0,
                dropped_groups=0,
                input_tokens=input_tokens,
                output_tokens=input_tokens,
            )
            return original

        groups = self._group_messages(original)
        current_user_index = self._find_current_user_group(groups)
        protected = {
            index
            for index, group in enumerate(groups)
            if group.kind == "system" or index == current_user_index
        }
        kept = [True] * len(groups)
        compacted_tool_results = 0
        dropped_groups = 0
        recent_start = max(0, len(groups) - self.recent_groups)

        for index in range(recent_start):
            if groups[index].kind != "tool_interaction":
                continue
            compacted_tool_results += self._compact_tool_group(groups[index])
            if self._within_budget(groups, kept):
                return self._finish(
                    original,
                    groups,
                    kept,
                    input_chars,
                    input_tokens,
                    compacted_tool_results,
                    dropped_groups,
                )

        for index in range(recent_start):
            if index in protected or not kept[index]:
                continue
            kept[index] = False
            dropped_groups += 1
            if self._within_budget(groups, kept):
                return self._finish(
                    original,
                    groups,
                    kept,
                    input_chars,
                    input_tokens,
                    compacted_tool_results,
                    dropped_groups,
                )

        for index in range(recent_start, len(groups)):
            if groups[index].kind != "tool_interaction":
                continue
            compacted_tool_results += self._compact_tool_group(groups[index])
            if self._within_budget(groups, kept):
                return self._finish(
                    original,
                    groups,
                    kept,
                    input_chars,
                    input_tokens,
                    compacted_tool_results,
                    dropped_groups,
                )

        for index in range(recent_start, len(groups)):
            if index in protected or not kept[index]:
                continue
            kept[index] = False
            dropped_groups += 1
            if self._within_budget(groups, kept):
                break

        return self._finish(
            original,
            groups,
            kept,
            input_chars,
            input_tokens,
            compacted_tool_results,
            dropped_groups,
        )

    @staticmethod
    def _group_messages(messages: list[Message]) -> list[_MessageGroup]:
        """识别 System、User、普通 Assistant 和工具交互消息组。"""
        groups: list[_MessageGroup] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            role = message.get("role")
            if role == "assistant" and message.get("tool_calls"):
                grouped_messages = [message]
                index += 1
                while index < len(messages) and messages[index].get("role") == "tool":
                    grouped_messages.append(messages[index])
                    index += 1
                groups.append(_MessageGroup("tool_interaction", grouped_messages))
                continue

            kind = role if role in {"system", "user", "assistant"} else "other"
            groups.append(_MessageGroup(str(kind), [message]))
            index += 1
        return groups

    @staticmethod
    def _find_current_user_group(groups: list[_MessageGroup]) -> int | None:
        """返回最后一条用户任务所在的消息组索引。"""
        for index in range(len(groups) - 1, -1, -1):
            if groups[index].kind == "user":
                return index
        return None

    @staticmethod
    def _compact_tool_group(group: _MessageGroup) -> int:
        """保留工具协议和关键元数据，同时删除旧的大型 Payload。"""
        assistant = group.messages[0]
        calls_by_id = {
            call.get("id"): call
            for call in assistant.get("tool_calls", [])
            if isinstance(call, dict)
        }
        content = assistant.get("content")
        if isinstance(content, str) and len(content) > MAX_COMPACT_ASSISTANT_CONTENT_CHARS:
            assistant["content"] = (
                content[:MAX_COMPACT_ASSISTANT_CONTENT_CHARS]
                + "\n[Older assistant tool-call text compacted]"
            )

        compacted = 0
        for message in group.messages[1:]:
            if message.get("role") != "tool":
                continue
            call = calls_by_id.get(message.get("tool_call_id"), {})
            function = call.get("function", {}) if isinstance(call, dict) else {}
            tool_name = function.get("name", "unknown")
            arguments = ContextManager._parse_object(function.get("arguments"))
            result = ContextManager._parse_object(message.get("content"))
            summary: dict[str, Any] = {
                "compacted": True,
                "summary": "[Older tool result compacted]",
                "tool": tool_name,
                "success": result.get("success"),
            }
            original_content = message.get("content")
            if isinstance(original_content, str):
                summary["original_chars"] = len(original_content)
                summary["content_sha256"] = sha256(
                    original_content.encode("utf-8")
                ).hexdigest()[:16]
            for name in ("path", "query", "cwd", "command"):
                if name in arguments:
                    summary[name] = arguments[name]

            if result.get("success"):
                data = result.get("data", {})
                if isinstance(data, dict):
                    for name in (
                        "path",
                        "exit_code",
                        "timed_out",
                        "created",
                        "modified",
                        "truncated",
                        "stdout_truncated",
                        "stderr_truncated",
                    ):
                        if name in data:
                            summary[name] = data[name]
            elif "error" in result:
                summary["error"] = result["error"]

            compacted_content = json.dumps(summary, ensure_ascii=False, sort_keys=True)
            if message.get("content") != compacted_content:
                message["content"] = compacted_content
                compacted += 1
        return compacted

    @staticmethod
    def _parse_object(value: Any) -> dict[str, Any]:
        """将 JSON 字符串安全解析为对象，失败时返回空对象。"""
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _serialized_chars(messages: list[Message]) -> int:
        """使用稳定 JSON 序列化估算请求字符数。"""
        return len(
            json.dumps(
                messages,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    def _kept_chars(self, groups: list[_MessageGroup], kept: list[bool]) -> int:
        """计算当前保留消息组的序列化字符数。"""
        return self._serialized_chars(self._flatten(groups, kept))

    @staticmethod
    def _estimated_tokens(messages: list[Message]) -> int:
        """无需额外 tokenizer 依赖，按 ASCII/CJK 比例估算请求 token 数。"""
        serialized = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        ascii_chars = sum(1 for character in serialized if ord(character) < 128)
        non_ascii_chars = len(serialized) - ascii_chars
        return max(1, math.ceil(ascii_chars / 4) + math.ceil(non_ascii_chars / 1.5))

    def _within_budget(self, groups: list[_MessageGroup], kept: list[bool]) -> bool:
        output = self._flatten(groups, kept)
        return (
            self._serialized_chars(output) <= self.max_chars
            and self._estimated_tokens(output) <= self.max_tokens
        )

    @staticmethod
    def _flatten(groups: list[_MessageGroup], kept: list[bool]) -> list[Message]:
        """按原顺序展开仍被保留的消息组。"""
        return [
            message
            for index, group in enumerate(groups)
            if kept[index]
            for message in group.messages
        ]

    def _finish(
        self,
        original: list[Message],
        groups: list[_MessageGroup],
        kept: list[bool],
        input_chars: int,
        input_tokens: int,
        compacted_tool_results: int,
        dropped_groups: int,
    ) -> list[Message]:
        """保存统计并返回最终 Context View。"""
        output = self._flatten(groups, kept)
        output_chars = self._serialized_chars(output)
        output_tokens = self._estimated_tokens(output)
        self.last_stats = ContextStats(
            input_messages=len(original),
            output_messages=len(output),
            input_chars=input_chars,
            output_chars=output_chars,
            compacted_tool_results=compacted_tool_results,
            dropped_groups=dropped_groups,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        return output
