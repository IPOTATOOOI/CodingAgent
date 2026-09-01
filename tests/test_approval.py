"""Safety Mode 与 ToolRegistry 执行前授权测试。"""

from pathlib import Path
import tempfile
import unittest

from coding_agent.approval import (
    ApprovalAction,
    ApprovalDecision,
    SafetyMode,
    approval_action,
)
from coding_agent.tools.registry import create_tool_registry


class ApprovalPolicyTests(unittest.TestCase):
    def test_four_modes_have_distinct_tool_policies(self) -> None:
        self.assertEqual(
            approval_action(SafetyMode.ASK, "read_file"), ApprovalAction.ALLOW
        )
        self.assertEqual(
            approval_action(SafetyMode.ASK, "edit_file"), ApprovalAction.ASK
        )
        self.assertEqual(
            approval_action(SafetyMode.AUTO_EDIT, "write_file"),
            ApprovalAction.ALLOW,
        )
        self.assertEqual(
            approval_action(SafetyMode.AUTO_EDIT, "run_command"),
            ApprovalAction.ASK,
        )
        self.assertEqual(
            approval_action(SafetyMode.AUTO, "run_command"), ApprovalAction.ALLOW
        )
        self.assertEqual(
            approval_action(SafetyMode.READ_ONLY, "create_directory"),
            ApprovalAction.DENY,
        )

    def test_registry_rejection_happens_before_file_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            registry = create_tool_registry(
                workspace,
                approval_callback=lambda name, arguments: ApprovalDecision.reject(),
            )

            result = registry.execute(
                "write_file", '{"path":"blocked.txt","content":"secret"}'
            )

            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "ApprovalRejected")
            self.assertFalse((workspace / "blocked.txt").exists())

    def test_read_tools_can_be_auto_approved_by_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "README.md").write_text("hello", encoding="utf-8")
            registry = create_tool_registry(
                workspace,
                approval_callback=lambda name, arguments: ApprovalDecision.allow(),
            )

            result = registry.execute("read_file", '{"path":"README.md"}')

            self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
