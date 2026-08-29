"""Conversation 的有界、原子持久化与按 Workspace 恢复。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

from coding_agent.context import ContextManager
from coding_agent.conversation import Conversation, Message


SESSION_VERSION = 1
DEFAULT_MAX_SESSION_BYTES = 2 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_SESSIONS = 20
DEFAULT_RECENT_GROUPS = 12
SESSION_METADATA_RESERVE_BYTES = 4096
STALE_TEMP_SECONDS = 24 * 60 * 60


class SessionTooLargeError(ValueError):
    """会话在协议安全压缩后仍然超过磁盘存储硬限制。"""


@dataclass(frozen=True)
class SessionSnapshot:
    """磁盘会话中允许恢复的稳定数据。"""

    workspace: Path
    model: str
    messages: list[Message]
    updated_at: str
    compacted: bool = False

    def to_conversation(self) -> Conversation:
        return Conversation.from_messages(self.messages)


class SessionStore:
    """提供有界存储、保留策略和原子替换，且不接触 Agent Workspace。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_bytes: int = DEFAULT_MAX_SESSION_BYTES,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        recent_groups: int = DEFAULT_RECENT_GROUPS,
    ) -> None:
        if max_bytes <= SESSION_METADATA_RESERVE_BYTES:
            raise ValueError("max_bytes is too small for session metadata.")
        if retention_days < 1:
            raise ValueError("retention_days must be positive.")
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive.")
        if recent_groups < 1:
            raise ValueError("recent_groups must be positive.")
        self.root = (root or self.default_root()).resolve()
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.max_sessions = max_sessions
        self.recent_groups = recent_groups

    @staticmethod
    def default_root() -> Path:
        local_data = os.getenv("LOCALAPPDATA", "").strip()
        base = Path(local_data) if local_data else Path.home() / ".mini-coding-agent"
        return base / "MiniCodingAgent" / "sessions"

    def path_for(self, workspace: Path) -> Path:
        # normcase 只在大小写不敏感的平台折叠大小写，避免 Linux 路径哈希碰撞。
        normalized = os.path.normcase(str(workspace.resolve())).encode("utf-8")
        identity = sha256(normalized).hexdigest()[:20]
        return self.root / f"{identity}.json"

    def save(
        self,
        conversation: Conversation,
        workspace: Path,
        model: str,
    ) -> Path:
        """从 Conversation 副本原子写入会话。"""
        return self.save_messages(
            conversation.messages,
            workspace,
            model,
        )

    def save_messages(
        self,
        messages: list[Message],
        workspace: Path,
        model: str,
    ) -> Path:
        """保存消息快照；超限时按协议组压缩，仍超限则拒绝写入。"""
        target = self.path_for(workspace)
        self._ensure_root()
        validated_messages = Conversation.from_messages(messages).messages
        updated_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "version": SESSION_VERSION,
            "workspace": str(workspace.resolve()),
            "model": model if isinstance(model, str) else "",
            "messages": validated_messages,
            "updated_at": updated_at,
            "storage": {"compacted": False},
        }
        serialized = self._serialize(payload)
        if len(serialized) > self.max_bytes:
            message_budget = max(
                1,
                (self.max_bytes - SESSION_METADATA_RESERVE_BYTES) // 4,
            )
            manager = ContextManager(
                max_chars=message_budget,
                max_tokens=1_000_000_000,
                recent_groups=self.recent_groups,
            )
            compacted_messages = manager.build_context(validated_messages)
            payload["messages"] = compacted_messages
            payload["storage"] = {
                "compacted": True,
                "input_messages": manager.last_stats.input_messages,
                "output_messages": manager.last_stats.output_messages,
                "compacted_tool_results": (
                    manager.last_stats.compacted_tool_results
                ),
                "dropped_groups": manager.last_stats.dropped_groups,
            }
            serialized = self._serialize(payload)
        if len(serialized) > self.max_bytes:
            raise SessionTooLargeError(
                "session exceeds the storage limit after safe compaction."
            )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.stem}-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(serialized.decode("utf-8"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            if os.name != "nt":
                target.chmod(0o600)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        self.cleanup()
        return target

    def load(self, workspace: Path) -> SessionSnapshot | None:
        target = self.path_for(workspace)
        if not target.exists():
            return None
        if target.stat().st_size > self.max_bytes:
            raise SessionTooLargeError("session file exceeds the configured limit.")
        try:
            payload: Any = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"session file is unreadable: {error}") from error
        if not isinstance(payload, dict) or payload.get("version") != SESSION_VERSION:
            raise ValueError("session file version is not supported.")
        stored_workspace = Path(str(payload.get("workspace", ""))).resolve()
        if stored_workspace != workspace.resolve():
            raise ValueError("session workspace does not match the selected workspace.")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("session messages are invalid.")
        conversation = Conversation.from_messages(messages)
        storage = payload.get("storage", {})
        return SessionSnapshot(
            workspace=stored_workspace,
            model=str(payload.get("model", "")),
            messages=conversation.messages,
            updated_at=str(payload.get("updated_at", "")),
            compacted=(
                bool(storage.get("compacted"))
                if isinstance(storage, dict)
                else False
            ),
        )

    def delete(self, workspace: Path) -> bool:
        target = self.path_for(workspace)
        if not target.exists():
            return False
        target.unlink()
        return True

    def clear_all(self) -> int:
        """只删除 SessionStore 根目录中的会话 JSON，不递归触碰其他文件。"""
        if not self.root.exists():
            return 0
        removed = 0
        for pattern in ("*.json", ".*.tmp"):
            for path in self.root.glob(pattern):
                if not path.is_file():
                    continue
                path.unlink()
                removed += 1
        return removed

    def cleanup(self, *, now: float | None = None) -> int:
        """删除超期会话，并把最近会话数量限制在 ``max_sessions``。"""
        if not self.root.exists():
            return 0
        current_time = time.time() if now is None else now
        cutoff = current_time - self.retention_days * 24 * 60 * 60
        temporary_cutoff = current_time - STALE_TEMP_SECONDS
        removed = 0
        for path in self.root.glob(".*.tmp"):
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                continue
            if path.is_file() and modified_at < temporary_cutoff:
                path.unlink()
                removed += 1
        candidates = [
            path
            for path in self.root.glob("*.json")
            if path.is_file()
        ]
        retained: list[Path] = []
        for path in candidates:
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                continue
            if modified_at < cutoff:
                path.unlink()
                removed += 1
            else:
                retained.append(path)
        retained.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in retained[self.max_sessions :]:
            path.unlink()
            removed += 1
        return removed

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)

    @staticmethod
    def _serialize(payload: dict[str, Any]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
