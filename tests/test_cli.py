"""Tests for the Stage 0 command-line interface."""

import io
import unittest
from unittest.mock import patch

from coding_agent.cli import main


class CliTests(unittest.TestCase):
    def test_main_is_importable(self) -> None:
        self.assertTrue(callable(main))

    def test_main_displays_stage_zero_response(self) -> None:
        output = io.StringIO()

        with patch("builtins.input", return_value="fix calculator.py"), patch(
            "sys.stdout", output
        ):
            main()

        text = output.getvalue()
        self.assertIn("Mini Coding Agent", text)
        self.assertIn("Agent functionality is not implemented yet.", text)

    def test_main_handles_eof(self) -> None:
        output = io.StringIO()

        with patch("builtins.input", side_effect=EOFError), patch(
            "sys.stdout", output
        ):
            main()

        self.assertIn("Exiting Mini Coding Agent.", output.getvalue())

    def test_main_handles_keyboard_interrupt(self) -> None:
        output = io.StringIO()

        with patch("builtins.input", side_effect=KeyboardInterrupt), patch(
            "sys.stdout", output
        ):
            main()

        self.assertIn("Exiting Mini Coding Agent.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
