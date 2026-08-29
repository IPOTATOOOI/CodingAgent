"""代码修改后的执行验证状态与命令分类。"""

from pathlib import Path
import json
import posixpath
import re
from typing import Any

from coding_agent.llm import ToolCall


VERIFICATION_NOT_REQUIRED = "not_required"
VERIFICATION_UNVERIFIED = "unverified"
VERIFICATION_FAILED = "failed"
VERIFICATION_VERIFIED = "verified"

VERIFIABLE_SUFFIXES = {
    ".py",
    ".pyw",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".cc",
    ".hpp",
    ".go",
    ".rs",
    ".cs",
    ".rb",
    ".php",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}


class VerificationTracker:
    """跟踪最近一代代码修改是否已有真实执行证据。"""

    def __init__(self) -> None:
        self.reset_task()

    def reset_task(self) -> None:
        """为新的用户任务重置验证状态。"""
        self.mutation_generation = 0
        self.verified_generation = 0
        self.verification_status = VERIFICATION_NOT_REQUIRED
        self.pending_mutation_paths: set[str] = set()

    @property
    def completion_blocked(self) -> bool:
        """返回最新一代修改是否仍然缺少成功验证。"""
        return self.mutation_generation > self.verified_generation

    def record_tool_result(
        self,
        tool_call: ToolCall,
        result: dict[str, Any],
    ) -> None:
        """根据真实工具结果更新 mutation generation 和验证状态。"""
        if self._is_verifiable_mutation(tool_call, result):
            data = result["data"]
            self.mutation_generation += 1
            self.verification_status = VERIFICATION_UNVERIFIED
            self.pending_mutation_paths.add(_normalized_relative_path(data["path"]))
            return

        if tool_call.name != "run_command" or not self.completion_blocked:
            return

        command = self._command_arguments(tool_call)
        if command is None or not is_verification_command(command):
            return
        if not result.get("success"):
            return

        data = result.get("data", {})
        if not isinstance(data, dict):
            return
        cwd = data.get("cwd", ".")
        if not isinstance(cwd, str) or not verification_command_covers_paths(
            command,
            cwd,
            self.pending_mutation_paths,
        ):
            return
        if data.get("timed_out"):
            self.verification_status = VERIFICATION_FAILED
            return
        if data.get("exit_code") == 0:
            self.verified_generation = self.mutation_generation
            self.verification_status = VERIFICATION_VERIFIED
            self.pending_mutation_paths.clear()
        else:
            self.verification_status = VERIFICATION_FAILED

    @staticmethod
    def _is_verifiable_mutation(
        tool_call: ToolCall,
        result: dict[str, Any],
    ) -> bool:
        """判断结果是否成功修改了需要执行验证的文件。"""
        if tool_call.name not in {"write_file", "edit_file"}:
            return False
        if not result.get("success"):
            return False
        data = result.get("data", {})
        if not isinstance(data, dict):
            return False
        if not (data.get("created") or data.get("modified")):
            return False
        path = data.get("path")
        return isinstance(path, str) and Path(path).suffix.casefold() in VERIFIABLE_SUFFIXES

    @staticmethod
    def _command_arguments(tool_call: ToolCall) -> list[str] | None:
        """从 run_command Tool Call 中安全提取参数数组。"""
        try:
            arguments = json.loads(tool_call.arguments)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(arguments, dict):
            return None
        command = arguments.get("command")
        if not isinstance(command, list) or not command:
            return None
        if any(not isinstance(part, str) for part in command):
            return None
        return command


def is_verification_command(command: list[str]) -> bool:
    """使用小型确定性规则识别测试、构建和语法检查命令。"""
    if not command:
        return False
    executable = _normalized_executable(command[0])
    arguments = [argument.casefold() for argument in command[1:]]

    if _is_pytest_executable(executable):
        return True
    if _is_python_executable(executable):
        return _is_python_verification(arguments)

    if executable == "npm":
        return arguments[:1] == ["test"] or arguments[:2] in (
            ["run", "test"],
            ["run", "build"],
        )
    if executable in {"yarn", "pnpm"}:
        return arguments[:1] == ["test"]
    if executable == "go":
        return arguments[:1] == ["test"]
    if executable == "cargo":
        return arguments[:1] in (["test"], ["check"])
    if executable == "mvn":
        return any(argument in {"test", "verify"} for argument in arguments)
    if executable in {"gradle", "gradlew"}:
        return any(argument == "test" for argument in arguments)
    return False


def verification_command_covers_paths(
    command: list[str],
    cwd: str,
    mutated_paths: set[str],
) -> bool:
    """判断窄范围语法检查是否覆盖全部待验证修改路径。"""
    if not mutated_paths or not command:
        return False
    executable = _normalized_executable(command[0])
    arguments = [argument.casefold() for argument in command[1:]]
    if not _is_python_executable(executable):
        return True
    if len(arguments) < 2 or arguments[0] != "-m":
        return True

    module = arguments[1]
    targets = [
        _command_target_path(cwd, argument)
        for argument in command[3:]
        if argument and not argument.startswith("-")
    ]
    if module == "py_compile":
        return bool(targets) and mutated_paths.issubset(set(targets))
    if module == "compileall":
        coverage_roots = targets or [_normalized_relative_path(cwd)]
        return all(
            any(_path_is_within(path, root) for root in coverage_roots)
            for path in mutated_paths
        )
    return True


def _is_python_verification(arguments: list[str]) -> bool:
    """识别 Python 模块检查和直接测试脚本。"""
    if len(arguments) >= 2 and arguments[0] == "-m":
        return arguments[1] in {"pytest", "unittest", "compileall", "py_compile"}
    if not arguments:
        return False
    script_name = Path(arguments[0]).name
    return bool(
        re.fullmatch(r"test_.+\.py", script_name)
        or re.fullmatch(r".+_test\.py", script_name)
    )


def _normalized_executable(value: str) -> str:
    """提取不含常见启动器后缀的跨平台可执行文件名。"""
    name = Path(value).name.casefold()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _command_target_path(cwd: str, target: str) -> str:
    """把命令目标规范化为 workspace 相对 POSIX 路径。"""
    normalized_target = target.replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", normalized_target) or normalized_target.startswith("/"):
        return normalized_target.casefold()
    return _normalized_relative_path(posixpath.join(cwd, normalized_target))


def _normalized_relative_path(value: str) -> str:
    """规范化工具结果中的 workspace 相对路径。"""
    normalized = posixpath.normpath(value.replace("\\", "/"))
    return "." if normalized in {"", "."} else normalized.casefold()


def _path_is_within(path: str, root: str) -> bool:
    """判断相对文件路径是否位于指定相对目录内。"""
    if root == ".":
        return not path.startswith("../") and not path.startswith("/")
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_python_executable(executable: str) -> bool:
    """识别 python、pythonw、py 及其版本化名称。"""
    return executable == "py" or bool(
        re.fullmatch(r"pythonw?(?:\d+(?:\.\d+)*)?", executable)
    )


def _is_pytest_executable(executable: str) -> bool:
    """识别 pytest 及其版本化可执行文件名。"""
    return bool(re.fullmatch(r"pytest(?:\d+(?:\.\d+)*)?", executable))
