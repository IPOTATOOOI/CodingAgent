"""受工作区边界约束的文件系统工具。"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 1024 * 1024
MAX_READ_LINES = 500
MAX_SEARCH_RESULTS = 100
DEFAULT_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}
SAFE_ENV_EXAMPLE = ".env.example"


class ToolError(Exception):
    """可转换为结构化工具结果的受控异常。"""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


def resolve_workspace_path(workspace_root: Path, user_path: str) -> Path:
    """解析路径，并拒绝工作区以外的目标。"""
    root = workspace_root.resolve()
    try:
        target = (root / user_path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise ToolError("InvalidPath", "The requested path is invalid.") from None
    if target != root and root not in target.parents:
        raise ToolError(
            "PathOutsideWorkspace",
            "The requested path is outside the workspace.",
        )
    return target


class FilesystemTools:
    """提供受限的工作区读取、搜索、创建和编辑操作。"""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def list_directory(self, path: str = ".") -> dict[str, Any]:
        """列出目录的直接子项，不递归。"""
        target = resolve_workspace_path(self.workspace_root, path)
        if not target.exists():
            raise ToolError("FileNotFound", f"Directory '{path}' does not exist.")
        if not target.is_dir():
            raise ToolError("NotDirectory", f"Path '{path}' is not a directory.")

        entries = [
            {
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
            }
            for entry in target.iterdir()
        ]
        entries.sort(
            key=lambda item: (
                item["type"] != "directory",
                item["name"].casefold(),
            )
        )
        return {"path": self._relative_path(target), "entries": entries}

    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """按 1-based 行号读取有界的 UTF-8 文本。"""
        target = resolve_workspace_path(self.workspace_root, path)
        if self._is_sensitive_environment_file(target):
            raise ToolError(
                "SensitiveFile",
                "Environment files containing secrets cannot be read.",
            )
        if not target.exists():
            raise ToolError("FileNotFound", f"File '{path}' does not exist.")
        if not target.is_file():
            raise ToolError("NotFile", f"Path '{path}' is not a file.")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(
                "FileTooLarge",
                f"File '{path}' exceeds the {MAX_FILE_BYTES}-byte limit.",
            )

        first_line = 1 if start_line is None else start_line
        if first_line < 1:
            raise ToolError("InvalidArguments", "start_line must be at least 1.")
        if end_line is not None and end_line < first_line:
            raise ToolError(
                "InvalidArguments",
                "end_line must be greater than or equal to start_line.",
            )

        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            raise ToolError(
                "UnsupportedFileEncoding",
                "The file is not valid UTF-8 text.",
            ) from None

        requested_end = len(lines) if end_line is None else end_line
        available_end = min(requested_end, len(lines))
        actual_end = min(available_end, first_line + MAX_READ_LINES - 1)
        selected = lines[first_line - 1 : actual_end]
        numbered_content = "\n".join(
            f"{line_number} | {line}"
            for line_number, line in enumerate(selected, start=first_line)
        )
        return {
            "path": self._relative_path(target),
            "start_line": first_line,
            "end_line": actual_end,
            "total_lines": len(lines),
            "content": numbered_content,
            "truncated": available_end > actual_end,
        }

    def search_text(
        self,
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> dict[str, Any]:
        """在 UTF-8 文件中进行区分大小写的字面字符串搜索。"""
        if not query:
            raise ToolError("InvalidArguments", "query must not be empty.")
        if max_results < 1 or max_results > MAX_SEARCH_RESULTS:
            raise ToolError(
                "InvalidArguments",
                f"max_results must be between 1 and {MAX_SEARCH_RESULTS}.",
            )

        target = resolve_workspace_path(self.workspace_root, path)
        if not target.exists():
            raise ToolError("FileNotFound", f"Path '{path}' does not exist.")

        matches: list[dict[str, Any]] = []
        for file_path in self._iter_search_files(target):
            if self._is_sensitive_environment_file(file_path):
                continue
            try:
                file_size = file_path.stat().st_size
            except OSError:
                continue
            if file_size > MAX_FILE_BYTES:
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue

            for line_number, line in enumerate(lines, start=1):
                if query not in line:
                    continue
                matches.append(
                    {
                        "path": self._relative_path(file_path),
                        "line": line_number,
                        "text": line,
                    }
                )
                if len(matches) > max_results:
                    return {
                        "query": query,
                        "matches": matches[:max_results],
                        "truncated": True,
                    }

        return {"query": query, "matches": matches, "truncated": False}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        """在工作区中创建新的 UTF-8 文本文件，不覆盖已有路径。"""
        target = resolve_workspace_path(self.workspace_root, path)
        if target.exists():
            raise ToolError(
                "FileAlreadyExists",
                f"File '{path}' already exists. Use edit_file to modify existing files.",
            )
        if not target.parent.exists() or not target.parent.is_dir():
            raise ToolError(
                "ParentDirectoryNotFound",
                f"Parent directory for '{path}' does not exist.",
            )

        encoded_content = content.encode("utf-8")
        if len(encoded_content) > MAX_FILE_BYTES:
            raise ToolError(
                "FileTooLarge",
                f"Content for '{path}' exceeds the {MAX_FILE_BYTES}-byte limit.",
            )

        try:
            with target.open("x", encoding="utf-8", newline="") as file:
                file.write(content)
        except FileExistsError:
            raise ToolError(
                "FileAlreadyExists",
                f"File '{path}' already exists. Use edit_file to modify existing files.",
            ) from None

        return {
            "path": self._relative_path(target),
            "created": True,
            "bytes_written": len(encoded_content),
        }

    def edit_file(
        self,
        path: str,
        old_text: str,
        new_text: str,
    ) -> dict[str, Any]:
        """用唯一匹配的旧文本替换现有 UTF-8 文件中的一个片段。"""
        if not old_text:
            raise ToolError("InvalidArguments", "old_text must not be empty.")
        if old_text == new_text:
            raise ToolError("NoChange", "old_text and new_text are identical.")

        target = resolve_workspace_path(self.workspace_root, path)
        if not target.exists():
            raise ToolError("FileNotFound", f"File '{path}' does not exist.")
        if not target.is_file():
            raise ToolError("NotFile", f"Path '{path}' is not a file.")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ToolError(
                "FileTooLarge",
                f"File '{path}' exceeds the {MAX_FILE_BYTES}-byte limit.",
            )

        try:
            content = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(
                "UnsupportedFileEncoding",
                "The file is not valid UTF-8 text.",
            ) from None

        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ToolError(
                "TextNotFound",
                "The specified old_text was not found in the file.",
            )
        if occurrences > 1:
            raise ToolError(
                "AmbiguousEdit",
                "The specified old_text occurs multiple times. "
                "Provide a larger unique context.",
            )

        match_index = content.index(old_text)
        start_line = content.count("\n", 0, match_index) + 1
        old_line_count = max(1, len(old_text.splitlines()))
        new_line_count = len(new_text.splitlines())
        updated_content = content.replace(old_text, new_text, 1)
        encoded_content = updated_content.encode("utf-8")
        if len(encoded_content) > MAX_FILE_BYTES:
            raise ToolError(
                "FileTooLarge",
                f"Edited file '{path}' would exceed the {MAX_FILE_BYTES}-byte limit.",
            )
        target.write_bytes(encoded_content)
        return {
            "path": self._relative_path(target),
            "modified": True,
            "replacements": 1,
            "start_line": start_line,
            "old_end_line": start_line + old_line_count - 1,
            "new_end_line": (
                start_line + new_line_count - 1 if new_line_count else start_line - 1
            ),
        }

    def _iter_search_files(self, target: Path) -> Iterator[Path]:
        """稳定地产生可搜索文件，并跳过明显的大型目录。"""
        if target.is_file():
            resolved = resolve_workspace_path(self.workspace_root, str(target))
            yield resolved
            return
        if not target.is_dir():
            raise ToolError("NotDirectory", "The search path is not a directory.")

        for current_root, directories, filenames in os.walk(target):
            directories[:] = sorted(
                (name for name in directories if name not in DEFAULT_IGNORED_DIRS),
                key=str.casefold,
            )
            for filename in sorted(filenames, key=str.casefold):
                candidate = Path(current_root) / filename
                try:
                    yield resolve_workspace_path(self.workspace_root, str(candidate))
                except ToolError:
                    continue

    def _relative_path(self, path: Path) -> str:
        """返回适合跨平台展示和传给模型的工作区相对路径。"""
        relative = path.relative_to(self.workspace_root)
        return "." if not relative.parts else relative.as_posix()

    @staticmethod
    def _is_sensitive_environment_file(path: Path) -> bool:
        """识别应避免发送给远端模型的环境密钥文件。"""
        name = path.name.casefold()
        return name == ".env" or (
            name.startswith(".env.") and name != SAFE_ENV_EXAMPLE
        )
