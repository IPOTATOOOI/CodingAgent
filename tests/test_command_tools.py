"""本地命令执行、限制和凭据隔离测试。"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from coding_agent.tools.command import (
    MAX_STDERR_CHARS,
    MAX_STDOUT_CHARS,
    CommandTools,
)
from coding_agent.tools.filesystem import ToolError


class CommandToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        (self.workspace / "sub").mkdir()
        (self.workspace / "file.txt").write_text("text", encoding="utf-8")
        self.tools = CommandTools(self.workspace)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_run_command_captures_stdout(self) -> None:
        result = self.tools.run_command(
            [sys.executable, "-c", "print('hello')"]
        )

        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello", result["stdout"])
        self.assertEqual(result["stderr"], "")
        self.assertFalse(result["timed_out"])

    def test_run_command_keeps_stderr_separate(self) -> None:
        result = self.tools.run_command(
            [
                sys.executable,
                "-c",
                "import sys; print('error', file=sys.stderr)",
            ]
        )

        self.assertEqual(result["stdout"], "")
        self.assertIn("error", result["stderr"])

    def test_nonzero_exit_code_is_a_normal_command_result(self) -> None:
        result = self.tools.run_command(
            [sys.executable, "-c", "raise SystemExit(3)"]
        )

        self.assertEqual(result["exit_code"], 3)
        self.assertFalse(result["timed_out"])

    def test_run_command_uses_workspace_scoped_cwd(self) -> None:
        result = self.tools.run_command(
            [sys.executable, "-c", "import os; print(os.getcwd())"],
            cwd="sub",
        )

        self.assertEqual(result["cwd"], "sub")
        self.assertEqual(
            Path(result["stdout"].strip()).resolve(),
            (self.workspace / "sub").resolve(),
        )

    def test_run_command_rejects_invalid_working_directories(self) -> None:
        cases = [
            ("missing", "WorkingDirectoryNotFound"),
            ("file.txt", "NotDirectory"),
            ("..", "PathOutsideWorkspace"),
        ]

        for cwd, expected_error in cases:
            with self.subTest(cwd=cwd), self.assertRaises(ToolError) as raised:
                self.tools.run_command([sys.executable, "-c", "pass"], cwd=cwd)
            self.assertEqual(raised.exception.error, expected_error)

    def test_run_command_rejects_invalid_arguments(self) -> None:
        cases = [
            ([], 30),
            ([""], 30),
            ([sys.executable], 0),
            ([sys.executable], 121),
        ]

        for command, timeout in cases:
            with self.subTest(command=command, timeout=timeout), self.assertRaises(
                ToolError
            ) as raised:
                self.tools.run_command(command, timeout_seconds=timeout)
            self.assertEqual(raised.exception.error, "InvalidArguments")

    def test_command_not_found_returns_controlled_error(self) -> None:
        with self.assertRaises(ToolError) as raised:
            self.tools.run_command(["definitely_not_a_real_command_xyz"])

        self.assertEqual(raised.exception.error, "CommandNotFound")

    def test_timeout_returns_observation(self) -> None:
        result = self.tools.run_command(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_seconds=1,
        )

        self.assertIsNone(result["exit_code"])
        self.assertTrue(result["timed_out"])

    def test_stdout_and_stderr_are_truncated_independently(self) -> None:
        result = self.tools.run_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    f"print('x' * {MAX_STDOUT_CHARS + 10}); "
                    f"print('y' * {MAX_STDERR_CHARS + 10}, file=sys.stderr)"
                ),
            ]
        )

        self.assertEqual(len(result["stdout"]), MAX_STDOUT_CHARS)
        self.assertEqual(len(result["stderr"]), MAX_STDERR_CHARS)
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])

    def test_agent_api_key_is_not_inherited(self) -> None:
        with patch.dict(os.environ, {"LLM_API_KEY": "secret_test_value"}):
            result = self.tools.run_command(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.getenv('LLM_API_KEY'))",
                ]
            )

        self.assertEqual(result["stdout"].strip(), "None")


if __name__ == "__main__":
    unittest.main()
