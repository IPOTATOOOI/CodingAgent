"""第四阶段命令行编排、执行反馈与工具解析测试。"""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from coding_agent.cli import (
    SYSTEM_PROMPT,
    ToolResolutionLimitError,
    _format_tool_trace,
    _format_tool_result_trace,
    _safe_print,
    main,
    resolve_user_turn,
    run_cli,
)
from coding_agent.conversation import Conversation
from coding_agent.llm import LLMError, LLMResponse, ToolCall
from coding_agent.tools.registry import create_tool_registry


class CliTests(unittest.TestCase):
    def test_main_is_importable(self) -> None:
        self.assertTrue(callable(main))

    def test_safe_print_replaces_characters_unsupported_by_console(self) -> None:
        class AsciiStream(io.StringIO):
            @property
            def encoding(self) -> str:
                return "ascii"

            def write(self, text: str) -> int:
                text.encode(self.encoding)
                return super().write(text)

        output = AsciiStream()
        with patch("sys.stdout", output):
            _safe_print("result ✅")

        self.assertEqual(output.getvalue(), "result ?\n")

    def test_multi_turn_text_conversation_keeps_history(self) -> None:
        client = Mock()
        client.complete.side_effect = [
            LLMResponse("Mock answer 1", []),
            LLMResponse("Mock answer 2", []),
        ]
        output = io.StringIO()

        with patch(
            "builtins.input",
            side_effect=["What is recursion?", "Give me an example.", "/exit"],
        ), patch("sys.stdout", output):
            run_cli(client=client, workspace_root=Path.cwd())

        second_messages = client.complete.call_args_list[1].args[0]
        self.assertEqual(
            [message["role"] for message in second_messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertIn("Mock answer 2", output.getvalue())

    def test_complete_tool_resolution_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "README.md").write_text(
                "Mini Coding Agent", encoding="utf-8"
            )
            registry = create_tool_registry(workspace)
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"README.md"}')],
                ),
                LLMResponse("The README describes Mini Coding Agent.", []),
            ]
            conversation = Conversation("System prompt")
            conversation.add_user_message("Summarize README")

            with patch("sys.stdout", io.StringIO()):
                result = resolve_user_turn(client, conversation, registry)

        self.assertEqual(result, "The README describes Mini Coding Agent.")
        self.assertEqual(client.complete.call_count, 2)
        second_messages = client.complete.call_args_list[1].args[0]
        self.assertEqual(
            [message["role"] for message in second_messages],
            ["system", "user", "assistant", "tool"],
        )
        tool_result = json.loads(second_messages[-1]["content"])
        self.assertTrue(tool_result["success"])
        self.assertIn("Mini Coding Agent", tool_result["data"]["content"])

    def test_multiple_tool_calls_are_all_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "one.txt").write_text("one", encoding="utf-8")
            (workspace / "two.txt").write_text("two", encoding="utf-8")
            registry = create_tool_registry(workspace)
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [
                        ToolCall("call-1", "read_file", '{"path":"one.txt"}'),
                        ToolCall("call-2", "read_file", '{"path":"two.txt"}'),
                    ],
                ),
                LLMResponse("Both files were read.", []),
            ]
            conversation = Conversation("System prompt")
            conversation.add_user_message("Read both")

            with patch("sys.stdout", io.StringIO()):
                resolve_user_turn(client, conversation, registry)

        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user", "assistant", "tool", "tool", "assistant"],
        )

    def test_two_tool_rounds_can_inspect_then_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "calculator.py"
            target.write_text(
                "def divide(a, b):\n    return a / b\n", encoding="utf-8"
            )
            registry = create_tool_registry(workspace)
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"calculator.py"}')],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-2",
                            "edit_file",
                            json.dumps(
                                {
                                    "path": "calculator.py",
                                    "old_text": "    return a / b",
                                    "new_text": (
                                        "    if b == 0:\n"
                                        "        raise ValueError('division by zero')\n"
                                        "    return a / b"
                                    ),
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse(
                    "I added division-by-zero handling. I did not run tests.", []
                ),
            ]
            conversation = Conversation("System prompt")
            conversation.add_user_message("Handle division by zero")

            with patch("sys.stdout", io.StringIO()):
                result = resolve_user_turn(client, conversation, registry)

            self.assertIn("if b == 0", target.read_text(encoding="utf-8"))

        self.assertEqual(result, "I added division-by-zero handling. I did not run tests.")
        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(
            [message["role"] for message in conversation.messages],
            ["system", "user", "assistant", "tool", "assistant", "tool", "assistant"],
        )

    def test_fourth_tool_response_is_rejected_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "README.md").write_text("text", encoding="utf-8")
            registry = create_tool_registry(workspace)
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"README.md"}')],
                ),
                LLMResponse(
                    None,
                    [ToolCall("call-2", "search_text", '{"query":"text"}')],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-3", "read_file", '{"path":"README.md"}'
                        )
                    ],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-4",
                            "write_file",
                            '{"path":"forbidden.txt","content":"no"}',
                        )
                    ],
                ),
            ]
            conversation = Conversation("System prompt")
            conversation.add_user_message("Inspect")

            with patch("sys.stdout", io.StringIO()), self.assertRaises(
                ToolResolutionLimitError
            ):
                resolve_user_turn(client, conversation, registry)

            self.assertFalse((workspace / "forbidden.txt").exists())

        self.assertEqual(client.complete.call_count, 4)
        self.assertEqual(conversation.messages[-1]["role"], "assistant")

    def test_three_tool_rounds_can_inspect_edit_and_execute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / "calculator.py"
            target.write_text(
                "def add(a, b):\n    return a - b\n", encoding="utf-8"
            )
            registry = create_tool_registry(workspace)
            client = Mock()
            client.complete.side_effect = [
                LLMResponse(
                    None,
                    [ToolCall("call-1", "read_file", '{"path":"calculator.py"}')],
                ),
                LLMResponse(
                    None,
                    [
                        ToolCall(
                            "call-2",
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
                            "call-3",
                            "run_command",
                            json.dumps(
                                {
                                    "command": [
                                        sys.executable,
                                        "-c",
                                        (
                                            "from calculator import add; "
                                            "raise SystemExit(0 if add(2, 3) == 5 else 1)"
                                        ),
                                    ],
                                    "cwd": ".",
                                    "timeout_seconds": 30,
                                }
                            ),
                        )
                    ],
                ),
                LLMResponse("I fixed add() and execution succeeded.", []),
            ]
            conversation = Conversation("System prompt")
            conversation.add_user_message("Fix add and verify it")

            with patch("sys.stdout", io.StringIO()):
                result = resolve_user_turn(client, conversation, registry)

        self.assertEqual(result, "I fixed add() and execution succeeded.")
        self.assertEqual(client.complete.call_count, 4)
        self.assertEqual(
            [message["role"] for message in conversation.messages],
            [
                "system",
                "user",
                "assistant",
                "tool",
                "assistant",
                "tool",
                "assistant",
                "tool",
                "assistant",
            ],
        )
        execution_result = json.loads(conversation.messages[-2]["content"])
        self.assertTrue(execution_result["success"])
        self.assertEqual(execution_result["data"]["exit_code"], 0)

    def test_mutation_trace_only_displays_path(self) -> None:
        trace = _format_tool_trace(
            ToolCall(
                "call-1",
                "edit_file",
                '{"path":"app.py","old_text":"secret-old","new_text":"secret-new"}',
            )
        )
        write_trace = _format_tool_trace(
            ToolCall(
                "call-2",
                "write_file",
                '{"path":"new.py","content":"secret-content"}',
            )
        )

        self.assertEqual(trace, "[tool] edit_file(path='app.py')")
        self.assertEqual(write_trace, "[tool] write_file(path='new.py')")

    def test_command_trace_and_result_do_not_print_full_output(self) -> None:
        tool_call = ToolCall(
            "call-1",
            "run_command",
            '{"command":["python","-m","pytest"],"cwd":"."}',
        )
        trace = _format_tool_trace(tool_call)
        result_trace = _format_tool_result_trace(
            tool_call,
            {
                "success": True,
                "data": {
                    "exit_code": 1,
                    "timed_out": False,
                    "stdout": "failure details",
                    "stderr": "",
                },
            },
        )

        self.assertIn("command=['python', '-m', 'pytest']", trace)
        self.assertEqual(
            result_trace,
            "[result] exit_code=1, stdout=15 chars, stderr=0 chars",
        )
        self.assertNotIn("failure details", result_trace)

    def test_system_prompt_requires_execution_evidence(self) -> None:
        lowered = SYSTEM_PROMPT.lower()

        self.assertNotIn("read-only access", lowered)
        self.assertIn("create and edit utf-8 text files", lowered)
        self.assertIn("run_command", lowered)
        self.assertIn("never claim tests passed", lowered)
        self.assertIn("does not imply that the code is correct", lowered)

    def test_empty_input_does_not_call_llm(self) -> None:
        client = Mock()
        with patch("builtins.input", side_effect=["   ", "/exit"]), patch(
            "sys.stdout", io.StringIO()
        ):
            run_cli(client=client, workspace_root=Path.cwd())
        client.complete.assert_not_called()

    def test_failed_request_does_not_add_assistant_message(self) -> None:
        client = Mock()
        client.complete.side_effect = [
            LLMError("network error."),
            LLMResponse("Recovered", []),
        ]
        with patch(
            "builtins.input", side_effect=["Hello", "Try again", "/exit"]
        ), patch("sys.stdout", io.StringIO()):
            run_cli(client=client, workspace_root=Path.cwd())

        second_messages = client.complete.call_args_list[1].args[0]
        self.assertEqual(
            [message["role"] for message in second_messages],
            ["system", "user", "user"],
        )

    def test_missing_configuration_is_reported(self) -> None:
        output = io.StringIO()
        with patch("coding_agent.config.load_dotenv"), patch.dict(
            "os.environ", {}, clear=True
        ), patch("sys.stdout", output):
            run_cli(workspace_root=Path.cwd())
        self.assertIn("Configuration error", output.getvalue())

    def test_eof_and_keyboard_interrupt_exit_cleanly(self) -> None:
        for interruption in (EOFError, KeyboardInterrupt):
            with self.subTest(interruption=interruption), patch(
                "builtins.input", side_effect=interruption
            ), patch("sys.stdout", io.StringIO()) as output:
                run_cli(client=Mock(), workspace_root=Path.cwd())
            self.assertIn("Exiting Mini Coding Agent.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
