"""工具注册、schema、参数校验和分发测试。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from coding_agent.tools.registry import create_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        (self.workspace / "README.md").write_text("hello", encoding="utf-8")
        self.registry = create_tool_registry(self.workspace)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_six_tools_are_registered_with_schemas(self) -> None:
        self.assertEqual(
            self.registry.names,
            (
                "list_directory",
                "read_file",
                "search_text",
                "write_file",
                "edit_file",
                "run_command",
            ),
        )
        self.assertEqual(len(self.registry.schemas), 6)
        self.assertEqual(
            [schema["function"]["name"] for schema in self.registry.schemas],
            list(self.registry.names),
        )

    def test_execute_dispatches_to_registered_tool(self) -> None:
        result = self.registry.execute("read_file", '{"path":"README.md"}')

        self.assertTrue(result["success"])
        self.assertIn("hello", result["data"]["content"])

    def test_unknown_tool_returns_structured_error(self) -> None:
        result = self.registry.execute("delete_file", "{}")

        self.assertEqual(result["error"], "UnknownTool")
        self.assertFalse(result["success"])

    def test_invalid_json_returns_structured_error(self) -> None:
        result = self.registry.execute("read_file", "not json")

        self.assertEqual(result["error"], "InvalidArguments")

    def test_missing_required_argument_returns_structured_error(self) -> None:
        result = self.registry.execute("read_file", "{}")

        self.assertEqual(result["error"], "InvalidArguments")
        self.assertIn("path", result["message"])

    def test_wrong_argument_type_returns_structured_error(self) -> None:
        result = self.registry.execute("search_text", '{"query":3}')

        self.assertEqual(result["error"], "InvalidArguments")

    def test_mutation_error_is_returned_as_structured_result(self) -> None:
        result = self.registry.execute(
            "write_file",
            '{"path":"README.md","content":"replacement"}',
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "FileAlreadyExists")

    def test_run_command_schema_contains_bounded_arguments(self) -> None:
        schema = next(
            item["function"]
            for item in self.registry.schemas
            if item["function"]["name"] == "run_command"
        )

        properties = schema["parameters"]["properties"]
        self.assertEqual(properties["command"]["type"], "array")
        self.assertIn("cwd", properties)
        self.assertEqual(properties["timeout_seconds"]["maximum"], 120)

    def test_run_command_rejects_string_instead_of_array(self) -> None:
        result = self.registry.execute(
            "run_command", '{"command":"python -m unittest"}'
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "InvalidArguments")

    def test_nonzero_command_exit_is_a_successful_tool_result(self) -> None:
        result = self.registry.execute(
            "run_command",
            json.dumps(
                {
                    "command": [sys.executable, "-c", "raise SystemExit(3)"],
                    "timeout_seconds": 30,
                }
            ),
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["exit_code"], 3)


if __name__ == "__main__":
    unittest.main()
