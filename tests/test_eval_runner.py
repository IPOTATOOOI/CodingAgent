"""独立 Evaluation Runner 的确定性测试。"""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

from coding_agent.llm import LLMResponse, ToolCall
from eval.runner import load_tasks, run_evaluation, summarize


class EvaluationRunnerTests(unittest.TestCase):
    def test_loads_six_tasks_and_filters_by_id(self) -> None:
        tasks = load_tasks()

        self.assertEqual(len(tasks), 6)
        self.assertEqual(load_tasks("single_bug_fix")[0]["task_id"], "single_bug_fix")
        with self.assertRaises(ValueError):
            load_tasks("missing-task")

    def test_summary_uses_independent_success_field(self) -> None:
        summary = summarize(
            [
                {
                    "success": True,
                    "agent_stop_reason": "max_steps",
                    "steps": 4,
                    "tool_calls": 3,
                    "duration_seconds": 2.0,
                },
                {
                    "success": False,
                    "agent_stop_reason": "completed",
                    "steps": 2,
                    "tool_calls": 1,
                    "duration_seconds": 4.0,
                },
            ]
        )

        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["verified_success_rate"], 0.5)
        self.assertEqual(summary["average_steps"], 3.0)
        self.assertEqual(summary["average_tool_calls"], 2.0)
        self.assertEqual(summary["average_duration_seconds"], 3.0)
        self.assertEqual(
            summary["stop_reason_distribution"],
            {"completed": 1, "max_steps": 1},
        )

    def test_runner_uses_fresh_copy_and_external_verifier(self) -> None:
        task = load_tasks("single_bug_fix")[0]
        fixture = Path("eval/tasks/task_01/workspace/calculator.py")
        original = fixture.read_text(encoding="utf-8")
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
                                "path": "calculator.py",
                                "old_text": "return a - b",
                                "new_text": "return a + b",
                            }
                        ),
                    )
                ],
            ),
            LLMResponse(
                None,
                [
                    ToolCall(
                        "run",
                        "run_command",
                        json.dumps(
                            {
                                "command": [
                                    sys.executable,
                                    "test_calculator.py",
                                ]
                            }
                        ),
                    )
                ],
            ),
            LLMResponse("Fixed and verified", []),
        ]

        report = run_evaluation([task], client, max_steps=4)

        self.assertTrue(report["tasks"][0]["success"])
        self.assertEqual(report["tasks"][0]["agent_stop_reason"], "completed")
        self.assertEqual(report["tasks"][0]["verification_status"], "verified")
        self.assertEqual(fixture.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
