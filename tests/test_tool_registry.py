"""工具注册、schema、参数校验和分发测试。"""

from pathlib import Path
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

    def test_three_tools_are_registered_with_schemas(self) -> None:
        self.assertEqual(
            self.registry.names,
            ("list_directory", "read_file", "search_text"),
        )
        self.assertEqual(len(self.registry.schemas), 3)
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


if __name__ == "__main__":
    unittest.main()
