"""Stage 7 验证状态机、命令分类和 Completion Gate 测试。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from coding_agent.agent import Agent, VERIFICATION_REMINDER
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMResponse, ToolCall
from coding_agent.tools.registry import create_tool_registry
from coding_agent.verification import (
    VerificationTracker,
    is_verification_command,
)


def _mutation_call(path: str = "app.py", call_id: str = "edit") -> ToolCall:
    return ToolCall(
        call_id,
        "edit_file",
        json.dumps({"path": path, "old_text": "old", "new_text": "new"}),
    )


def _mutation_result(path: str = "app.py") -> dict:
    return {
        "success": True,
        "data": {"path": path, "modified": True, "replacements": 1},
    }


def _command_call(command: list[str], call_id: str = "run") -> ToolCall:
    return ToolCall(call_id, "run_command", json.dumps({"command": command}))


def _command_result(exit_code: int = 0, timed_out: bool = False) -> dict:
    return {
        "success": True,
        "data": {
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": "",
            "stderr": "",
            "cwd": ".",
        },
    }


class VerificationTrackerTests(unittest.TestCase):
    def test_no_mutation_is_not_required(self) -> None:
        tracker = VerificationTracker()

        self.assertEqual(tracker.verification_status, "not_required")
        self.assertFalse(tracker.completion_blocked)

    def test_python_mutation_becomes_unverified(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call(), _mutation_result())

        self.assertEqual(tracker.mutation_generation, 1)
        self.assertEqual(tracker.verification_status, "unverified")
        self.assertTrue(tracker.completion_blocked)

    def test_markdown_mutation_does_not_require_verification(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(
            _mutation_call("README.md"),
            _mutation_result("README.md"),
        )

        self.assertEqual(tracker.mutation_generation, 0)
        self.assertEqual(tracker.verification_status, "not_required")

    def test_pytest_success_verifies_latest_generation(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call(), _mutation_result())
        tracker.record_tool_result(
            _command_call(["python", "-m", "pytest"]),
            _command_result(),
        )

        self.assertEqual(tracker.verified_generation, 1)
        self.assertEqual(tracker.verification_status, "verified")

    def test_failed_verification_is_recorded(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call(), _mutation_result())
        tracker.record_tool_result(
            _command_call(["pytest"]),
            _command_result(exit_code=1),
        )

        self.assertEqual(tracker.verification_status, "failed")
        self.assertTrue(tracker.completion_blocked)

    def test_new_edit_after_success_requires_new_verification(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call(), _mutation_result())
        tracker.record_tool_result(
            _command_call(["python", "-m", "pytest"]),
            _command_result(),
        )
        tracker.record_tool_result(
            _mutation_call("other.py", "edit-2"),
            _mutation_result("other.py"),
        )

        self.assertEqual(tracker.mutation_generation, 2)
        self.assertEqual(tracker.verified_generation, 1)
        self.assertEqual(tracker.verification_status, "unverified")

    def test_plain_python_command_is_not_verification(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call(), _mutation_result())
        tracker.record_tool_result(
            _command_call(["python", "-c", "print('ok')"]),
            _command_result(),
        )

        self.assertEqual(tracker.verification_status, "unverified")
        self.assertFalse(is_verification_command(["python", "script.py"]))

    def test_compileall_is_syntax_verification(self) -> None:
        self.assertTrue(
            is_verification_command(["python.exe", "-m", "compileall", "src"])
        )

    def test_py_compile_must_cover_pending_mutation(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call("src/app.py"), _mutation_result("src/app.py"))
        tracker.record_tool_result(
            _command_call(["python", "-m", "py_compile", "src/other.py"]),
            _command_result(),
        )

        self.assertEqual(tracker.verification_status, "unverified")
        self.assertEqual(tracker.pending_mutation_paths, {"src/app.py"})

        tracker.record_tool_result(
            _command_call(["python", "-m", "py_compile", "app.py"], "run-2"),
            {
                **_command_result(),
                "data": {**_command_result()["data"], "cwd": "src"},
            },
        )

        self.assertEqual(tracker.verification_status, "verified")
        self.assertEqual(tracker.pending_mutation_paths, set())

    def test_compileall_directory_must_cover_all_pending_mutations(self) -> None:
        tracker = VerificationTracker()
        for index, path in enumerate(("src/app.py", "config/settings.json"), 1):
            tracker.record_tool_result(
                _mutation_call(path, f"edit-{index}"),
                _mutation_result(path),
            )

        tracker.record_tool_result(
            _command_call(["python", "-m", "compileall", "src"]),
            _command_result(),
        )
        self.assertEqual(tracker.verification_status, "unverified")

        tracker.record_tool_result(
            _command_call(["python", "-m", "compileall", "."], "run-2"),
            _command_result(),
        )
        self.assertEqual(tracker.verification_status, "verified")

    def test_project_test_covers_multiple_pending_paths(self) -> None:
        tracker = VerificationTracker()
        for index, path in enumerate(("src/app.py", "config/settings.json"), 1):
            tracker.record_tool_result(
                _mutation_call(path, f"edit-{index}"),
                _mutation_result(path),
            )

        tracker.record_tool_result(
            _command_call(["python", "-m", "pytest"]),
            _command_result(),
        )

        self.assertEqual(tracker.verification_status, "verified")

    def test_timed_out_verification_is_failed(self) -> None:
        tracker = VerificationTracker()
        tracker.record_tool_result(_mutation_call(), _mutation_result())
        tracker.record_tool_result(
            _command_call(["python", "-m", "unittest"]),
            _command_result(timed_out=True),
        )

        self.assertEqual(tracker.verification_status, "failed")
        self.assertTrue(tracker.completion_blocked)

    def test_common_non_python_verification_commands(self) -> None:
        commands = [
            ["npm", "test"],
            ["npm", "run", "test"],
            ["npm", "run", "build"],
            ["yarn", "test"],
            ["pnpm", "test"],
            ["go", "test", "./..."],
            ["cargo", "test"],
            ["cargo", "check"],
            ["mvn", "verify"],
            ["gradlew.bat", "test"],
        ]

        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(is_verification_command(command))


class CompletionGateTests(unittest.TestCase):
    def _agent(
        self,
        client: Mock,
        workspace: Path,
        max_steps: int,
    ) -> tuple[Agent, Conversation]:
        conversation = Conversation("System prompt")
        return (
            Agent(
                client,
                conversation,
                create_tool_registry(workspace),
                max_steps=max_steps,
            ),
            conversation,
        )

    @staticmethod
    def _edit_response(path: str = "app.py", call_id: str = "edit") -> LLMResponse:
        return LLMResponse(
            None,
            [
                ToolCall(
                    call_id,
                    "edit_file",
                    json.dumps(
                        {"path": path, "old_text": "old", "new_text": "new"}
                    ),
                )
            ],
        )

    @staticmethod
    def _run_response(command: list[str], call_id: str = "run") -> LLMResponse:
        return LLMResponse(
            None,
            [ToolCall(call_id, "run_command", json.dumps({"command": command}))],
        )

    def test_edit_then_final_answer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("old\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                self._edit_response(),
                LLMResponse("Done", []),
            ]
            agent, conversation = self._agent(client, workspace, max_steps=2)

            result = agent.run("Change app")

        self.assertEqual(result.stop_reason, "max_steps")
        self.assertEqual(result.verification_status, "unverified")
        self.assertEqual(conversation.messages[-1]["content"], VERIFICATION_REMINDER)

    def test_edit_pass_then_final_answer_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("old\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                self._edit_response(),
                self._run_response([sys.executable, "-m", "py_compile", "app.py"]),
                LLMResponse("Verified", []),
            ]
            agent, _ = self._agent(client, workspace, max_steps=3)

            result = agent.run("Change app")

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.verification_status, "verified")
        self.assertEqual(result.tool_calls, 2)

    def test_failed_verification_then_final_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("old\n", encoding="utf-8")
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
                                    "path": "app.py",
                                    "old_text": "old",
                                    "new_text": "def broken(:",
                                }
                            ),
                        )
                    ],
                ),
                self._run_response([sys.executable, "-m", "py_compile", "app.py"]),
                LLMResponse("Done", []),
            ]
            agent, _ = self._agent(client, workspace, max_steps=3)

            result = agent.run("Change app")

        self.assertEqual(result.stop_reason, "max_steps")
        self.assertEqual(result.verification_status, "failed")

    def test_edit_after_pass_requires_verification_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("old\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                self._edit_response(),
                self._run_response([sys.executable, "-m", "py_compile", "app.py"]),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "edit-2",
                            "edit_file",
                            json.dumps(
                                {
                                    "path": "app.py",
                                    "old_text": "new",
                                    "new_text": "newer",
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse("Done", []),
            ]
            agent, _ = self._agent(client, workspace, max_steps=4)

            result = agent.run("Change twice")

        self.assertEqual(result.stop_reason, "max_steps")
        self.assertEqual(result.verification_status, "unverified")

    def test_readme_only_edit_can_complete_without_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "README.md").write_text("old\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                self._edit_response("README.md"),
                LLMResponse("Documented", []),
            ]
            agent, _ = self._agent(client, workspace, max_steps=2)

            result = agent.run("Update docs")

        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(result.verification_status, "not_required")

    def test_reminder_limit_stops_with_verification_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("old\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                self._edit_response(),
                LLMResponse("Done once", []),
                LLMResponse("Done twice", []),
                LLMResponse("Done three times", []),
            ]
            agent, conversation = self._agent(client, workspace, max_steps=5)

            result = agent.run("Change app")

        reminders = [
            message
            for message in conversation.messages
            if message["role"] == "system" and message["content"] == VERIFICATION_REMINDER
        ]
        self.assertEqual(result.stop_reason, "verification_required")
        self.assertEqual(result.steps, 4)
        self.assertEqual(len(reminders), 2)

    def test_completion_gate_never_exceeds_max_steps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "app.py").write_text("old\n", encoding="utf-8")
            client = Mock()
            client.complete.side_effect = [
                self._edit_response(),
                LLMResponse("Done", []),
                LLMResponse("Must not run", []),
            ]
            agent, _ = self._agent(client, workspace, max_steps=2)

            result = agent.run("Change app")

        self.assertEqual(result.stop_reason, "max_steps")
        self.assertEqual(result.steps, 2)
        self.assertEqual(client.complete.call_count, 2)


if __name__ == "__main__":
    unittest.main()
