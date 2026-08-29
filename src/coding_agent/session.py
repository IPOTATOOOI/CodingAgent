"""Conversation 的原子持久化与按 Workspace 恢复。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from coding_agent.conversation import Conversation, Message


SESSION_VERSION = 1


@dataclass(frozen=True)
class SessionSnapshot:
    """磁盘会话中允许恢复的稳定数据。"""

    workspace: Path
    model: str
    messages: list[Message]
    updated_at: str

    def to_conversation(self) -> Conversation:
        return Conversation.from_messages(self.messages)


class SessionStore:
    """使用临时文件加 ``os.replace``，避免中断时留下半个 JSON。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or self.default_root()).resolve()

    @staticmethod
    def default_root() -> Path:
        local_data = os.getenv("LOCALAPPDATA", "").strip()
        base = Path(local_data) if local_data else Path.home() / ".mini-coding-agent"
        return base / "MiniCodingAgent" / "sessions"

    def path_for(self, workspace: Path) -> Path:
        normalized = str(workspace.resolve()).casefold().encode("utf-8")
        identity = sha256(normalized).hexdigest()[:20]
        return self.root / f"{identity}.json"

    def save(
        self,
        conversation: Conversation,
        workspace: Path,
        model: str,
    ) -> Path:
        """原子写入会话；仅保存消息、模型名和 Workspace 元数据。"""
        target = self.path_for(workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SESSION_VERSION,
            "workspace": str(workspace.resolve()),
            "model": model,
            "messages": conversation.messages,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
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
                json.dump(payload, temporary, ensure_ascii=False, indent=2)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return target

    def load(self, workspace: Path) -> SessionSnapshot | None:
        target = self.path_for(workspace)
        if not target.exists():
            return None
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
        return SessionSnapshot(
            workspace=stored_workspace,
            model=str(payload.get("model", "")),
            messages=conversation.messages,
            updated_at=str(payload.get("updated_at", "")),
        )

    def delete(self, workspace: Path) -> bool:
        target = self.path_for(workspace)
        if not target.exists():
            return False
        target.unlink()
        return True
