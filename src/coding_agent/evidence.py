"""可导出、可回放且不包含完整工具正文的任务 Evidence Trail。"""

from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from uuid import uuid4

from coding_agent.agent import AgentResult
from coding_agent.events import RuntimeEvent, RuntimeEventKind


EVIDENCE_VERSION = 1
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
MAX_DIFF_CHARS = 8_000
MAX_OUTPUT_CHARS = 1_200
DEFAULT_MAX_TRACES = 100
DEFAULT_RETENTION_DAYS = 30
PRIVATE_ARGUMENTS = {"content", "old_text", "new_text"}


def default_evidence_root() -> Path:
    """返回与平台匹配的应用 Trace 存储目录。"""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "MiniCodingAgent" / "traces"
    return Path.home() / ".mini-coding-agent" / "MiniCodingAgent" / "traces"


class EvidenceTrailBuilder:
    """从统一 Runtime Event 流增量构造结构化任务证据。"""

    def __init__(self, workspace: Path, model: str, max_steps: int) -> None:
        self._started_monotonic = time.monotonic()
        self._raw_arguments: dict[str, dict[str, Any]] = {}
        self._tool_indexes: dict[str, int] = {}
        self._created_files: set[str] = set()
        self._modified_files: set[str] = set()
        self._created_directories: set[str] = set()
        self.data: dict[str, Any] = {
            "version": EVIDENCE_VERSION,
            "trace_id": uuid4().hex,
            "task": "",
            "workspace": str(workspace.resolve()),
            "model": model,
            "max_steps": max_steps,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "steps": 0,
            "tool_calls": 0,
            "files_created": 0,
            "files_modified": 0,
            "directories_created": 0,
            "verification": "not_required",
            "stop_reason": "running",
            "duration": 0.0,
            "tools": [],
            "approvals": [],
            "verification_events": [],
        }

    def record_approval(
        self,
        request: dict[str, Any],
        approved: bool,
    ) -> None:
        """记录用户授权结果；请求中已经不含文件正文。"""
        self.data["approvals"].append(
            {
                "request_id": request.get("request_id"),
                "tool": request.get("tool_name"),
                "arguments": request.get("arguments", {}),
                "approved": approved,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def record(self, event: RuntimeEvent) -> None:
        """消费一个 Runtime Event；未知字段不会进入稳定导出结构。"""
        payload = event.payload
        if event.kind == RuntimeEventKind.TASK_STARTED:
            self.data["task"] = str(payload.get("task", ""))
            return
        if event.kind == RuntimeEventKind.TOOL_STARTED:
            self._record_tool_started(event)
            return
        if event.kind == RuntimeEventKind.TOOL_FINISHED:
            self._record_tool_finished(event)
            return
        if event.kind == RuntimeEventKind.VERIFICATION_CHANGED:
            status = str(payload.get("status", self.data["verification"]))
            self.data["verification"] = status
            self.data["verification_events"].append(
                {
                    "step": event.step,
                    "status": status,
                    "outcome": payload.get("outcome"),
                    "pending_paths": list(payload.get("pending_paths", [])),
                    "created_at": event.created_at,
                }
            )
            return
        if event.kind == RuntimeEventKind.TASK_FINISHED:
            self.data["stop_reason"] = str(payload.get("stop_reason", "unknown"))
            self.data["steps"] = int(payload.get("steps", event.step or 0))
            self.data["tool_calls"] = int(
                payload.get("tool_calls", len(self.data["tools"]))
            )
            self.data["verification"] = str(
                payload.get("verification_status", self.data["verification"])
            )
            self._finish()

    def finalize(self, result: AgentResult) -> dict[str, Any]:
        """使用 AgentResult 补齐最终字段并返回可序列化快照。"""
        self.data["stop_reason"] = result.stop_reason
        self.data["steps"] = result.steps
        self.data["tool_calls"] = result.tool_calls
        self.data["verification"] = result.verification_status
        self._finish()
        return self.snapshot()

    def fail(self, message: str) -> dict[str, Any]:
        """记录 Worker 边界异常，但不保存异常堆栈或凭据。"""
        self.data["stop_reason"] = "worker_error"
        self.data["error"] = str(message)[:1_000]
        self._finish()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """通过 JSON round-trip 返回不共享内部引用的快照。"""
        return json.loads(json.dumps(self.data, ensure_ascii=False))

    def _record_tool_started(self, event: RuntimeEvent) -> None:
        payload = event.payload
        call_id = str(payload.get("tool_call_id", ""))
        raw = _json_object(payload.get("arguments", "{}"))
        self._raw_arguments[call_id] = raw
        record = {
            "id": call_id,
            "step": event.step or 0,
            "tool": str(payload.get("tool_name", "unknown")),
            "arguments": _safe_arguments(raw),
            "started_at": event.created_at,
            "finished_at": None,
            "success": None,
            "error": None,
            "result": {},
            "diff": "",
        }
        self._tool_indexes[call_id] = len(self.data["tools"])
        self.data["tools"].append(record)

    def _record_tool_finished(self, event: RuntimeEvent) -> None:
        payload = event.payload
        call_id = str(payload.get("tool_call_id", ""))
        index = self._tool_indexes.get(call_id)
        if index is None:
            self._record_tool_started(event)
            index = self._tool_indexes[call_id]
        record = self.data["tools"][index]
        result = payload.get("result", {})
        result = result if isinstance(result, dict) else {}
        record["finished_at"] = event.created_at
        record["success"] = bool(result.get("success"))
        record["error"] = result.get("error")
        record["result"] = _safe_result(result)
        record["verification"] = payload.get("verification_status")
        arguments = self._raw_arguments.get(call_id, {})
        record["diff"] = _change_diff(record["tool"], arguments)
        self._update_change_counts(record["tool"], result)

    def _update_change_counts(self, tool_name: str, result: dict[str, Any]) -> None:
        if not result.get("success"):
            return
        data = result.get("data", {})
        if not isinstance(data, dict):
            return
        path = data.get("path")
        if not isinstance(path, str):
            return
        if tool_name == "write_file" and data.get("created"):
            self._created_files.add(path)
        elif tool_name == "edit_file" and data.get("modified"):
            self._modified_files.add(path)
        elif tool_name == "create_directory" and data.get("created"):
            created = data.get("created_directories", [path])
            if isinstance(created, list):
                self._created_directories.update(str(item) for item in created)
        self.data["files_created"] = len(self._created_files)
        self.data["files_modified"] = len(self._modified_files)
        self.data["directories_created"] = len(self._created_directories)

    def _finish(self) -> None:
        if self.data["completed_at"] is None:
            self.data["completed_at"] = datetime.now(timezone.utc).isoformat()
        self.data["duration"] = round(time.monotonic() - self._started_monotonic, 3)


class EvidenceStore:
    """原子保存自动 Trace，并支持显式导出和只读加载。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_traces: int = DEFAULT_MAX_TRACES,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if max_traces < 1 or retention_days < 1:
            raise ValueError("evidence retention values must be positive.")
        self.root = (root or default_evidence_root()).resolve()
        self.max_traces = max_traces
        self.retention_days = retention_days

    def save(self, snapshot: dict[str, Any]) -> Path:
        trace_id = str(snapshot.get("trace_id") or uuid4().hex)
        target = self.root / f"{trace_id}.json"
        path = self._write(snapshot, target)
        self.cleanup()
        return path

    def export(self, snapshot: dict[str, Any], target: Path) -> Path:
        return self._write(snapshot, target.resolve())

    def load(self, path: Path) -> dict[str, Any]:
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise ValueError("evidence trace exceeds the size limit.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != EVIDENCE_VERSION:
            raise ValueError("evidence trace version is not supported.")
        if not isinstance(value.get("tools"), list):
            raise ValueError("evidence trace tools are invalid.")
        return value

    def cleanup(self) -> int:
        """只清理自动 Trace 根目录中的过期或超额 JSON。"""
        if not self.root.exists():
            return 0
        files = sorted(
            self.root.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        cutoff = time.time() - self.retention_days * 24 * 60 * 60
        removed = 0
        for index, path in enumerate(files):
            if index >= self.max_traces or path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        return removed

    def _write(self, snapshot: dict[str, Any], target: Path) -> Path:
        serialized = json.dumps(
            snapshot, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        if len(serialized) > MAX_EVIDENCE_BYTES:
            raise ValueError("evidence trace exceeds the size limit.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=target.parent, prefix=f".{target.stem}-", delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(serialized)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return target


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for name, value in arguments.items():
        if name in PRIVATE_ARGUMENTS:
            length = len(value) if isinstance(value, str) else 0
            safe[name] = f"<{length} chars omitted>"
        else:
            safe[name] = value
    return safe


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("success"):
        return {
            "error": result.get("error"),
            "message": str(result.get("message", ""))[:1_000],
        }
    data = result.get("data", {})
    if not isinstance(data, dict):
        return {}
    allowed = {
        "path", "created", "modified", "created_directories", "bytes_written",
        "replacements", "start_line", "old_end_line", "new_end_line", "cwd",
        "command", "exit_code", "timed_out", "cancelled", "duration_ms",
        "stdout_truncated", "stderr_truncated",
    }
    safe = {key: value for key, value in data.items() if key in allowed}
    for name in ("stdout", "stderr"):
        if name in data:
            safe[f"{name}_preview"] = str(data[name])[:MAX_OUTPUT_CHARS]
    return safe


def _change_diff(tool_name: str, arguments: dict[str, Any]) -> str:
    path = str(arguments.get("path", "file"))
    if tool_name == "write_file":
        content = str(arguments.get("content", ""))
        lines = [f"+++ {path}", *(f"+{line}" for line in content.splitlines())]
    elif tool_name == "edit_file":
        lines = list(
            difflib.unified_diff(
                str(arguments.get("old_text", "")).splitlines(),
                str(arguments.get("new_text", "")).splitlines(),
                fromfile=f"{path} (before)",
                tofile=f"{path} (after)",
                lineterm="",
            )
        )
    else:
        return ""
    text = "\n".join(lines[:100])
    return text[:MAX_DIFF_CHARS]
