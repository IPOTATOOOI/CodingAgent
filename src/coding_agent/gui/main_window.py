"""Mini Coding Agent 的三栏开发者工具主窗口。"""

from concurrent.futures import Future, ThreadPoolExecutor
from html import escape
from pathlib import Path
import time
from typing import Any

from PySide6.QtCore import QDir, QModelIndex, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCloseEvent, QTextDocumentFragment
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTextBrowser,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from coding_agent.agent import AgentResult, DEFAULT_MAX_STEPS, MAX_MAX_STEPS
from coding_agent.cli import SYSTEM_PROMPT
from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEvent, RuntimeEventKind
from coding_agent.gui.trace import (
    TracePresentation,
    format_tool_call,
    format_tool_result,
)
from coding_agent.gui.worker import AgentWorker, ClientFactory
from coding_agent.llm import LLMClient, ToolCall
from coding_agent.session import SessionStore
from coding_agent.verification import (
    VERIFICATION_FAILED,
    VERIFICATION_NOT_REQUIRED,
    VERIFICATION_UNVERIFIED,
    VERIFICATION_VERIFIED,
)


PREVIEW_MAX_BYTES = 512 * 1024
PREVIEW_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".ini", ".java", ".js", ".json", ".jsx", ".kt", ".md", ".php",
    ".py", ".pyw", ".rb", ".rs", ".sh", ".toml", ".ts", ".tsx",
    ".txt", ".xml", ".yaml", ".yml",
}
TONE_COLORS = {
    "normal": QColor("#c9d1d9"),
    "success": QColor("#7ee787"),
    "warning": QColor("#d29922"),
    "error": QColor("#f85149"),
    "info": QColor("#58a6ff"),
}
LIGHT_TONE_COLORS = {
    "normal": QColor("#24292f"),
    "success": QColor("#1a7f37"),
    "warning": QColor("#9a6700"),
    "error": QColor("#cf222e"),
    "info": QColor("#0969da"),
}
VERIFICATION_TEXT = {
    VERIFICATION_NOT_REQUIRED: "无需验证",
    VERIFICATION_UNVERIFIED: "等待验证",
    VERIFICATION_FAILED: "验证失败",
    VERIFICATION_VERIFIED: "验证通过",
}

STOP_REASON_TEXT = {
    "max_steps": (
        "已达到最大步骤数",
        "Agent 已停止继续操作；当前结果可能已经完成，也可能仍需要人工检查。",
    ),
    "no_progress": (
        "连续多步没有有效进展",
        "Runtime 为避免无意义循环而停止任务，可以换一种描述后重新运行。",
    ),
    "verification_required": (
        "最新代码尚未通过验证",
        "Agent 修改了代码，但没有在限制步数内取得成功的测试或检查结果。",
    ),
    "llm_error": (
        "模型请求失败",
        "LLM 服务没有成功返回，请检查网络、模型配置或稍后重试。",
    ),
    "invalid_response": (
        "模型响应无法执行",
        "Runtime 无法从本次响应中获得有效文本或工具操作。",
    ),
}


