"""第一阶段命令行界面测试。"""

import io
import unittest
from unittest.mock import Mock, patch

from coding_agent.cli import main, run_cli
from coding_agent.llm import LLMError


class CliTests(unittest.TestCase):
    def test_main_is_importable(self) -> None:
        self.assertTrue(callable(main))

    def test_multi_turn_conversation_passes_complete_history(self) -> None:
        client = Mock()
        client.complete.side_effect = ["Mock answer 1", "Mock answer 2"]
        output = io.StringIO()

        with patch(
            "builtins.input",
            side_effect=["What is recursion?", "Give me an example.", "/exit"],
        ), patch("sys.stdout", output):
            run_cli(client=client)

        first_messages = client.complete.call_args_list[0].args[0]
        second_messages = client.complete.call_args_list[1].args[0]
        self.assertEqual(
            [message["role"] for message in first_messages], ["system", "user"]
        )
        self.assertEqual(
            [message["role"] for message in second_messages],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(second_messages[-2]["content"], "Mock answer 1")
        self.assertEqual(second_messages[-1]["content"], "Give me an example.")
        self.assertIn("Mini Coding Agent", output.getvalue())
        self.assertIn("Mock answer 2", output.getvalue())
        self.assertIn("Exiting Mini Coding Agent.", output.getvalue())

    def test_empty_input_does_not_call_llm(self) -> None:
        client = Mock()

        with patch("builtins.input", side_effect=["   ", "/exit"]), patch(
            "sys.stdout", io.StringIO()
        ):
            run_cli(client=client)

        client.complete.assert_not_called()

    def test_failed_request_does_not_add_assistant_message(self) -> None:
        client = Mock()
        client.complete.side_effect = [LLMError("network error."), "Recovered"]

        with patch(
            "builtins.input", side_effect=["Hello", "Try again", "/exit"]
        ), patch("sys.stdout", io.StringIO()):
            run_cli(client=client)

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
            run_cli()

        self.assertIn(
            "Configuration error: LLM_API_KEY is not set.", output.getvalue()
        )

    def test_main_handles_eof(self) -> None:
        output = io.StringIO()

        with patch("builtins.input", side_effect=EOFError), patch(
            "sys.stdout", output
        ):
            run_cli(client=Mock())

        self.assertIn("Exiting Mini Coding Agent.", output.getvalue())

    def test_main_handles_keyboard_interrupt(self) -> None:
        output = io.StringIO()

        with patch("builtins.input", side_effect=KeyboardInterrupt), patch(
            "sys.stdout", output
        ):
            run_cli(client=Mock())

        self.assertIn("Exiting Mini Coding Agent.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
