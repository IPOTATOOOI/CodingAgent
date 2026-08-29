"""文件工具的 schema、参数验证和分发。"""

from collections.abc import Callable
from dataclasses import dataclass
import inspect
import json
from pathlib import Path
import types
from typing import Any, get_args, get_origin, get_type_hints, Union

from coding_agent.tools.command import (
    CommandTools,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
)
from coding_agent.tools.filesystem import FilesystemTools, ToolError


ToolHandler = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    """一个工具的模型 schema 和本地处理函数。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    @classmethod
    def from_callable(
        cls,
        handler: ToolHandler,
        description: str,
        *,
        name: str | None = None,
        parameter_descriptions: dict[str, str] | None = None,
        parameter_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> "ToolDefinition":
        """从函数签名生成基础 schema，并允许显式补充安全约束。"""
        signature = inspect.signature(handler)
        hints = get_type_hints(handler)
        properties: dict[str, dict[str, Any]] = {}
        required: list[str] = []
        descriptions = parameter_descriptions or {}
        overrides = parameter_overrides or {}
        for parameter_name, parameter in signature.parameters.items():
            annotation = hints.get(parameter_name, parameter.annotation)
            schema = cls._annotation_schema(annotation)
            if parameter_name in descriptions:
                schema["description"] = descriptions[parameter_name]
            schema.update(overrides.get(parameter_name, {}))
            if parameter.default is inspect.Parameter.empty:
                required.append(parameter_name)
            else:
                schema["default"] = parameter.default
            properties[parameter_name] = schema
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            parameters["required"] = required
        return cls(
            name=name or handler.__name__,
            description=description,
            parameters=parameters,
            handler=handler,
        )

    @staticmethod
    def _annotation_schema(annotation: Any) -> dict[str, Any]:
        """把项目工具常用的 Python 类型映射到 JSON Schema 子集。"""
        origin = get_origin(annotation)
        arguments = get_args(annotation)
        if origin in {Union, types.UnionType}:
            mapped = [
                ToolDefinition._annotation_schema(item)["type"]
                for item in arguments
            ]
            return {"type": mapped}
        if origin is list:
            item_type = arguments[0] if arguments else Any
            return {
                "type": "array",
                "items": ToolDefinition._annotation_schema(item_type),
            }
        mapping = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            dict: "object",
            type(None): "null",
            Any: "object",
        }
        if annotation not in mapping:
            raise TypeError(f"unsupported tool parameter annotation: {annotation!r}")
        return {"type": mapping[annotation]}

    @property
    def schema(self) -> dict[str, Any]:
        """返回 OpenAI-compatible function tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """注册、描述并安全分发本地工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """注册一个工具定义。"""
        self._tools[definition.name] = definition

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """按注册顺序返回全部模型 schema。"""
        return [definition.schema for definition in self._tools.values()]

    @property
    def names(self) -> tuple[str, ...]:
        """返回已经注册的工具名称。"""
        return tuple(self._tools)

    def execute(self, tool_name: str, arguments: str) -> dict[str, Any]:
        """解析 JSON 参数、调用工具并统一结果结构。"""
        definition = self._tools.get(tool_name)
        if definition is None:
            return self._failure(
                "UnknownTool", f"Tool '{tool_name}' is not registered."
            )

        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return self._failure(
                "InvalidArguments", "Tool arguments must be a valid JSON object."
            )
        if not isinstance(parsed, dict):
            return self._failure(
                "InvalidArguments", "Tool arguments must be a JSON object."
            )

        validation_error = self._validate_arguments(definition.parameters, parsed)
        if validation_error is not None:
            return self._failure("InvalidArguments", validation_error)

        try:
            data = definition.handler(**parsed)
        except ToolError as error:
            return self._failure(error.error, error.message)
        except (OSError, ValueError):
            return self._failure("ToolExecutionError", "The local tool failed.")
        return {"success": True, "data": data}

    @staticmethod
    def _validate_arguments(
        schema: dict[str, Any], arguments: dict[str, Any]
    ) -> str | None:
        """验证当前六个工具所需的 JSON Schema 子集。"""
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in arguments:
                return f"Missing required argument: {name}."

        unknown = set(arguments) - set(properties)
        if unknown:
            return f"Unknown argument: {sorted(unknown)[0]}."

        for name, value in arguments.items():
            property_schema = properties[name]
            expected = property_schema.get("type")
            allowed_types = expected if isinstance(expected, list) else [expected]
            if not ToolRegistry._matches_type(value, allowed_types):
                return f"Argument '{name}' has the wrong type."
            if isinstance(value, list):
                minimum_items = property_schema.get("minItems")
                if minimum_items is not None and len(value) < minimum_items:
                    return f"Argument '{name}' contains too few items."
                item_type = property_schema.get("items", {}).get("type")
                if item_type is not None and any(
                    not ToolRegistry._matches_type(item, [item_type]) for item in value
                ):
                    return f"Argument '{name}' contains an item of the wrong type."
            if isinstance(value, int) and not isinstance(value, bool):
                if "minimum" in property_schema and value < property_schema["minimum"]:
                    return f"Argument '{name}' is below its minimum."
                if "maximum" in property_schema and value > property_schema["maximum"]:
                    return f"Argument '{name}' exceeds its maximum."
        return None

    @staticmethod
    def _matches_type(value: Any, allowed_types: list[str]) -> bool:
        """判断 Python 值是否匹配声明的 JSON 类型。"""
        checks = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int)
            and not isinstance(item, bool),
            "array": lambda item: isinstance(item, list),
            "number": lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "object": lambda item: isinstance(item, dict),
            "null": lambda item: item is None,
        }
        return any(checks[item](value) for item in allowed_types if item in checks)

    @staticmethod
    def _failure(error: str, message: str) -> dict[str, Any]:
        """构造统一失败结果。"""
        return {"success": False, "error": error, "message": message}


def create_tool_registry(
    workspace_root: Path,
    should_cancel: Callable[[], bool] | None = None,
) -> ToolRegistry:
    """为指定工作区创建六个本地工具。"""
    filesystem = FilesystemTools(workspace_root)
    commands = CommandTools(workspace_root, should_cancel=should_cancel)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="list_directory",
            description=(
                "List the direct children of a directory inside the current workspace. "
                "The operation is non-recursive and read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to the workspace root.",
                        "default": ".",
                    }
                },
                "additionalProperties": False,
            },
            handler=filesystem.list_directory,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            description=(
                "Read bounded UTF-8 text from a file inside the current workspace. "
                "Line numbers are 1-based."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the workspace root.",
                    },
                    "start_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional first line, using 1-based numbering.",
                    },
                    "end_line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional final line, using 1-based numbering.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=filesystem.read_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="search_text",
            description=(
                "Search for a case-sensitive literal text string in UTF-8 files under "
                "a workspace path. Returns paths, line numbers, and matching lines."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal text to find.",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory relative to the workspace root.",
                        "default": ".",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum number of matches to return.",
                        "default": 50,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=filesystem.search_text,
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            description=(
                "Create a new UTF-8 text file inside the current workspace. "
                "This tool refuses to overwrite an existing file and does not "
                "create missing parent directories."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "New file path relative to the workspace root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete UTF-8 text content for the new file.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=filesystem.write_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="edit_file",
            description=(
                "Modify an existing UTF-8 text file inside the current workspace by "
                "replacing one exact, uniquely occurring text block. The tool refuses "
                "missing or ambiguous old_text and never replaces multiple matches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Existing file path relative to the workspace root.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact, uniquely occurring text to replace.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text; it may be empty.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            handler=filesystem.edit_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="run_command",
            description=(
                "Execute a non-interactive local development command inside a working "
                "directory under the current workspace. The command is executed without "
                "a shell. Use separate array elements for the executable and every "
                "argument; do not use shell operators such as &&, |, >, or <. Returns "
                "stdout, stderr, exit code, timeout status, and truncation status."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": (
                            "Executable followed by each argument as separate strings."
                        ),
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory relative to the workspace root."
                        ),
                        "default": ".",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": MIN_TIMEOUT_SECONDS,
                        "maximum": MAX_TIMEOUT_SECONDS,
                        "description": "Maximum execution time in seconds.",
                        "default": DEFAULT_TIMEOUT_SECONDS,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            handler=commands.run_command,
        )
    )
    return registry
