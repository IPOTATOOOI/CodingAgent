"""桌面 GUI 与 Agent Runtime 连接层的基础测试。"""

import os
from pathlib import Path
import tempfile
from time import sleep
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QMessageBox,
    QStyle,
    QStyleOptionSpinBox,
)

from coding_agent.config import Settings
from coding_agent.agent import AgentResult, DEFAULT_MAX_STEPS
from coding_agent.events import RuntimeEvent, RuntimeEventKind
from coding_agent.evidence import EvidenceStore
from coding_agent.gui.app import main as gui_main
from coding_agent.gui.main_window import (
    MainWindow,
    markdown_to_html_fragment,
    read_preview_text,
    trace_details_to_html,
)
from coding_agent.gui.trace import format_tool_call, format_tool_result
from coding_agent.llm import LLMResponse, ToolCall
from coding_agent.session import SessionStore


class _SlowSuccessClient:
    def complete(self, messages, tools=None):
        del messages, tools
        time.sleep(0.15)
        return LLMResponse("Task completed from worker.", [])


class _FailingClient:
    def complete(self, messages, tools=None):
        del messages, tools
        raise RuntimeError("worker boom")


class _ApprovalClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools=None):
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                None,
                [
                    ToolCall(
                        "write-approved",
                        "write_file",
                        '{"path":"approved.txt","content":"created"}',
                    )
                ],
            )
        return LLMResponse("Created after approval.", [])


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
        self.session_store = SessionStore(self.workspace / ".test-sessions")
        self.evidence_store = EvidenceStore(self.workspace / ".test-traces")
        self.windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self.windows:
            window.close()
        self.application.processEvents()
        self.temporary_directory.cleanup()

    def _window(
        self,
        client_factory=lambda settings: _SlowSuccessClient(),
        session_store=None,
        max_steps=DEFAULT_MAX_STEPS,
    ):
        window = MainWindow(
            self.workspace,
            max_steps=max_steps,
            settings=self.settings,
            client_factory=client_factory,
            session_store=session_store or self.session_store,
            evidence_store=self.evidence_store,
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
        self.assertEqual(window.safety_mode_combo.currentText(), "询问")
        self.assertTrue(window.replay_trace_button.isEnabled())
        self.assertIsNotNone(window.findChild(QFrame, "taskComposer"))
        self.assertIsNotNone(window.findChild(QFrame, "currentActionCard"))
        self.assertEqual(
            window.activity_list.horizontalScrollBarPolicy(),
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )

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

    def test_gui_accepts_max_steps_above_previous_limit(self) -> None:
        window = self._window(max_steps=120)

        self.assertEqual(window.max_steps_spin.value(), 120)

    def test_gui_entrypoint_forwards_large_max_steps(self) -> None:
        with patch("coding_agent.gui.app.MainWindow") as window_class, patch(
            "coding_agent.gui.app.QApplication"
        ) as application_class:
            application = application_class.instance.return_value
            application.exec.return_value = 0

            exit_code = gui_main(
                ["--workspace", str(self.workspace), "--max-steps", "120"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(window_class.call_args.kwargs["max_steps"], 120)

    def test_theme_icon_switches_between_dark_and_light_modes(self) -> None:
        window = self._window()

        self.assertEqual(window._theme, "dark")
        self.assertEqual(window.theme_button.text(), "☀")
        window.toggle_theme()
        self.assertEqual(window._theme, "light")
        self.assertEqual(window.theme_button.text(), "☾")
        self.assertIn("#eef2f7", window.styleSheet())
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

        self.assertIn("用户 · 任务指令", window.conversation_view.toPlainText())
        self.assertIn("智能体 · 最终回答", window.conversation_view.toPlainText())
        self.assertIn("<table", html)
        self.assertIn("font-weight:700", html)
        self.assertNotIn("**Passed**", html)
        self.assertNotIn("<html", fragment)
        self.assertIn("font-weight:700", fragment)

    def test_trace_details_html_highlights_diff_and_escapes_code(self) -> None:
        html = trace_details_to_html(
            "修改内容\n@@ 第 8 行 @@\n-old <tag>\n+new & value",
            "light",
        )

        self.assertIn('class="section"', html)
        self.assertIn('class="hunk"', html)
        self.assertIn('class="deletion"', html)
        self.assertIn('class="addition"', html)
        self.assertIn("#dafbe1", html)
        self.assertIn("-old &lt;tag&gt;", html)
        self.assertIn("+new &amp; value", html)
        self.assertNotIn("-old <tag>", html)

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

    def test_saved_workspace_conversation_is_restored(self) -> None:
        from coding_agent.conversation import Conversation

        conversation = Conversation("System")
        conversation.add_user_message("Restore me")
        conversation.add_assistant_message("Restored answer")
        self.session_store.save(conversation, self.workspace, "test-model")

        window = self._window()

        displayed = window.conversation_view.toPlainText()
        self.assertIn("Restore me", displayed)
        self.assertIn("Restored answer", displayed)
        self.assertIn("完成状态未知", displayed)

    def test_unverified_result_is_not_presented_as_task_completion(self) -> None:
        window = self._window()

        window._on_agent_completed(
            AgentResult(
                content="Implemented, but verification is still missing.",
                stop_reason="verification_required",
                steps=13,
                tool_calls=10,
                verification_status="unverified",
                verification_reminders=5,
            )
        )

        displayed = window.conversation_view.toPlainText()
        self.assertEqual(window.agent_status.text(), "智能体：等待验证")
        self.assertIn("未验证草稿", displayed)
        self.assertIn("不是完成结果", displayed)
        self.assertNotIn("智能体 · 最终回答", displayed)

    def test_unverified_session_restores_run_state_and_draft(self) -> None:
        from coding_agent.conversation import Conversation

        conversation = Conversation("System")
        conversation.add_user_message("Build the app")
        self.session_store.save(
            conversation,
            self.workspace,
            "test-model",
            {
                "stop_reason": "verification_required",
                "steps": 13,
                "tool_calls": 10,
                "verification_status": "unverified",
                "content": "Unverified generated result",
            },
        )

        window = self._window()
        displayed = window.conversation_view.toPlainText()

        self.assertIn("未验证草稿", displayed)
        self.assertIn("Unverified generated result", displayed)
        self.assertIn("任务没有完成", displayed)
        self.assertNotIn("智能体 · 最终回答", displayed)
        self.assertEqual(window.agent_status.text(), "智能体：等待验证")
        self.assertEqual(window.steps_status.text(), "步骤：13 / 20")
        self.assertEqual(window.tools_status.text(), "工具调用：10")
        self.assertEqual(window.verification_status.text(), "验证：等待验证")

    def test_auto_save_can_be_disabled(self) -> None:
        window = self._window()
        window.auto_save_checkbox.setChecked(False)
        window._conversation.add_user_message("Do not persist")

        window._save_session()
        window._flush_session_saves()

        self.assertFalse(self.session_store.path_for(self.workspace).exists())

    def test_session_save_runs_outside_gui_thread(self) -> None:
        class SlowSessionStore(SessionStore):
            def save_messages(self, messages, workspace, model, last_run=None):
                sleep(0.2)
                return super().save_messages(messages, workspace, model, last_run)

        slow_store = SlowSessionStore(self.workspace / ".slow-sessions")
        window = self._window(session_store=slow_store)
        started = time.monotonic()

        window._save_session()
        returned_after = time.monotonic() - started

        self.assertLess(returned_after, 0.1)
        window._flush_session_saves()
        self.assertTrue(slow_store.path_for(self.workspace).exists())

    def test_clear_all_saved_sessions_requires_confirmation(self) -> None:
        other_workspace = self.workspace / "other"
        other_workspace.mkdir()
        self.session_store.save(
            self._conversation_for_test("Current"),
            self.workspace,
            "test-model",
        )
        self.session_store.save(
            self._conversation_for_test("Other"),
            other_workspace,
            "test-model",
        )
        window = self._window()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window.clear_all_saved_sessions()

        self.assertEqual(list(self.session_store.root.glob("*.json")), [])
        self.assertIn("已删除 2 个", window.conversation_view.toPlainText())

    @staticmethod
    def _conversation_for_test(content: str):
        from coding_agent.conversation import Conversation

        conversation = Conversation("System")
        conversation.add_user_message(content)
        return conversation

    def test_agent_worker_does_not_block_main_thread_and_restores_ui(self) -> None:
        window = self._window()
        window.task_input.setPlainText("finish the task")

        started = time.monotonic()
        window.run_task()
        returned_after = time.monotonic() - started

        self.assertLess(returned_after, 0.1)
        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.run_button.text(), "发送补充指令")
        self.assertTrue(window.task_input.isEnabled())
        self._wait_for_worker(window)
        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.agent_status.text(), "智能体：已完成")
        self.assertIn("Task completed from worker.", window.conversation_view.toPlainText())
        self.assertTrue(window.export_trace_button.isEnabled())
        self.assertIn("停止原因：正常完成", window.evidence_summary.text())
        self.assertEqual(len(list(self.evidence_store.root.glob("*.json"))), 1)

    def test_ask_mode_waits_before_write_and_accepts_approval(self) -> None:
        client = _ApprovalClient()
        window = self._window(lambda settings: client)
        window.show()
        self.application.processEvents()
        window.task_input.setPlainText("create a file")

        window.run_task()
        deadline = time.monotonic() + 3
        while not window.approval_frame.isVisible() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.01)

        self.assertTrue(window.approval_frame.isVisible())
        self.assertFalse((self.workspace / "approved.txt").exists())
        self.assertIn("请求修改内容", window.approval_label.text())
        self.assertIn("+created", window.approval_label.text())
        window.approve_button.click()
        self._wait_for_worker(window)

        self.assertEqual(
            (self.workspace / "approved.txt").read_text(encoding="utf-8"),
            "created",
        )
        self.assertIn("已批准工具执行", window.activity_list.item(1).text())
        self.assertEqual(window._evidence_snapshot["approvals"][0]["approved"], True)

    def test_read_only_mode_blocks_write_without_showing_approval(self) -> None:
        window = self._window(lambda settings: _ApprovalClient())
        window.safety_mode_combo.setCurrentIndex(3)
        window.task_input.setPlainText("try to create a file")

        window.run_task()
        self._wait_for_worker(window)

        self.assertFalse((self.workspace / "approved.txt").exists())
        self.assertTrue(window.approval_frame.isHidden())
        combined = "\n".join(
            window.activity_list.item(index).text()
            for index in range(window.activity_list.count())
        )
        self.assertIn("只读模式", combined)
        self.assertEqual(
            window._evidence_snapshot["tools"][0]["error"], "ReadOnlyMode"
        )

    def test_export_and_replay_trace_are_read_only(self) -> None:
        window = self._window()
        snapshot = {
            "version": 1,
            "trace_id": "replay-1",
            "task": "Repair calculator",
            "model": "test-model",
            "steps": 2,
            "tool_calls": 1,
            "files_created": 0,
            "files_modified": 1,
            "directories_created": 0,
            "verification": "verified",
            "stop_reason": "completed",
            "duration": 1.25,
            "tools": [
                {
                    "id": "edit-1",
                    "step": 1,
                    "tool": "edit_file",
                    "success": True,
                    "error": None,
                    "diff": "-value = 1\n+value = 2",
                }
            ],
        }
        source = self.evidence_store.save(snapshot)
        export_path = self.workspace / "exported-trace.json"
        original = (self.workspace / "app.py").read_text(encoding="utf-8")
        window._evidence_snapshot = snapshot

        with patch.object(
            QFileDialog,
            "getSaveFileName",
            return_value=(str(export_path), "JSON files (*.json)"),
        ):
            window.export_trace()
        with patch.object(
            QFileDialog,
            "getOpenFileName",
            return_value=(str(source), "JSON files (*.json)"),
        ):
            window.replay_trace()

        self.assertTrue(export_path.exists())
        self.assertIn("回放", window.activity_list.item(0).text())
        self.assertIn("-value = 1", window.activity_list.item(0).text())
        self.assertIn("不会执行任何工具", window.current_action_label.text())
        self.assertEqual(
            (self.workspace / "app.py").read_text(encoding="utf-8"), original
        )

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
        self.assertEqual(window.verification_status.text(), "验证：验证通过")
        self.assertIn("项目测试通过", window.current_action_label.text())

    def test_progress_warning_event_is_explained_in_activity_panel(self) -> None:
        window = self._window()

        window._on_runtime_event(
            RuntimeEvent(
                RuntimeEventKind.PROGRESS_WARNING,
                step=12,
                payload={"inspection_calls": 12},
            )
        )

        self.assertIn("读取较多", window.activity_list.item(0).text())
        self.assertIn("12 次", window.activity_list.item(0).text())
        self.assertIn("采取具体行动", window.current_action_label.text())

    def test_verification_reminder_explains_why_agent_continues(self) -> None:
        window = self._window()

        window._on_runtime_event(
            RuntimeEvent(
                RuntimeEventKind.VERIFICATION_CHANGED,
                step=13,
                payload={
                    "status": "unverified",
                    "reminder": 3,
                    "max_reminders": 8,
                    "pending_paths": ["script.js", "server.js"],
                },
            )
        )

        window._on_runtime_event(
            RuntimeEvent(
                RuntimeEventKind.VERIFICATION_CHANGED,
                step=14,
                payload={
                    "status": "unverified",
                    "reminder": 4,
                    "max_reminders": 8,
                    "pending_paths": ["server.js"],
                },
            )
        )

        self.assertEqual(window.activity_list.count(), 1)
        self.assertIn("代码还没有验证", window.activity_list.item(0).text())
        self.assertIn("4/8", window.activity_list.item(0).text())
        self.assertIn("server.js", window.activity_list.item(0).text())
        self.assertIn("任务尚未完成", window.current_action_label.text())

        window._on_runtime_event(
            RuntimeEvent(
                RuntimeEventKind.VERIFICATION_CHANGED,
                step=15,
                payload={
                    "status": "verified",
                    "outcome": "verified",
                    "covered_paths": ["server.js"],
                    "pending_paths": [],
                },
            )
        )

        self.assertEqual(window.activity_list.count(), 1)
        self.assertIn("警告已经解决", window.activity_list.item(0).text())

    def test_worker_failure_restores_controls(self) -> None:
        window = self._window(lambda settings: _FailingClient())
        window.task_input.setPlainText("trigger failure")

        window.run_task()
        self._wait_for_worker(window)

        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.agent_status.text(), "智能体：失败")
        self.assertIn("worker boom", window.conversation_view.toPlainText())

    def test_stop_requests_cooperative_cancellation(self) -> None:
        window = self._window()
        window.task_input.setPlainText("long task")

        window.run_task()
        window.stop_task()
        self._wait_for_worker(window)

        self.assertTrue(window.run_button.isEnabled())
        self.assertEqual(window.agent_status.text(), "智能体：就绪")
        self.assertIn("用户中断", window.conversation_view.toPlainText())

    def test_running_task_accepts_a_steering_update(self) -> None:
        window = self._window()
        window.task_input.setPlainText("initial task")
        window.run_task()
        window.task_input.setPlainText("also inspect tests")

        window.run_task()

        self.assertEqual(window.task_input.toPlainText(), "")
        self.assertIn(
            "运行中补充指令",
            window.conversation_view.toPlainText(),
        )
        self.assertIn("also inspect tests", window.conversation_view.toPlainText())
        self._wait_for_worker(window)

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

    def test_successful_read_trace_has_an_empty_change_preview(self) -> None:
        call = ToolCall("read-1", "read_file", '{"path":"app.py"}')

        presentation = format_tool_result(
            call,
            {
                "success": True,
                "data": {
                    "path": "app.py",
                    "start_line": 1,
                    "end_line": 1,
                    "total_lines": 1,
                    "content": "1 | value = 1",
                    "truncated": False,
                },
            },
        )

        self.assertEqual(presentation.preview, "")
        self.assertEqual(presentation.tone, "success")

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
        self.assertIn("@@ 第 8 行 → 第 8 行 @@", item_text)
        self.assertIn("✓ 已应用", item_text)
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
