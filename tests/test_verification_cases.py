"""可复用真实 Verification Cases 工具的非网络测试。"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from coding_agent.llm import LLMResponse, ToolCall
from eval.verification_cases import build_cases, run_cases


class VerificationCasesTests(unittest.TestCase):
    def test_builds_four_named_cases(self) -> None:
        cases = build_cases("python")

        self.assertEqual([case["case"] for case in cases], ["A", "B", "C", "D"])

    def test_script_entrypoint_help_works(self) -> None:
        completed = subprocess.run(
            [sys.executable, "eval/verification_cases.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertIn("--case", completed.stdout)

    def test_case_report_is_checkpointed(self) -> None:
        case = build_cases(sys.executable)[3]
        client = Mock()
        client.complete.side_effect = [
            LLMResponse(
                None,
                [
                    ToolCall(
                        "edit",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "README.md",
                                "old_text": "# Demo\n",
                                "new_text": (
                                    "# Demo\n\nStage 7 verification demo\n"
                                ),
                            }
                        ),
                    )
                ],
            ),
            LLMResponse("Documented", []),
        ]

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "cases.json"
            report = run_cases(
                client,
                [case],
                max_steps=3,
                checkpoint_path=checkpoint,
                trace=False,
            )
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertTrue(report["complete"])
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertTrue(saved["complete"])
        self.assertEqual(saved["cases"][0]["verification_status"], "not_required")


if __name__ == "__main__":
    unittest.main()
