"""桌面 GUI 与 Agent Runtime 连接层的基础测试。"""

import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox

from coding_agent.config import Settings
from coding_agent.agent import DEFAULT_MAX_STEPS
from coding_agent.gui.main_window import (
    MainWindow,
    markdown_to_html_fragment,
    read_preview_text,
)
from coding_agent.gui.trace import format_tool_call, format_tool_result
from coding_agent.llm import LLMResponse, ToolCall


class _SlowSuccessClient:
    def complete(self, messages, tools=None):
        del messages, tools
        time.sleep(0.15)
        return LLMResponse("Task completed from worker.", [])


class _FailingClient:
    def complete(self, messages, tools=None):
        del messages, tools
        raise RuntimeError("worker boom")


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        with (self.workspace / "app.py").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            file.write("value = 1\n")
        self.settings = Settings(api_key="test-key", model="test-model")
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window.close()
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def _window(self, client_factory=lambda settings: _SlowSuccessClient()):
        window = MainWindow(
            self.workspace,
            settings=self.settings,
            client_factory=client_factory,
        )
        self.windows.append(window)
        return window

    def _wait_for_worker(self, window: MainWindow, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while window._thread is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)
        self.application.processEvents()
        self.assertIsNone(window._thread)

    def test_gui_module_and_main_window_can_be_created(self) -> None:
        window = self._window()

        self.assertEqual(window.windowTitle(), "Mini Coding Agent")
        self.assertEqual(window.model_label.text(), "test-model")
        self.assertEqual(DEFAULT_MAX_STEPS, 20)
        self.assertEqual(window.max_steps_spin.value(), 20)

    def test_max_steps_can_be_increased_and_decreased(self) -> None:
        window = self._window()
        window.show()
        self.application.processEvents()
        option = QStyleOptionSpinBox()
        window.max_steps_spin.initStyleOption(option)
        up_button = window.max_steps_spin.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox,
            option,
            QStyle.SubControl.SC_SpinBoxUp,
            window.max_steps_spin,
        )

        QTest.mouseClick(
            window.max_steps_spin,
            Qt.MouseButton.LeftButton,
            pos=up_button.center(),
        )
        self.assertEqual(window.max_steps_spin.value(), 21)
        window.max_steps_spin.stepDown()
        self.assertEqual(window.max_steps_spin.value(), 20)
        self.assertGreaterEqual(window.max_steps_spin.minimumWidth(), 82)

    def test_theme_icon_switches_between_dark_and_light_modes(self) -> None:
        window = self._window()

        self.assertEqual(window._theme, "dark")
        self.assertEqual(window.theme_button.text(), "☀")
        window.toggle_theme()
        self.assertEqual(window._theme, "light")
        self.assertEqual(window.theme_button.text(), "☾")
        self.assertIn("#f6f8fa", window.styleSheet())
        window.toggle_theme()
        self.assertEqual(window._theme, "dark")

    def test_conversation_uses_bubbles_and_renders_agent_markdown(self) -> None:
        window = self._window()
        window.conversation_view.clear()

        window._append_message("USER TASK", "Fix the tests", "#000000")
        window._append_message(
            "AGENT FINAL RESPONSE",
            "## Result\n\n**Passed**\n\n- fixed code\n- verified tests",
            "#000000",
            markdown=True,
        )
        html = window.conversation_view.toHtml()
        fragment = markdown_to_html_fragment("**Passed**")

        self.assertIn("USER · 用户指令", window.conversation_view.toPlainText())
        self.assertIn("AGENT · 最终回答", window.conversation_view.toPlainText())
        self.assertIn("<table", html)
        self.assertIn("font-weight:700", html)
        self.assertNotIn("**Passed**", html)
        self.assertNotIn("<html", fragment)
        self.assertIn("font-weight:700", fragment)

    def test_workspace_loads_and_text_preview_is_bounded(self) -> None:
        window = self._window()

        self.assertEqual(Path(window.file_model.rootPath()), self.workspace.resolve())
        self.assertEqual(
            read_preview_text(self.workspace, self.workspace / "app.py"),
            "value = 1\n",
        )
        secret = self.workspace / ".env"
        secret.write_text("TOKEN=secret\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_preview_text(self.workspace, secret)

    def test_agent_worker_does_not_block_main_thread_and_restores_ui(self) -> None:
        window = self._window()
        window.task_input.setPlainText("finish the task")

        started = time.monotonic()
        window.run_task()
        returned_after = time.monotonic() - started

        self.assertLess(returned_after, 0.1)
        self.assertFalse(window.run_button.isEnabled())
        self._wait_for_worker(window)
        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.agent_status.text(), "Agent: Completed")
        self.assertIn("Task completed from worker.", window.conversation_view.toPlainText())

    def test_trace_event_updates_item_and_verification_status(self) -> None:
        window = self._window()
        call = ToolCall(
            "run-1",
            "run_command",
            '{"command":["python","-m","unittest"],"cwd":"."}',
        )

        window._on_tool_started(1, call)
        window._on_tool_finished(
            1,
            call,
            {
                "success": True,
                "data": {
                    "cwd": ".",
                    "exit_code": 0,
                    "timed_out": False,
                    "stdout": "OK",
                    "stderr": "",
                },
            },
            "verified",
        )

        self.assertIn("运行项目测试", window.activity_list.item(0).text())
        self.assertIn("项目测试通过", window.activity_list.item(0).text())
        self.assertIn("验证通过", window.activity_list.item(1).text())
        self.assertEqual(window.verification_status.text(), "Verification: 验证通过")
        self.assertIn("项目测试通过", window.current_action_label.text())

    def test_worker_failure_restores_controls(self) -> None:
        window = self._window(lambda settings: _FailingClient())
        window.task_input.setPlainText("trigger failure")

        window.run_task()
        self._wait_for_worker(window)

        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.agent_status.text(), "Agent: Failed")
        self.assertIn("worker boom", window.conversation_view.toPlainText())

    def test_stop_requests_cooperative_cancellation(self) -> None:
        window = self._window()
        window.task_input.setPlainText("long task")

        window.run_task()
        window.stop_task()
        self._wait_for_worker(window)

        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.agent_status.text(), "Agent: Ready")
        self.assertIn("interrupted", window.conversation_view.toPlainText().lower())

    def test_trace_formatters_hide_file_content_and_bound_output(self) -> None:
        call = ToolCall(
            "edit-1",
            "edit_file",
            '{"path":"app.py","old_text":"secret old","new_text":"secret new"}',
        )
        call_view = format_tool_call(call)
        result_view = format_tool_result(
            ToolCall("run-1", "run_command", "{}"),
            {
                "success": True,
                "data": {
                    "exit_code": 1,
                    "timed_out": False,
                    "stdout": "x" * 5000,
                    "stderr": "failure",
                },
            },
        )

        self.assertNotIn("secret old", call_view.details)
        self.assertNotIn("secret new", call_view.details)
        self.assertLess(len(result_view.details), 3000)
        self.assertEqual(result_view.tone, "error")

    def test_trace_explains_actions_and_failures_in_user_language(self) -> None:
        test_call = ToolCall(
            "test-1",
            "run_command",
            '{"command":["python","-m","pytest"],"cwd":"."}',
        )
        read_call = ToolCall(
            "read-1",
            "read_file",
            '{"path":"calculator.py"}',
        )

        test_action = format_tool_call(test_call)
        failed_test = format_tool_result(
            test_call,
            {
                "success": True,
                "data": {
                    "exit_code": 1,
                    "timed_out": False,
                    "stdout": "1 failed",
                    "stderr": "",
                },
            },
        )
        read_action = format_tool_call(read_call)
        blocked = format_tool_result(
            test_call,
            {
                "success": False,
                "error": "CommandBlocked",
                "message": "blocked before process creation",
            },
        )

        self.assertEqual(test_action.title, "运行项目测试")
        self.assertIn("python -m pytest", test_action.summary)
        self.assertEqual(failed_test.title, "项目测试失败")
        self.assertIn("继续定位和修复", failed_test.summary)
        self.assertEqual(read_action.title, "读取文件 calculator.py")
        self.assertEqual(blocked.title, "命令已被安全策略阻止")
        self.assertIn("进程启动前", blocked.summary)

    def test_successful_edit_shows_bounded_old_and_new_diff(self) -> None:
        window = self._window()
        call = ToolCall(
            "edit-diff",
            "edit_file",
            '{"path":"calculator.py","old_text":"return a - b",'
            '"new_text":"return a + b"}',
        )
        result = {
            "success": True,
            "data": {
                "path": "calculator.py",
                "modified": True,
                "replacements": 1,
                "start_line": 8,
                "old_end_line": 8,
                "new_end_line": 8,
            },
        }

        presentation = format_tool_result(call, result)
        window._on_tool_started(3, call)
        window._on_tool_finished(3, call, result, "unverified")
        item_text = window.activity_list.item(0).text()

        self.assertIn("第 8 行 → 第 8 行", presentation.summary)
        self.assertIn("-return a - b", presentation.preview)
        self.assertIn("+return a + b", presentation.preview)
        self.assertIn("修改预览", item_text)
        self.assertIn("-return a - b", item_text)
        self.assertIn("+return a + b", item_text)
        self.assertIn("修改内容", presentation.details)

    def test_failed_edit_does_not_display_requested_diff(self) -> None:
        call = ToolCall(
            "failed-edit",
            "edit_file",
            '{"path":"app.py","old_text":"not applied",'
            '"new_text":"must stay hidden"}',
        )

        presentation = format_tool_result(
            call,
            {
                "success": False,
                "error": "TextNotFound",
                "message": "old_text was not found",
            },
        )

        self.assertEqual(presentation.preview, "")
        self.assertNotIn("must stay hidden", presentation.details)


if __name__ == "__main__":
    unittest.main()
