"""受执行时间、输出大小和工作目录约束的本地命令工具。"""

import os
from pathlib import Path
import subprocess
import time
from typing import Any

from coding_agent.tools.filesystem import ToolError, resolve_workspace_path


MIN_TIMEOUT_SECONDS = 1
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
MAX_STDOUT_CHARS = 12_000
MAX_STDERR_CHARS = 12_000
AGENT_SECRET_NAMES = {"LLM_API_KEY"}


class CommandTools:
    """提供不经过 Shell 的非交互式本地命令执行。"""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def run_command(
        self,
        command: list[str],
        cwd: str = ".",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """运行一个参数数组表示的命令，并返回有界的执行结果。"""
        self._validate_arguments(command, cwd, timeout_seconds)
        resolved_cwd = resolve_workspace_path(self.workspace_root, cwd)
        if not resolved_cwd.exists():
            raise ToolError(
                "WorkingDirectoryNotFound",
                f"Working directory '{cwd}' does not exist.",
            )
        if not resolved_cwd.is_dir():
            raise ToolError("NotDirectory", f"Path '{cwd}' is not a directory.")

        execution_env = os.environ.copy()
        for secret_name in AGENT_SECRET_NAMES:
            execution_env.pop(secret_name, None)

        started_at = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=resolved_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                stdin=subprocess.DEVNULL,
                env=execution_env,
                check=False,
            )
        except FileNotFoundError:
            raise ToolError(
                "CommandNotFound",
                f"Executable '{command[0]}' was not found.",
            ) from None
        except subprocess.TimeoutExpired as error:
            duration_ms = round((time.monotonic() - started_at) * 1000)
            return self._result(
                command=command,
                cwd=resolved_cwd,
                exit_code=None,
                timed_out=True,
                stdout=self._decode_timeout_output(error.stdout),
                stderr=self._decode_timeout_output(error.stderr),
                duration_ms=duration_ms,
            )
        except OSError:
            raise ToolError(
                "CommandExecutionError",
                f"Executable '{command[0]}' could not be started.",
            ) from None

        duration_ms = round((time.monotonic() - started_at) * 1000)
        return self._result(
            command=command,
            cwd=resolved_cwd,
            exit_code=completed.returncode,
            timed_out=False,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _validate_arguments(
        command: list[str], cwd: str, timeout_seconds: int
    ) -> None:
        """验证直接调用时的参数，避免只依赖 Registry schema。"""
        if not isinstance(command, list) or not command:
            raise ToolError(
                "InvalidArguments",
                "command must contain at least one argument.",
            )
        if any(not isinstance(argument, str) or not argument for argument in command):
            raise ToolError(
                "InvalidArguments",
                "every command argument must be a non-empty string.",
            )
        if not isinstance(cwd, str):
            raise ToolError("InvalidArguments", "cwd must be a string.")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            raise ToolError(
                "InvalidArguments", "timeout_seconds must be an integer."
            )
        if not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ToolError(
                "InvalidArguments",
                f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} "
                f"and {MAX_TIMEOUT_SECONDS}.",
            )

    def _result(
        self,
        command: list[str],
        cwd: Path,
        exit_code: int | None,
        timed_out: bool,
        stdout: str,
        stderr: str,
        duration_ms: int,
    ) -> dict[str, Any]:
        """构造带独立输出截断状态的命令结果。"""
        bounded_stdout, stdout_truncated = self._truncate(
            stdout, MAX_STDOUT_CHARS
        )
        bounded_stderr, stderr_truncated = self._truncate(
            stderr, MAX_STDERR_CHARS
        )
        relative_cwd = cwd.relative_to(self.workspace_root)
        return {
            "command": command,
            "cwd": "." if not relative_cwd.parts else relative_cwd.as_posix(),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": bounded_stdout,
            "stderr": bounded_stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": duration_ms,
        }

    @staticmethod
    def _truncate(content: str, limit: int) -> tuple[str, bool]:
        """裁剪传给模型的字符数，并标记是否发生裁剪。"""
        if len(content) > limit:
            return content[:limit], True
        return content, False

    @staticmethod
    def _decode_timeout_output(output: str | bytes | None) -> str:
        """统一 TimeoutExpired 中可能出现的文本或字节输出。"""
        if output is None:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return output
