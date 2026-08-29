"""独立 Evaluation Runner 的确定性测试。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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
                    "run": 1,
                    "task_id": "one",
                    "success": True,
                    "agent_stop_reason": "max_steps",
                    "steps": 4,
                    "tool_calls": 3,
                    "duration_seconds": 2.0,
                },
                {
                    "run": 1,
                    "task_id": "two",
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

    def test_summary_reports_multi_run_stability(self) -> None:
        results = [
            {
                "run": 1,
                "task_id": "one",
                "success": True,
                "agent_stop_reason": "completed",
                "steps": 2,
                "tool_calls": 1,
                "duration_seconds": 2.0,
            },
            {
                "run": 1,
                "task_id": "two",
                "success": False,
                "agent_stop_reason": "max_steps",
                "steps": 12,
                "tool_calls": 10,
                "duration_seconds": 10.0,
            },
            {
                "run": 2,
                "task_id": "one",
                "success": True,
                "agent_stop_reason": "completed",
                "steps": 4,
                "tool_calls": 3,
                "duration_seconds": 4.0,
            },
            {
                "run": 2,
                "task_id": "two",
                "success": True,
                "agent_stop_reason": "completed",
                "steps": 6,
                "tool_calls": 5,
                "duration_seconds": 6.0,
            },
        ]

        summary = summarize(results)

        self.assertEqual(summary["runs"], 2)
        self.assertEqual(summary["suite_runs_passed"], 1)
        self.assertEqual(summary["suite_run_success_rate"], 0.5)
        self.assertEqual(summary["task_pass_at_least_once_rate"], 1.0)
        self.assertEqual(summary["agent_completion_rate"], 0.75)
        self.assertEqual(summary["p50_steps"], 4)
        self.assertEqual(summary["p95_steps"], 12)
        self.assertEqual(summary["per_task"]["two"]["pass_rate"], 0.5)

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

        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "checkpoint.json"
            report = run_evaluation(
                [task],
                client,
                max_steps=4,
                checkpoint_path=checkpoint,
                trace=False,
            )
            checkpoint_report = json.loads(checkpoint.read_text(encoding="utf-8"))

        self.assertTrue(report["tasks"][0]["success"])
        self.assertTrue(report["complete"])
        self.assertTrue(checkpoint_report["complete"])
        self.assertEqual(report["tasks"][0]["agent_stop_reason"], "completed")
        self.assertEqual(report["tasks"][0]["verification_status"], "verified")
        self.assertEqual(fixture.read_text(encoding="utf-8"), original)

    def test_verifier_snapshot_survives_but_integrity_escape_fails_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            eval_root = Path(directory)
            fixture = eval_root / "fixture"
            fixture.mkdir()
            (fixture / "README.md").write_text("demo\n", encoding="utf-8")
            verifier = eval_root / "verify.py"
            verifier.write_text("raise SystemExit(0)\n", encoding="utf-8")
            task = {
                "task_id": "integrity",
                "prompt": "Inspect the project.",
                "fixture": "fixture",
                "verifier": "verify.py",
            }
            malicious = (
                "from pathlib import Path; "
                f"Path({str(verifier)!r}).write_text('raise SystemExit(1)\\n')"
            )
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "escape",
                            "run_command",
                            json.dumps(
                                {"command": [sys.executable, "-c", malicious]}
                            ),
                        )
                    ],
                ),
                LLMResponse("Done", []),
            ]

            with patch("eval.runner.EVAL_ROOT", eval_root):
                report = run_evaluation([task], client, max_steps=3, trace=False)

        result = report["tasks"][0]
        self.assertEqual(result["verifier_exit_code"], 0)
        self.assertFalse(result["verifier_integrity"])
        self.assertFalse(result["success"])

    def test_runner_repeats_tasks_in_fresh_workspaces(self) -> None:
        task = load_tasks("single_bug_fix")[0]
        responses = []
        for run_number in range(1, 3):
            responses.extend(
                [
                    LLMResponse(
                        None,
                        [
                            ToolCall(
                                f"edit-{run_number}",
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
                                f"run-{run_number}",
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
                    LLMResponse("Fixed", []),
                ]
            )
        client = Mock()
        client.complete.side_effect = responses

        report = run_evaluation(
            [task],
            client,
            max_steps=4,
            runs=2,
            trace=False,
        )

        self.assertEqual([result["run"] for result in report["tasks"]], [1, 2])
        self.assertTrue(all(result["success"] for result in report["tasks"]))
        self.assertEqual(report["summary"]["suite_runs_passed"], 2)


if __name__ == "__main__":
    unittest.main()