class MainWindow(QMainWindow):
    """负责界面状态，Agent 执行完全交给后台 Worker。"""

    session_save_failed = Signal(str)

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        settings: Settings | None = None,
        client_factory: ClientFactory = LLMClient,
        session_store: SessionStore | None = None,
    ) -> None:
        super().__init__()
        self.workspace = (workspace or Path.cwd()).resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {self.workspace}")
        self._settings = settings
        self._client_factory = client_factory
        self._session_store = session_store or SessionStore()
        self._session_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="session-save",
        )
        self._session_executor_shutdown = False
        self.session_save_failed.connect(self._on_session_save_failed)
        self._conversation = Conversation(SYSTEM_PROMPT)
        self._worker: AgentWorker | None = None
        self._thread: QThread | None = None
        self._trace_items: dict[str, QListWidgetItem] = {}
        self._trace_presentations: dict[str, TracePresentation] = {}
        self._last_verification = VERIFICATION_NOT_REQUIRED
        self._stream_buffer = ""
        self._started_at: float | None = None
        self._close_pending = False
        self._theme = "dark"
        self._workspace_loaded = False

        self.setWindowTitle("Mini Coding Agent")
        self.resize(1460, 860)
        self.setMinimumSize(1040, 680)
        self._build_ui(max_steps)
        self._apply_style()
        self.load_workspace(self.workspace)
        self._set_agent_state("Ready")

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

    def _build_ui(self, max_steps: int) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 8)
        root_layout.setSpacing(10)
        root_layout.addWidget(self._build_top_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.addWidget(self._build_project_panel())
        splitter.addWidget(self._build_conversation_panel())
        splitter.addWidget(self._build_activity_panel())
        splitter.setSizes([280, 650, 430])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

        self.max_steps_spin.setValue(max_steps)
        self._build_status_bar()

    def _build_top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 9, 14, 9)

        title = QLabel("Mini Coding Agent")
        title.setObjectName("appTitle")
        layout.addWidget(title)
        layout.addSpacing(18)

        layout.addWidget(QLabel("Workspace"))
        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("workspaceLabel")
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.workspace_label, 1)
        self.choose_workspace_button = QPushButton("Choose…")
        self.choose_workspace_button.setObjectName("chooseWorkspaceButton")
        self.choose_workspace_button.clicked.connect(self.choose_workspace)
        layout.addWidget(self.choose_workspace_button)

        layout.addSpacing(14)
        layout.addWidget(QLabel("Model"))
        self.model_label = QLabel(self._model_name())
        self.model_label.setObjectName("modelLabel")
        layout.addWidget(self.model_label)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Max Steps"))
        self.max_steps_spin = QSpinBox()
        self.max_steps_spin.setObjectName("maxStepsSpin")
        self.max_steps_spin.setRange(1, MAX_MAX_STEPS)
        self.max_steps_spin.setSingleStep(1)
        self.max_steps_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        self.max_steps_spin.setMinimumWidth(82)
        self.max_steps_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.max_steps_spin)
        self.new_session_button = QPushButton("New Session")
        self.new_session_button.clicked.connect(self.clear_conversation)
        layout.addWidget(self.new_session_button)
        self.auto_save_checkbox = QCheckBox("Auto-save")
        self.auto_save_checkbox.setChecked(True)
        self.auto_save_checkbox.setToolTip(
            "任务结束后在后台保存当前 Workspace 的有界会话快照"
        )
        layout.addWidget(self.auto_save_checkbox)
        self.clear_saved_sessions_button = QPushButton("Clear Saved")
        self.clear_saved_sessions_button.setToolTip("删除全部 Workspace 的已保存会话")
        self.clear_saved_sessions_button.clicked.connect(
            self.clear_all_saved_sessions
        )
        layout.addWidget(self.clear_saved_sessions_button)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setFixedSize(34, 30)
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)
        return bar

    def _build_project_panel(self) -> QWidget:
        panel, layout = self._panel("PROJECT")
        self.file_model = QFileSystemModel(self)
        self.file_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )
        self.file_model.setNameFilterDisables(False)
        self.project_tree = QTreeView()
        self.project_tree.setObjectName("projectTree")
        self.project_tree.setModel(self.file_model)
        self.project_tree.setHeaderHidden(True)
        self.project_tree.setAnimated(False)
        self.project_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.project_tree.doubleClicked.connect(self.preview_index)
        for column in range(1, 4):
            self.project_tree.hideColumn(column)
        layout.addWidget(self.project_tree, 1)
        hint = QLabel("Double-click a text file to preview")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        return panel

    def _build_conversation_panel(self) -> QWidget:
        panel, layout = self._panel("CONVERSATION")
        self.conversation_view = QTextBrowser()
        self.conversation_view.setObjectName("conversationView")
        self.conversation_view.setOpenExternalLinks(False)
        layout.addWidget(self.conversation_view, 1)

        self.task_input = QPlainTextEdit()
        self.task_input.setObjectName("taskInput")
        self.task_input.setPlaceholderText(
            "Fix the failing tests, repair the implementation and verify the result."
        )
        self.task_input.setMaximumHeight(120)
        layout.addWidget(self.task_input)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("Run Task")
        self.run_button.setObjectName("runTaskButton")
        self.run_button.setProperty("accent", True)
        self.run_button.clicked.connect(self.run_task)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_task)
        self.clear_button = QPushButton("Clear Conversation")
        self.clear_button.setObjectName("clearConversationButton")
        self.clear_button.clicked.connect(self.clear_conversation)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)
        self._append_runtime("Ready. Choose a workspace and describe a coding task.")
        return panel

    def _build_activity_panel(self) -> QWidget:
        panel, layout = self._panel("AGENT ACTIVITY / EXECUTION TRACE")
        self.current_action_label = QLabel("当前状态：等待任务")
        self.current_action_label.setObjectName("currentAction")
        self.current_action_label.setWordWrap(True)
        layout.addWidget(self.current_action_label)
        self.activity_list = QListWidget()
        self.activity_list.setObjectName("activityList")
        self.activity_list.setSpacing(5)
        self.activity_list.setWordWrap(True)
        self.activity_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.activity_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.activity_list.setUniformItemSizes(False)
        self.activity_list.itemClicked.connect(self.show_trace_details)
        layout.addWidget(self.activity_list, 1)
        hint = QLabel("点击步骤可查看真实命令、参数、退出码和有界输出")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        return panel

    @staticmethod
    def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        label = QLabel(title)
        label.setObjectName("panelTitle")
        layout.addWidget(label)
        return panel, layout

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self.agent_status = QLabel("Agent: Ready")
        self.steps_status = QLabel(f"Steps: 0 / {self.max_steps_spin.value()}")
        self.verification_status = QLabel("Verification: Not Required")
        self.tools_status = QLabel("Tool Calls: 0")
        self.elapsed_status = QLabel("Elapsed: 0.0s")
        for widget in (
            self.agent_status,
            self.steps_status,
            self.verification_status,
            self.tools_status,
            self.elapsed_status,
        ):
            status.addPermanentWidget(widget)
        self.setStatusBar(status)

    def load_workspace(self, workspace: Path) -> None:
        """刷新文件树；真正的安全边界仍由 create_tool_registry 负责。"""
        if self._workspace_loaded and self._thread is None:
            self._save_session()
        resolved = workspace.resolve()
        if not resolved.is_dir():
            raise ValueError(f"workspace is not a directory: {resolved}")
        self.workspace = resolved
        self._workspace_loaded = True
        root_index = self.file_model.setRootPath(str(resolved))
        self.project_tree.setRootIndex(root_index)
        self.workspace_label.setText(str(resolved))
        self.workspace_label.setToolTip(str(resolved))
        self._restore_session()

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Workspace",
            str(self.workspace),
        )
        if selected:
            self.activity_list.clear()
            self._trace_items.clear()
            self._trace_presentations.clear()
            self.load_workspace(Path(selected))
            self._reset_status_metrics()

    def preview_index(self, index: QModelIndex) -> None:
        path = Path(self.file_model.filePath(index))
        if not path.is_file():
            return
        try:
            content = read_preview_text(self.workspace, path)
        except (OSError, ValueError, UnicodeError) as error:
            QMessageBox.information(self, "Preview unavailable", str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Preview — {path.name}")
        dialog.resize(820, 620)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(content)
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(editor)
        dialog.exec()

    def run_task(self) -> None:
        task = self.task_input.toPlainText().strip()
        if not task:
            return
        if self._worker is not None:
            self._worker.add_steering(task)
            self.task_input.clear()
            self._append_message("USER STEERING", task, "#1f6feb")
            self._add_activity(
                "UPDATE",
                "已收到补充指令",
                "这条指令会在当前操作结束后的下一个决策步骤生效。",
                task,
                "info",
            )
            return
        if self._thread is not None:
            return
        try:
            settings = self._settings or Settings.from_env()
        except ConfigurationError as error:
            self._append_runtime(f"Configuration error: {error}", error=True)
            self._set_agent_state("Failed")
            return

        self.model_label.setText(settings.model)
        self._append_message("USER TASK", task, "#1f6feb")
        self.task_input.clear()
        self.activity_list.clear()
        self._trace_items.clear()
        self._trace_presentations.clear()
        self._last_verification = VERIFICATION_NOT_REQUIRED
        self._stream_buffer = ""
        self._set_verification(VERIFICATION_NOT_REQUIRED)
        self._set_running(True)
        self.current_action_label.setText("当前状态：正在启动 Agent…")
        self._started_at = time.monotonic()
        self._elapsed_timer.start()
        self._set_agent_state("Running")

        thread = QThread(self)
        worker = AgentWorker(
            task,
            settings,
            self._conversation,
            self.workspace,
            self.max_steps_spin.value(),
            self._client_factory,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.runtime_event.connect(self._on_runtime_event)
        worker.completed.connect(self._on_agent_completed)
        worker.failed.connect(self._on_agent_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._worker_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_runtime_event(self, event: RuntimeEvent) -> None:
        """把框架无关 Runtime Event 转换成具体 Qt 界面更新。"""
        payload = event.payload
        if event.kind in {
            RuntimeEventKind.TOOL_STARTED,
            RuntimeEventKind.TOOL_FINISHED,
        }:
            tool_call = ToolCall(
                str(payload.get("tool_call_id", "")),
                str(payload.get("tool_name", "")),
                str(payload.get("arguments", "{}")),
            )
            if event.kind == RuntimeEventKind.TOOL_STARTED:
                self._on_tool_started(event.step or 0, tool_call)
            else:
                self._on_tool_finished(
                    event.step or 0,
                    tool_call,
                    payload.get("result", {}),
                    str(payload.get("verification_status", VERIFICATION_NOT_REQUIRED)),
                )
            return
        if event.kind == RuntimeEventKind.LLM_RETRY:
            self._on_llm_retry(
                int(payload.get("retry", 0)),
                int(payload.get("max_retries", 0)),
            )
        elif event.kind == RuntimeEventKind.STEP_STARTED:
            self._stream_buffer = ""
            self.steps_status.setText(
                f"Steps: {event.step or 0} / {self.max_steps_spin.value()}"
            )
            self.current_action_label.setText(
                f"步骤 {event.step or 0}：Agent 正在分析下一步…"
            )
        elif event.kind == RuntimeEventKind.LLM_REQUEST_STARTED:
            self.current_action_label.setText(
                f"步骤 {event.step or 0}：正在等待模型决策…"
            )
        elif event.kind == RuntimeEventKind.LLM_TEXT_DELTA:
            self._stream_buffer += str(payload.get("delta", ""))
            preview = self._stream_buffer[-140:].replace("\n", " ")
            self.current_action_label.setText(f"模型正在回复：{preview}")
        elif event.kind == RuntimeEventKind.CONTEXT_BUILT:
            compacted = int(payload.get("compacted_tool_results", 0))
            dropped = int(payload.get("dropped_groups", 0))
            if compacted or dropped:
                self._add_activity(
                    "CONTEXT",
                    "已自动整理较早的上下文",
                    f"压缩 {compacted} 个旧工具结果，省略 {dropped} 组较早消息。",
                    str(payload),
                    "info",
                )

    def stop_task(self) -> None:
        """请求协作式停止；正在等待的网络请求或命令返回后才会生效。"""
        if self._worker is None:
            return
        self._worker.request_cancel()
        self.stop_button.setEnabled(False)
        self._set_agent_state("Stopping…")
        self._append_runtime("Stop requested; waiting for the current operation.")

    def clear_conversation(self) -> None:
        if self._thread is not None:
            return
        self._conversation = Conversation(SYSTEM_PROMPT)
        self.conversation_view.clear()
        try:
            self._flush_session_saves()
            self._session_store.delete(self.workspace)
        except OSError as error:
            self._append_runtime(f"Could not remove saved session: {error}", error=True)
        self.activity_list.clear()
        self._trace_items.clear()
        self._trace_presentations.clear()
        self._append_runtime("New conversation started.")
        self._reset_status_metrics()

    def clear_all_saved_sessions(self) -> None:
        """经用户确认后删除 SessionStore 中的全部会话文件。"""
        if self._thread is not None:
            return
        answer = QMessageBox.question(
            self,
            "清除全部已保存会话",
            "将删除所有 Workspace 的已保存会话。当前内存中的对话不会被清空。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._flush_session_saves()
            removed = self._session_store.clear_all()
        except OSError as error:
            self._append_runtime(f"Could not clear saved sessions: {error}", error=True)
            return
        self._append_runtime(f"已删除 {removed} 个保存的会话文件。")

    def _on_tool_started(self, step: int, tool_call: ToolCall) -> None:
        presentation = format_tool_call(tool_call)
        item = QListWidgetItem(
            f"步骤 {step}  ·  {presentation.title}  [{presentation.label}]\n"
            f"{presentation.summary}\n"
            "状态：正在执行…"
        )
        item.setData(Qt.ItemDataRole.UserRole, presentation.details)
        item.setData(Qt.ItemDataRole.UserRole + 1, presentation.tone)
        item.setForeground(self._tone_color(presentation.tone))
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()
        self._trace_items[tool_call.id] = item
        self._trace_presentations[tool_call.id] = presentation
        self.current_action_label.setText(f"当前正在做：{presentation.title}")
        self.steps_status.setText(f"Steps: {step} / {self.max_steps_spin.value()}")

    def _on_tool_finished(
        self,
        step: int,
        tool_call: ToolCall,
        result: dict[str, Any],
        verification: str,
    ) -> None:
        presentation = format_tool_result(tool_call, result)
        item = self._trace_items.get(tool_call.id)
        call_presentation = self._trace_presentations.get(tool_call.id)
        if item is None:
            item = QListWidgetItem()
            self.activity_list.addItem(item)
        action_title = (
            call_presentation.title if call_presentation is not None else presentation.label
        )
        action_summary = (
            call_presentation.summary if call_presentation is not None else ""
        )
        change_preview = (
            f"\n修改预览：\n{presentation.preview}"
            if presentation.preview
            else ""
        )
        item.setText(
            f"步骤 {step}  ·  {action_title}  [{presentation.label}]\n"
            f"{action_summary}\n"
            f"结果：{presentation.title} — {presentation.summary}"
            f"{change_preview}"
        )
        existing = str(item.data(Qt.ItemDataRole.UserRole) or "")
        item.setData(
            Qt.ItemDataRole.UserRole,
            (
                f"这一步在做什么\n{action_summary}\n\n"
                f"技术参数\n{existing}\n\n"
                f"执行结果：{presentation.title}\n{presentation.details}"
            ).strip(),
        )
        item.setData(Qt.ItemDataRole.UserRole + 1, presentation.tone)
        item.setForeground(self._tone_color(presentation.tone))
        tool_count = sum(1 for value in self._trace_items.values() if value is not None)
        self.tools_status.setText(f"Tool Calls: {tool_count}")
        if verification != self._last_verification:
            self._add_verification_trace(verification)
        self._set_verification(verification)
        self.current_action_label.setText(
            f"刚刚完成：{action_title} · {presentation.title}"
        )
        self.activity_list.scrollToBottom()

    def _on_llm_retry(self, retry: int, maximum: int) -> None:
        self._add_activity(
            "LLM RETRY",
            "模型请求暂时失败，正在自动重试",
            f"正在进行第 {retry}/{maximum} 次重试；任务暂时不需要人工操作。",
            "The request body and credentials are intentionally not displayed.",
            "warning",
        )

    def _on_agent_completed(self, result: AgentResult) -> None:
        if result.stop_reason == "completed":
            self._append_message(
                "AGENT FINAL RESPONSE",
                result.content,
                "#238636",
                markdown=True,
            )
            state = "Completed"
        elif result.stop_reason == "interrupted":
            self._append_runtime("Agent task interrupted by user.")
            state = "Ready"
        else:
            self._append_runtime(
                f"Agent stopped: {result.stop_reason}\n{result.content}",
                error=True,
            )
            state = "Failed"
            title, summary = STOP_REASON_TEXT.get(
                result.stop_reason,
                ("Agent 已停止", result.content),
            )
            self._add_activity(
                result.stop_reason.upper(), title, summary, result.content, "error"
            )
        self.steps_status.setText(
            f"Steps: {result.steps} / {self.max_steps_spin.value()}"
        )
        self.tools_status.setText(f"Tool Calls: {result.tool_calls}")
        self._set_verification(result.verification_status)
        self._set_agent_state(state)
        self.current_action_label.setText(
            {
                "Completed": "当前状态：任务完成",
                "Ready": "当前状态：任务已停止",
                "Failed": "当前状态：任务未完成，请查看最后一条说明",
            }.get(state, f"当前状态：{state}")
        )
        self._save_session()

    def _on_agent_failed(self, message: str) -> None:
        self._append_runtime(f"Agent worker failed: {message}", error=True)
        self._add_activity(
            "WORKER ERROR",
            "后台执行线程发生异常",
            "界面已恢复，可以检查详情后重新运行任务。",
            message,
            "error",
        )
        self._set_agent_state("Failed")
        self.current_action_label.setText("当前状态：后台执行失败，请查看详情")
        self._save_session()

    def _worker_thread_finished(self) -> None:
        self._elapsed_timer.stop()
        self._update_elapsed()
        self._set_running(False)
        self._worker = None
        self._thread = None
        if self._close_pending:
            self._close_pending = False
            QTimer.singleShot(0, self.close)

    def _add_verification_trace(self, verification: str) -> None:
        self._last_verification = verification
        title, summary = {
            VERIFICATION_UNVERIFIED: (
                "代码已修改，等待验证",
                "文件写入成功不代表代码正确，Agent 接下来需要运行测试、构建或语法检查。",
            ),
            VERIFICATION_FAILED: (
                "验证失败，继续修复",
                "最新测试或检查没有通过，Agent 应根据错误结果继续定位问题。",
            ),
            VERIFICATION_VERIFIED: (
                "验证通过",
                "最新一轮代码修改已经获得成功的测试、构建或语法检查证据。",
            ),
            VERIFICATION_NOT_REQUIRED: (
                "本次操作无需代码验证",
                "当前没有修改需要执行验证的代码或配置文件。",
            ),
        }.get(verification, ("验证状态变化", verification))
        tone = {
            VERIFICATION_VERIFIED: "success",
            VERIFICATION_FAILED: "error",
            VERIFICATION_UNVERIFIED: "warning",
        }.get(verification, "info")
        self._add_activity(
            "VERIFICATION",
            title,
            summary,
            f"Runtime verification status: {verification}",
            tone,
        )

    def _add_activity(
        self,
        label: str,
        title: str,
        summary: str,
        details: str,
        tone: str,
    ) -> None:
        item = QListWidgetItem(f"{title}  [{label}]\n{summary}")
        item.setData(Qt.ItemDataRole.UserRole, details)
        item.setData(Qt.ItemDataRole.UserRole + 1, tone)
        item.setForeground(self._tone_color(tone))
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()

    def show_trace_details(self, item: QListWidgetItem) -> None:
        details = str(item.data(Qt.ItemDataRole.UserRole) or "No details available.")
        dialog = QDialog(self)
        dialog.setWindowTitle("执行详情")
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit(details)
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(viewer)
        dialog.exec()

    def _append_message(
        self,
        role: str,
        content: str,
        color: str,
        *,
        markdown: bool = False,
    ) -> None:
        """用左右气泡区分用户与 Agent，并按需渲染 Markdown。"""
        del color
        body = (
            markdown_to_html_fragment(content)
            if markdown
            else escape(content).replace("\n", "<br>")
        )
        if role in {"USER TASK", "USER STEERING"}:
            role_text = (
                "USER · 用户指令"
                if role == "USER TASK"
                else "USER · 运行中补充指令"
            )
            row = (
                '<td width="18%"></td>'
                f'<td class="userBubble"><div class="bubbleRole">{role_text}</div>{body}</td>'
            )
        elif role == "AGENT FINAL RESPONSE":
            role_text = "AGENT · 最终回答"
            row = (
                f'<td class="agentBubble"><div class="bubbleRole">{role_text}</div>{body}</td>'
                '<td width="18%"></td>'
            )
        else:
            role_text = escape(role)
            row = (
                f'<td class="runtimeBubble"><div class="bubbleRole">{role_text}</div>'
                f'{body}</td>'
            )
        self.conversation_view.append(
            '<table width="100%" cellspacing="0" cellpadding="9">'
            f"<tr>{row}</tr></table><br>"
        )
        self.conversation_view.verticalScrollBar().setValue(
            self.conversation_view.verticalScrollBar().maximum()
        )

    def _append_runtime(self, content: str, *, error: bool = False) -> None:
        self._append_message("RUNTIME", content, "#f85149" if error else "#8b949e")

    def _set_running(self, running: bool) -> None:
        self.run_button.setEnabled(True)
        self.run_button.setText("Send Update" if running else "Run Task")
        self.stop_button.setEnabled(running)
        self.task_input.setEnabled(True)
        self.task_input.setPlaceholderText(
            "输入补充指令，它会在下一个步骤生效。"
            if running
            else "Fix the failing tests, repair the implementation and verify the result."
        )
        self.choose_workspace_button.setEnabled(not running)
        self.max_steps_spin.setEnabled(not running)
        self.new_session_button.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.clear_saved_sessions_button.setEnabled(not running)

    def _set_agent_state(self, state: str) -> None:
        self.agent_status.setText(f"Agent: {state}")

    def _set_verification(self, status: str) -> None:
        text = VERIFICATION_TEXT.get(status, status)
        self.verification_status.setText(f"Verification: {text}")

    def _reset_status_metrics(self) -> None:
        self._set_agent_state("Ready")
        self.steps_status.setText(f"Steps: 0 / {self.max_steps_spin.value()}")
        self.tools_status.setText("Tool Calls: 0")
        self.elapsed_status.setText("Elapsed: 0.0s")
        self._set_verification(VERIFICATION_NOT_REQUIRED)
        self.current_action_label.setText("当前状态：等待任务")

    def _update_elapsed(self) -> None:
        if self._started_at is None:
            return
        elapsed = time.monotonic() - self._started_at
        self.elapsed_status.setText(f"Elapsed: {elapsed:.1f}s")

    def _model_name(self) -> str:
        if self._settings is not None:
            return self._settings.model
        try:
            return Settings.from_env().model
        except ConfigurationError:
            return "Not configured"

    def _save_session(self) -> None:
        """把不可变消息快照提交给单线程后台写入器。"""
        if self._session_executor_shutdown:
            return
        if (
            hasattr(self, "auto_save_checkbox")
            and not self.auto_save_checkbox.isChecked()
        ):
            return
        model = (
            self.model_label.text()
            if hasattr(self, "model_label")
            else self._model_name()
        )
        messages = self._conversation.messages
        workspace = self.workspace
        future = self._session_executor.submit(
            self._session_store.save_messages,
            messages,
            workspace,
            model,
        )
        future.add_done_callback(self._session_save_completed)

    def _session_save_completed(self, future: Future[Path]) -> None:
        """从后台线程把保存异常安全转回 Qt 主线程。"""
        try:
            future.result()
        except (OSError, ValueError) as error:
            self.session_save_failed.emit(f"{type(error).__name__}: {error}")

    def _on_session_save_failed(self, message: str) -> None:
        self._append_runtime(f"Session save failed: {message}", error=True)

    def _flush_session_saves(self) -> None:
        """等待之前提交的保存任务完成，确保后续删除不会被旧任务覆盖。"""
        if self._session_executor_shutdown:
            return
        self._session_executor.submit(lambda: None).result()

    def _restore_session(self) -> None:
        """恢复当前 Workspace 的最近会话，并只展示用户与最终回答。"""
        try:
            self._session_store.cleanup()
            snapshot = self._session_store.load(self.workspace)
        except (OSError, ValueError) as error:
            self._conversation = Conversation(SYSTEM_PROMPT)
            self._append_runtime(f"Saved session could not be restored: {error}", error=True)
            return
        if snapshot is None:
            self._conversation = Conversation(SYSTEM_PROMPT)
            self.conversation_view.clear()
            self._append_runtime("Ready. Describe a coding task for this Workspace.")
            return
        self._conversation = snapshot.to_conversation()
        self.conversation_view.clear()
        restored_message = "已恢复这个 Workspace 最近保存的会话。"
        if snapshot.compacted:
            restored_message += " 较早的工具结果已按存储上限安全压缩。"
        self._append_runtime(restored_message)
        for message in snapshot.messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str) or not content:
                continue
            if role == "user":
                self._append_message("USER TASK", content, "#1f6feb")
            elif role == "assistant" and not message.get("tool_calls"):
                self._append_message(
                    "AGENT FINAL RESPONSE", content, "#238636", markdown=True
                )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._thread is not None and self._thread.isRunning():
            self._close_pending = True
            self.stop_task()
            event.ignore()
            return
        self._save_session()
        if not self._session_executor_shutdown:
            self._session_executor.shutdown(wait=True, cancel_futures=False)
            self._session_executor_shutdown = True
        event.accept()

    def toggle_theme(self) -> None:
        """在亮色和暗色主题间切换，不影响当前会话与任务状态。"""
        self._theme = "light" if self._theme == "dark" else "dark"
        self._apply_style()
        for index in range(self.activity_list.count()):
            item = self.activity_list.item(index)
            tone = str(item.data(Qt.ItemDataRole.UserRole + 1) or "normal")
            item.setForeground(self._tone_color(tone))

    def _tone_color(self, tone: str) -> QColor:
        colors = LIGHT_TONE_COLORS if self._theme == "light" else TONE_COLORS
        return colors.get(tone, colors["normal"])

    def _apply_style(self) -> None:
        palette = (
            {
                "background": "#f6f8fa",
                "surface": "#ffffff",
                "input": "#ffffff",
                "border": "#d0d7de",
                "text": "#24292f",
                "bright": "#1f2328",
                "muted": "#57606a",
                "disabled": "#8c959f",
                "button": "#f6f8fa",
                "hover": "#eaeef2",
                "selected": "#ddf4ff",
                "accent": "#0969da",
                "scroll": "#afb8c1",
            }
            if self._theme == "light"
            else {
                "background": "#0d1117",
                "surface": "#161b22",
                "input": "#0d1117",
                "border": "#30363d",
                "text": "#c9d1d9",
                "bright": "#f0f6fc",
                "muted": "#8b949e",
                "disabled": "#484f58",
                "button": "#21262d",
                "hover": "#30363d",
                "selected": "#1f2937",
                "accent": "#58a6ff",
                "scroll": "#30363d",
            }
        )
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: {palette['background']}; color: {palette['text']}; font-size: 13px;
                font-family: "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif;
            }}
            QFrame#topBar, QFrame#panel {{ background: {palette['surface']}; border: 1px solid {palette['border']}; border-radius: 7px; }}
            QLabel#appTitle {{ color: {palette['bright']}; font-size: 18px; font-weight: 700; }}
            QLabel#panelTitle {{ color: {palette['muted']}; font-size: 11px; font-weight: 700; }}
            QLabel#workspaceLabel, QLabel#modelLabel {{ color: {palette['accent']}; }}
            QLabel#mutedText {{ color: {palette['muted']}; font-size: 11px; }}
            QPushButton, QToolButton {{ background: {palette['button']}; border: 1px solid {palette['border']}; border-radius: 5px; padding: 6px 11px; }}
            QPushButton:hover, QToolButton:hover {{ background: {palette['hover']}; border-color: {palette['muted']}; }}
            QPushButton:disabled {{ color: {palette['disabled']}; background: {palette['surface']}; }}
            QPushButton[accent="true"] {{ background: #238636; border-color: #2ea043; color: white; font-weight: 600; }}
            QPushButton[accent="true"]:hover {{ background: #2ea043; }}
            QTextBrowser, QPlainTextEdit, QTreeView, QListWidget, QSpinBox {{
                background: {palette['input']}; border: 1px solid {palette['border']}; border-radius: 5px; color: {palette['text']};
                selection-background-color: {palette['accent']};
            }}
            QSpinBox {{ min-height: 26px; padding-left: 6px; padding-right: 22px; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                subcontrol-origin: border; width: 20px; background: {palette['button']};
                border-left: 1px solid {palette['border']};
            }}
            QSpinBox::up-button {{ subcontrol-position: top right; border-bottom: 1px solid {palette['border']}; }}
            QSpinBox::down-button {{ subcontrol-position: bottom right; }}
            QPlainTextEdit {{ padding: 8px; font-family: Consolas, 'Cascadia Code', monospace; }}
            QListWidget {{ padding: 5px; }}
            QListWidget::item {{ background: {palette['surface']}; border: 1px solid {palette['border']}; border-radius: 5px; padding: 8px; }}
            QListWidget::item:selected {{ background: {palette['selected']}; border-color: {palette['accent']}; }}
            QTreeView::item {{ padding: 3px; }}
            QStatusBar {{ background: {palette['surface']}; border-top: 1px solid {palette['border']}; }}
            QStatusBar QLabel {{ padding: 0 9px; color: {palette['muted']}; }}
            QSplitter::handle {{ background: {palette['background']}; width: 8px; }}
            QScrollBar:vertical {{ background: {palette['background']}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {palette['scroll']}; border-radius: 4px; min-height: 30px; }}
            """
        )
        self.theme_button.setText("☀" if self._theme == "dark" else "☾")
        self.theme_button.setToolTip(
            "切换到亮色模式" if self._theme == "dark" else "切换到暗色模式"
        )
        self._apply_conversation_style()

    def _apply_conversation_style(self) -> None:
        if self._theme == "light":
            colors = {
                "user_bg": "#0969da",
                "user_text": "#ffffff",
                "agent_bg": "#dafbe1",
                "agent_text": "#1f2328",
                "runtime_bg": "#f6f8fa",
                "runtime_text": "#57606a",
                "code_bg": "#afb8c133",
            }
        else:
            colors = {
                "user_bg": "#1f6feb",
                "user_text": "#ffffff",
                "agent_bg": "#1b4721",
                "agent_text": "#e6edf3",
                "runtime_bg": "#21262d",
                "runtime_text": "#c9d1d9",
                "code_bg": "#0d1117",
            }
        self.conversation_view.document().setDefaultStyleSheet(
            f"""
            td.userBubble {{ background-color: {colors['user_bg']}; color: {colors['user_text']}; }}
            td.agentBubble {{ background-color: {colors['agent_bg']}; color: {colors['agent_text']}; }}
            td.runtimeBubble {{ background-color: {colors['runtime_bg']}; color: {colors['runtime_text']}; }}
            .bubbleRole {{ font-weight: 700; margin-bottom: 7px; }}
            pre {{ background-color: {colors['code_bg']}; padding: 8px; white-space: pre-wrap; }}
            code {{ font-family: Consolas, 'Cascadia Code', monospace; }}
            h1, h2, h3 {{ margin-top: 8px; margin-bottom: 5px; }}
            """
        )


def markdown_to_html_fragment(markdown: str) -> str:
    """使用 Qt 自带解析器把 Markdown 转换为可嵌入气泡的富文本。"""
    html = QTextDocumentFragment.fromMarkdown(markdown).toHtml()
    body_start = html.find("<body>")
    body_end = html.rfind("</body>")
    if body_start == -1 or body_end == -1:
        return escape(markdown).replace("\n", "<br>")
    body = html[body_start + len("<body>") : body_end]
    return body.replace("<!--StartFragment-->", "").replace(
        "<!--EndFragment-->", ""
    )


def read_preview_text(workspace: Path, path: Path) -> str:
    """读取有界文本预览，同时拒绝越界、密钥文件、二进制和超大文件。"""
    root = workspace.resolve()
    target = path.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("The preview target is outside the workspace.") from None
    name = target.name.casefold()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        raise ValueError("Environment secret files are not previewed.")
    if target.suffix.casefold() not in PREVIEW_SUFFIXES:
        raise ValueError("This file type is not supported by the text preview.")
    if target.stat().st_size > PREVIEW_MAX_BYTES:
        raise ValueError("The file is too large to preview.")
    data = target.read_bytes()
    if b"\x00" in data:
        raise ValueError("Binary files cannot be previewed.")
    return data.decode("utf-8")
