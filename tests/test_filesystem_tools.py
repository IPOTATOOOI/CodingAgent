"""工作区只读文件系统工具测试。"""

from pathlib import Path
import tempfile
import unittest

from coding_agent.tools.filesystem import (
    MAX_FILE_BYTES,
    FilesystemTools,
    ToolError,
)


class FilesystemToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        base_directory = Path(self.temporary_directory.name)
        self.workspace = base_directory / "workspace"
        self.outside_directory = base_directory / "outside"
        self.workspace.mkdir()
        self.outside_directory.mkdir()
        (self.workspace / "src").mkdir()
        (self.workspace / "tests").mkdir()
        (self.workspace / "README.md").write_text(
            "Mini Coding Agent\n第二行中文注释\nthird line\n", encoding="utf-8"
        )
        (self.workspace / "src" / "app.py").write_text(
            "class Settings:\n    pass\n", encoding="utf-8"
        )
        (self.workspace / "src" / "config.py").write_text(
            "class Settings:\n    model = None\n", encoding="utf-8"
        )
        self.tools = FilesystemTools(self.workspace)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_list_directory_returns_stable_direct_entries(self) -> None:
        result = self.tools.list_directory(".")

        self.assertEqual(
            result["entries"],
            [
                {"name": "src", "type": "directory"},
                {"name": "tests", "type": "directory"},
                {"name": "README.md", "type": "file"},
            ],
        )

    def test_list_directory_rejects_missing_path_file_and_traversal(self) -> None:
        cases = [
            ("missing", "FileNotFound"),
            ("README.md", "NotDirectory"),
            ("..", "PathOutsideWorkspace"),
        ]

        for path, expected_error in cases:
            with self.subTest(path=path), self.assertRaises(ToolError) as raised:
                self.tools.list_directory(path)
            self.assertEqual(raised.exception.error, expected_error)

    def test_read_file_returns_numbered_utf8_lines(self) -> None:
        result = self.tools.read_file("README.md", start_line=2, end_line=3)

        self.assertEqual(result["content"], "2 | 第二行中文注释\n3 | third line")
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertFalse(result["truncated"])

    def test_read_file_rejects_missing_directory_and_traversal(self) -> None:
        cases = [
            ("missing.txt", "FileNotFound"),
            ("src", "NotFile"),
            ("../../secret.txt", "PathOutsideWorkspace"),
        ]

        for path, expected_error in cases:
            with self.subTest(path=path), self.assertRaises(ToolError) as raised:
                self.tools.read_file(path)
            self.assertEqual(raised.exception.error, expected_error)

    def test_read_file_rejects_symlink_to_outside_workspace(self) -> None:
        outside_file = self.outside_directory / "secret.txt"
        outside_file.write_text("secret", encoding="utf-8")
        link = self.workspace / "external-link.txt"
        try:
            link.symlink_to(outside_file)
        except OSError as error:
            self.skipTest(f"当前系统不允许创建符号链接：{error}")

        with self.assertRaises(ToolError) as raised:
            self.tools.read_file("external-link.txt")

        self.assertEqual(raised.exception.error, "PathOutsideWorkspace")

    def test_read_file_rejects_large_and_binary_files(self) -> None:
        (self.workspace / "large.txt").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        (self.workspace / "binary.bin").write_bytes(b"\xff\xfe")

        with self.assertRaises(ToolError) as large_error:
            self.tools.read_file("large.txt")
        with self.assertRaises(ToolError) as binary_error:
            self.tools.read_file("binary.bin")

        self.assertEqual(large_error.exception.error, "FileTooLarge")
        self.assertEqual(binary_error.exception.error, "UnsupportedFileEncoding")

    def test_environment_secret_files_are_not_read_or_searched(self) -> None:
        (self.workspace / ".env").write_text(
            "LLM_API_KEY=secret-value", encoding="utf-8"
        )
        (self.workspace / ".env.example").write_text(
            "LLM_API_KEY=", encoding="utf-8"
        )

        with self.assertRaises(ToolError) as raised:
            self.tools.read_file(".env")
        search_result = self.tools.search_text("secret-value")
        example_result = self.tools.read_file(".env.example")

        self.assertEqual(raised.exception.error, "SensitiveFile")
        self.assertEqual(search_result["matches"], [])
        self.assertIn("LLM_API_KEY=", example_result["content"])

    def test_read_file_limits_number_of_lines(self) -> None:
        content = "\n".join(f"line {number}" for number in range(600))
        (self.workspace / "many-lines.txt").write_text(content, encoding="utf-8")

        result = self.tools.read_file("many-lines.txt")

        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["content"].splitlines()), 500)

    def test_search_text_returns_path_line_and_text(self) -> None:
        result = self.tools.search_text("class Settings", path="src")

        self.assertEqual(len(result["matches"]), 2)
        self.assertEqual(
            result["matches"][0],
            {"path": "src/app.py", "line": 1, "text": "class Settings:"},
        )

    def test_search_text_returns_empty_matches(self) -> None:
        result = self.tools.search_text("not present")

        self.assertEqual(result["matches"], [])
        self.assertFalse(result["truncated"])

    def test_search_text_respects_limit_and_ignored_directories(self) -> None:
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "ignored.txt").write_text(
            "needle", encoding="utf-8"
        )
        (self.workspace / "matches.txt").write_text(
            "needle\nneedle\nneedle\n", encoding="utf-8"
        )

        result = self.tools.search_text("needle", max_results=2)

        self.assertEqual(len(result["matches"]), 2)
        self.assertTrue(result["truncated"])
        self.assertTrue(
            all(not match["path"].startswith(".git/") for match in result["matches"])
        )

    def test_search_text_rejects_outside_workspace(self) -> None:
        with self.assertRaises(ToolError) as raised:
            self.tools.search_text("anything", path="..")

        self.assertEqual(raised.exception.error, "PathOutsideWorkspace")

    def test_write_file_creates_utf8_file(self) -> None:
        result = self.tools.write_file("hello.py", "print('你好')\n")

        self.assertEqual(
            result,
            {
                "path": "hello.py",
                "created": True,
                "bytes_written": len("print('你好')\n".encode("utf-8")),
            },
        )
        self.assertEqual(
            (self.workspace / "hello.py").read_text(encoding="utf-8"),
            "print('你好')\n",
        )

    def test_write_file_refuses_existing_file_without_changing_it(self) -> None:
        original = (self.workspace / "README.md").read_bytes()

        with self.assertRaises(ToolError) as raised:
            self.tools.write_file("README.md", "replacement")

        self.assertEqual(raised.exception.error, "FileAlreadyExists")
        self.assertEqual((self.workspace / "README.md").read_bytes(), original)

    def test_write_file_creates_missing_parents_but_rejects_traversal(self) -> None:
        result = self.tools.write_file("missing/nested/child.py", "content")

        self.assertTrue(result["created"])
        self.assertEqual(
            (self.workspace / "missing" / "nested" / "child.py").read_text(
                encoding="utf-8"
            ),
            "content",
        )
        with self.assertRaises(ToolError) as raised:
            self.tools.write_file("../outside.py", "content")
        self.assertEqual(raised.exception.error, "PathOutsideWorkspace")

    def test_edit_file_replaces_one_unique_utf8_block(self) -> None:
        target = self.workspace / "src" / "greeting.py"
        target.write_bytes("def greeting():\r\n    return '你好'\r\n".encode("utf-8"))

        result = self.tools.edit_file(
            "src/greeting.py",
            "return '你好'",
            "return '你好，世界'",
        )

        self.assertEqual(
            result,
            {
                "path": "src/greeting.py",
                "modified": True,
                "replacements": 1,
                "start_line": 2,
                "old_end_line": 2,
                "new_end_line": 2,
            },
        )
        self.assertEqual(
            target.read_bytes(),
            "def greeting():\r\n    return '你好，世界'\r\n".encode("utf-8"),
        )

    def test_edit_file_rejects_missing_and_ambiguous_text_without_changes(self) -> None:
        target = self.workspace / "values.txt"
        target.write_text("same\nsame\n", encoding="utf-8")

        for old_text, expected_error in [
            ("missing", "TextNotFound"),
            ("same", "AmbiguousEdit"),
        ]:
            with self.subTest(old_text=old_text), self.assertRaises(
                ToolError
            ) as raised:
                self.tools.edit_file("values.txt", old_text, "changed")
            self.assertEqual(raised.exception.error, expected_error)
            self.assertEqual(target.read_text(encoding="utf-8"), "same\nsame\n")

    def test_edit_file_rejects_empty_or_identical_text(self) -> None:
        for old_text, new_text, expected_error in [
            ("", "new", "InvalidArguments"),
            ("same", "same", "NoChange"),
        ]:
            with self.subTest(expected_error=expected_error), self.assertRaises(
                ToolError
            ) as raised:
                self.tools.edit_file("README.md", old_text, new_text)
            self.assertEqual(raised.exception.error, expected_error)

    def test_edit_file_rejects_invalid_targets_and_traversal(self) -> None:
        cases = [
            ("missing.txt", "FileNotFound"),
            ("src", "NotFile"),
            ("../outside.txt", "PathOutsideWorkspace"),
        ]

        for path, expected_error in cases:
            with self.subTest(path=path), self.assertRaises(ToolError) as raised:
                self.tools.edit_file(path, "old", "new")
            self.assertEqual(raised.exception.error, expected_error)

    def test_edit_file_rejects_large_and_binary_files(self) -> None:
        (self.workspace / "large-edit.txt").write_bytes(
            b"x" * (MAX_FILE_BYTES + 1)
        )
        (self.workspace / "binary-edit.bin").write_bytes(b"\xff\xfe")

        with self.assertRaises(ToolError) as large_error:
            self.tools.edit_file("large-edit.txt", "x", "y")
        with self.assertRaises(ToolError) as binary_error:
            self.tools.edit_file("binary-edit.bin", "old", "new")

        self.assertEqual(large_error.exception.error, "FileTooLarge")
        self.assertEqual(binary_error.exception.error, "UnsupportedFileEncoding")


if __name__ == "__main__":
    unittest.main()
