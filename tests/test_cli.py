"""命令行参数、Reliability Trace 和 Agent 委托测试。"""

import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from coding_agent.cli import (
    SYSTEM_PROMPT,
    _format_tool_result_trace,
    _format_tool_trace,
    _print_llm_retry,
    _safe_print,
    main,
    run_cli,
)
from coding_agent.llm import LLMError, LLMResponse, ToolCall
from coding_agent.agent import AgentResult


class CliTests(unittest.TestCase):
    def test_main_is_importable(self) -> None:
        self.assertTrue(callable(main))

    def test_verification_required_result_is_displayed(self) -> None:
        from coding_agent.cli import _print_agent_result

        output = io.StringIO()
        with patch("sys.stdout", output):
            _print_agent_result(
                AgentResult(
                    "Verification is still required.",
                    "verification_required",
                    4,
                    verification_status="unverified",
                )
            )

        self.assertIn("latest code changes still require verification", output.getvalue())

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

    def test_cli_delegates_repeated_tool_decisions_to_agent(self) -> None:
        client = Mock()
        client.complete.side_effect = [
            LLMResponse(
                None,
                [ToolCall("call-1", "list_directory", '{"path":"."}')],
            ),
            LLMResponse(
                None,
                [ToolCall("call-2", "read_file", '{"path":"README.md"}')],
            ),
            LLMResponse("README inspected.", []),
        ]
        output = io.StringIO()

        with patch("builtins.input", side_effect=["Inspect README", "/exit"]), patch(
            "sys.stdout", output
        ):
            run_cli(client=client, workspace_root=Path.cwd())

        displayed = output.getvalue()
        self.assertEqual(client.complete.call_count, 3)
        self.assertIn("[step 1] [tool] list_directory", displayed)
        self.assertIn("[step 2] [tool] read_file", displayed)
        self.assertIn("README inspected.", displayed)

    def test_mutation_trace_only_displays_path(self) -> None:
        edit_trace = _format_tool_trace(
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

        self.assertEqual(edit_trace, "[tool] edit_file(path='app.py')")
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

    def test_read_and_error_results_have_bounded_summaries(self) -> None:
        read_summary = _format_tool_result_trace(
            ToolCall("call-1", "read_file", '{"path":"a.py"}'),
            {
                "success": True,
                "data": {"start_line": 1, "end_line": 10, "total_lines": 20},
            },
        )
        error_summary = _format_tool_result_trace(
            ToolCall("call-2", "read_file", '{"path":"missing.py"}'),
            {"success": False, "error": "FileNotFound", "message": "details"},
        )

        self.assertEqual(read_summary, "[result] lines=1-10, total=20")
        self.assertEqual(error_summary, "[result] error=FileNotFound")

    def test_reliability_results_have_explicit_trace(self) -> None:
        call = ToolCall("call", "run_command", '{}')

        self.assertEqual(
            _format_tool_result_trace(
                call,
                {"success": False, "error": "CommandBlocked"},
            ),
            "[result] blocked by runtime policy",
        )
        self.assertEqual(
            _format_tool_result_trace(
                call,
                {"success": False, "error": "RepeatedAction"},
            ),
            "[warning] repeated action detected",
        )

    def test_llm_retry_trace_does_not_include_error_details(self) -> None:
        output = io.StringIO()

        with patch("sys.stdout", output):
            _print_llm_retry(1, 2)

        self.assertEqual(output.getvalue(), "[llm] transient error, retry 1/2\n")

    def test_system_prompt_describes_dynamic_autonomous_loop(self) -> None:
        lowered = SYSTEM_PROMPT.lower()

        self.assertIn("autonomous programming assistant", lowered)
        self.assertIn("work iteratively", lowered)
        self.assertIn("choose actions dynamically", lowered)
        self.assertIn("never claim tests passed", lowered)
        self.assertIn("verify the latest changes", lowered)
        self.assertIn("do not install packages only for verification", lowered)
        self.assertIn("tool fails", lowered)
        self.assertIn("repeatedaction", lowered)
        self.assertIn("commandblocked", lowered)
        self.assertIn("unrelated cleanup", lowered)

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

    def test_max_steps_result_is_displayed_without_traceback(self) -> None:
        client = Mock()
        client.complete.return_value = LLMResponse(
            None,
            [ToolCall("call-1", "list_directory", '{"path":"."}')],
        )
        output = io.StringIO()

        with patch("builtins.input", side_effect=["Keep going", "/exit"]), patch(
            "sys.stdout", output
        ):
            run_cli(client=client, workspace_root=Path.cwd(), max_steps=2)

        displayed = output.getvalue()
        self.assertEqual(client.complete.call_count, 2)
        self.assertIn("maximum step limit reached", displayed)
        self.assertNotIn("Traceback", displayed)

    def test_main_forwards_valid_max_steps(self) -> None:
        with patch("coding_agent.cli.run_cli") as mocked_run_cli:
            main(["--workspace", ".", "--max-steps", "7"])

        self.assertEqual(mocked_run_cli.call_args.kwargs["max_steps"], 7)

    def test_main_rejects_max_steps_outside_allowed_range(self) -> None:
        for value in ("0", "51"):
            with self.subTest(max_steps=value), patch(
                "sys.stderr", io.StringIO()
            ), self.assertRaises(SystemExit):
                main(["--max-steps", value])

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
