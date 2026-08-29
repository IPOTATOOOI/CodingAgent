"""运行中的 steering 与后续 follow-up 消息队列。"""

from collections import deque
from threading import Lock


class AgentMessageQueue:
    """供 GUI/其他线程安全地向 Agent 追加用户意图。"""

    def __init__(self) -> None:
        self._steering: deque[str] = deque()
        self._follow_ups: deque[str] = deque()
        self._lock = Lock()

    def add_steering(self, content: str) -> None:
        """让消息在当前任务的下一个 Agent Step 生效。"""
        normalized = self._normalize(content)
        with self._lock:
            self._steering.append(normalized)

    def add_follow_up(self, content: str) -> None:
        """让消息在当前任务完成后作为下一项任务处理。"""
        normalized = self._normalize(content)
        with self._lock:
            self._follow_ups.append(normalized)

    def drain_steering(self) -> list[str]:
        """按加入顺序一次取出当前全部 steering 消息。"""
        with self._lock:
            messages = list(self._steering)
            self._steering.clear()
        return messages

    def pop_follow_up(self) -> str | None:
        """取出最早的一条后续任务；队列为空时返回 ``None``。"""
        with self._lock:
            return self._follow_ups.popleft() if self._follow_ups else None

    @property
    def pending_steering(self) -> int:
        with self._lock:
            return len(self._steering)

    @property
    def pending_follow_ups(self) -> int:
        with self._lock:
            return len(self._follow_ups)

    @staticmethod
    def _normalize(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("queued message must be a non-empty string.")
        return content.strip()
