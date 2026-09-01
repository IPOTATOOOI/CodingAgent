"""Mini Coding Agent 的三栏开发者工具主窗口。"""

from concurrent.futures import Future, ThreadPoolExecutor
from html import escape
import json
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
    QComboBox,
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

from coding_agent.agent import AgentResult, DEFAULT_MAX_STEPS, MIN_MAX_STEPS
from coding_agent.approval import SafetyMode
from coding_agent.cli import SYSTEM_PROMPT
from coding_agent.config import ConfigurationError, Settings
from coding_agent.conversation import Conversation
from coding_agent.events import RuntimeEvent, RuntimeEventKind
from coding_agent.evidence import EvidenceStore, EvidenceTrailBuilder
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
# QSpinBox 内部使用 32 位有符号整数；这只是界面控件的技术边界，
# Agent Runtime 本身不再设置人为的最大步数上限。
QT_SPINBOX_MAXIMUM = 2_147_483_647
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
TONE_BACKGROUNDS = {
    "normal": QColor("#151b23"),
    "success": QColor("#12261e"),
    "warning": QColor("#2b2111"),
    "error": QColor("#2f171c"),
    "info": QColor("#13233a"),
}
LIGHT_TONE_BACKGROUNDS = {
    "normal": QColor("#ffffff"),
    "success": QColor("#dafbe1"),
    "warning": QColor("#fff8c5"),
    "error": QColor("#ffebe9"),
    "info": QColor("#ddf4ff"),
}
VERIFICATION_TEXT = {
    VERIFICATION_NOT_REQUIRED: "无需验证",
    VERIFICATION_UNVERIFIED: "等待验证",
    VERIFICATION_FAILED: "验证失败",
    VERIFICATION_VERIFIED: "验证通过",
}

AGENT_STATE_TEXT = {
    "Ready": "就绪",
    "Running": "运行中",
    "Completed": "已完成",
    "Failed": "失败",
    "Needs Verification": "等待验证",
    "Stopping…": "正在停止…",
}

STOP_REASON_LABELS = {
    "completed": "正常完成",
    "interrupted": "用户中断",
    "max_steps": "达到最大步骤数",
    "no_progress": "连续多步没有有效进展",
    "verification_required": "仍需验证",
    "llm_error": "模型请求失败",
    "invalid_response": "模型响应无效",
    "worker_error": "后台任务异常",
    "unknown": "未知",
}


def trace_details_to_html(details: str, theme: str = "dark") -> str:
    """把原始 Trace 文本渲染成安全、有配色的只读详情。"""
    palette = (
        {
            "background": "#ffffff",
            "text": "#24292f",
            "muted": "#57606a",
            "border": "#d0d7de",
            "add_text": "#116329",
            "add_background": "#dafbe1",
            "delete_text": "#82071e",
            "delete_background": "#ffebe9",
            "hunk_text": "#0550ae",
            "hunk_background": "#ddf4ff",
            "section_background": "#f6f8fa",
        }
        if theme == "light"
        else {
            "background": "#0d1117",
            "text": "#e6edf3",
            "muted": "#8b949e",
            "border": "#30363d",
            "add_text": "#aff5b4",
            "add_background": "#12261e",
            "delete_text": "#ffdcd7",
            "delete_background": "#321c20",
            "hunk_text": "#a5d6ff",
            "hunk_background": "#13233a",
            "section_background": "#161b22",
        }
    )
    section_titles = {
        "这一步在做什么",
        "技术参数",
        "执行结果",
        "修改内容",
        "参数",
        "Runtime 信息",
        "标准输出预览",
        "错误输出预览",
    }
    rendered_lines: list[str] = []
    for line in details.splitlines() or [""]:
        stripped = line.strip()
        if line.startswith("+") and not line.startswith("+++"):
            css_class = "addition"
        elif line.startswith("-") and not line.startswith("---"):
            css_class = "deletion"
        elif line.startswith("@@"):
            css_class = "hunk"
        elif stripped.rstrip("：") in section_titles or any(
            stripped.startswith(f"{title}：") for title in section_titles
        ):
            css_class = "section"
        elif line.startswith(("+++", "---")):
            css_class = "metadata"
        else:
            css_class = "line"
        visible = escape(line) if line else "&nbsp;"
        rendered_lines.append(f'<div class="{css_class}">{visible}</div>')

    return f"""
    <html><head><style>
      body {{ margin: 0; background: {palette['background']}; color: {palette['text']};
              font-family: Consolas, 'Cascadia Code', 'Microsoft YaHei UI', monospace;
              font-size: 13px; }}
      .line, .addition, .deletion, .hunk, .metadata, .section {{
              white-space: pre; padding: 3px 12px; }}
      .addition {{ color: {palette['add_text']}; background: {palette['add_background']};
                   border-left: 3px solid #2da44e; }}
      .deletion {{ color: {palette['delete_text']}; background: {palette['delete_background']};
                   border-left: 3px solid #cf222e; }}
      .hunk {{ color: {palette['hunk_text']}; background: {palette['hunk_background']};
               border-left: 3px solid #218bff; font-weight: 600; }}
      .metadata {{ color: {palette['muted']}; font-weight: 600; }}
      .section {{ margin-top: 8px; color: {palette['text']};
                  background: {palette['section_background']};
                  border-top: 1px solid {palette['border']};
                  border-bottom: 1px solid {palette['border']}; font-weight: 700; }}
    </style></head><body>{''.join(rendered_lines)}</body></html>
    """

STOP_REASON_TEXT = {
    "max_steps": (
        "已达到最大步骤数",
        "智能体已停止继续操作；当前结果可能已经完成，也可能仍需要人工检查。",
    ),
    "no_progress": (
        "连续多步没有有效进展",
        "Runtime 为避免无意义循环而停止任务，可以换一种描述后重新运行。",
    ),
    "verification_required": (
        "最新代码尚未通过验证",
        "智能体多次尝试结束任务，但仍没有取得 Runtime 能确认的测试或检查结果。",
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
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        super().__init__()
        self.workspace = (workspace or Path.cwd()).resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {self.workspace}")
        self._settings = settings
        self._client_factory = client_factory
        self._session_store = session_store or SessionStore()
        self._evidence_store = evidence_store or EvidenceStore()
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
        self._verification_guidance_item: QListWidgetItem | None = None
        self._last_verification = VERIFICATION_NOT_REQUIRED
        self._last_run_state: dict[str, object] | None = None
        self._evidence_builder: EvidenceTrailBuilder | None = None
        self._evidence_snapshot: dict[str, Any] | None = None
        self._evidence_path: Path | None = None
        self._pending_approval_id: int | None = None
        self._pending_approval_request: dict[str, Any] | None = None
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
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        primary = QHBoxLayout()
        primary.setSpacing(10)
        app_mark = QLabel("MC")
        app_mark.setObjectName("appMark")
        app_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        app_mark.setFixedSize(38, 38)
        primary.addWidget(app_mark)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("Mini Coding Agent")
        title.setObjectName("appTitle")
        subtitle = QLabel("本地可观察的智能编码工作台")
        subtitle.setObjectName("appSubtitle")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        primary.addLayout(brand)
        primary.addSpacing(24)

        workspace_group = QVBoxLayout()
        workspace_group.setSpacing(1)
        workspace_caption = QLabel("当前工作区")
        workspace_caption.setObjectName("fieldCaption")
        workspace_group.addWidget(workspace_caption)
        self.workspace_label = QLabel()
        self.workspace_label.setObjectName("workspaceLabel")
        self.workspace_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        workspace_group.addWidget(self.workspace_label)
        primary.addLayout(workspace_group, 1)
        self.choose_workspace_button = QPushButton("选择工作区")
        self.choose_workspace_button.setObjectName("chooseWorkspaceButton")
        self.choose_workspace_button.clicked.connect(self.choose_workspace)
        primary.addWidget(self.choose_workspace_button)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setFixedSize(38, 34)
        self.theme_button.clicked.connect(self.toggle_theme)
        primary.addWidget(self.theme_button)
        layout.addLayout(primary)

        divider = QFrame()
        divider.setObjectName("topDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        model_caption = QLabel("模型")
        model_caption.setObjectName("fieldCaption")
        controls.addWidget(model_caption)
        self.model_label = QLabel(self._model_name())
        self.model_label.setObjectName("modelLabel")
        controls.addWidget(self.model_label)
        controls.addSpacing(12)
        steps_caption = QLabel("最大步骤")
        steps_caption.setObjectName("fieldCaption")
        controls.addWidget(steps_caption)
        self.max_steps_spin = QSpinBox()
        self.max_steps_spin.setObjectName("maxStepsSpin")
        self.max_steps_spin.setRange(MIN_MAX_STEPS, QT_SPINBOX_MAXIMUM)
        self.max_steps_spin.setSingleStep(1)
        self.max_steps_spin.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.UpDownArrows
        )
        self.max_steps_spin.setMinimumWidth(82)
        self.max_steps_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.max_steps_spin)
        controls.addSpacing(12)
        safety_caption = QLabel("安全模式")
        safety_caption.setObjectName("fieldCaption")
        controls.addWidget(safety_caption)
        self.safety_mode_combo = QComboBox()
        self.safety_mode_combo.setObjectName("safetyModeCombo")
        self.safety_mode_combo.addItem("询问", SafetyMode.ASK.value)
        self.safety_mode_combo.addItem("自动编辑", SafetyMode.AUTO_EDIT.value)
        self.safety_mode_combo.addItem("全自动", SafetyMode.AUTO.value)
        self.safety_mode_combo.addItem("只读", SafetyMode.READ_ONLY.value)
        self.safety_mode_combo.setToolTip(
            "询问：修改与命令均需确认；自动编辑：文件修改自动执行、命令需确认；"
            "全自动：按 Runtime Policy 自动执行；只读：禁止修改文件和运行命令"
        )
        controls.addWidget(self.safety_mode_combo)
        controls.addStretch(1)
        self.new_session_button = QPushButton("新建会话")
        self.new_session_button.clicked.connect(self.clear_conversation)
        controls.addWidget(self.new_session_button)
        self.auto_save_checkbox = QCheckBox("自动保存")
        self.auto_save_checkbox.setChecked(True)
        self.auto_save_checkbox.setToolTip(
            "任务结束后在后台保存当前工作区的有界会话快照"
        )
        controls.addWidget(self.auto_save_checkbox)
        self.clear_saved_sessions_button = QPushButton("清除存档")
        self.clear_saved_sessions_button.setToolTip("删除全部工作区的已保存会话")
        self.clear_saved_sessions_button.clicked.connect(
            self.clear_all_saved_sessions
        )
        controls.addWidget(self.clear_saved_sessions_button)
        layout.addLayout(controls)
        return bar

    def _build_project_panel(self) -> QWidget:
        panel, layout = self._panel("项目文件", "project")
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
        hint = QLabel("双击文本文件可预览内容")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        return panel

    def _build_conversation_panel(self) -> QWidget:
        panel, layout = self._panel("对话", "conversation")
        self.conversation_view = QTextBrowser()
        self.conversation_view.setObjectName("conversationView")
        self.conversation_view.setOpenExternalLinks(False)
        layout.addWidget(self.conversation_view, 1)

        composer = QFrame()
        composer.setObjectName("taskComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 9, 10, 9)
        composer_layout.setSpacing(7)
        composer_header = QHBoxLayout()
        composer_title = QLabel("向智能体描述任务")
        composer_title.setObjectName("composerTitle")
        composer_hint = QLabel("支持多行输入 · 运行时可补充指令")
        composer_hint.setObjectName("mutedText")
        composer_header.addWidget(composer_title)
        composer_header.addStretch(1)
        composer_header.addWidget(composer_hint)
        composer_layout.addLayout(composer_header)

        self.task_input = QPlainTextEdit()
        self.task_input.setObjectName("taskInput")
        self.task_input.setPlaceholderText(
            "例如：运行项目测试，定位并修复失败的实现，然后验证结果。"
        )
        self.task_input.setMaximumHeight(120)
        composer_layout.addWidget(self.task_input)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("运行任务")
        self.run_button.setObjectName("runTaskButton")
        self.run_button.setProperty("accent", True)
        self.run_button.clicked.connect(self.run_task)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_task)
        self.clear_button = QPushButton("清空对话")
        self.clear_button.setObjectName("clearConversationButton")
        self.clear_button.clicked.connect(self.clear_conversation)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        composer_layout.addLayout(buttons)
        layout.addWidget(composer)
        self._append_runtime("已就绪。请选择工作区并描述编码任务。")
        return panel

    def _build_activity_panel(self) -> QWidget:
        panel, layout = self._panel("智能体活动 / 执行轨迹", "activity")
        current_frame = QFrame()
        current_frame.setObjectName("currentActionCard")
        current_layout = QHBoxLayout(current_frame)
        current_layout.setContentsMargins(10, 8, 10, 8)
        current_layout.setSpacing(8)
        self.current_action_dot = QLabel("●")
        self.current_action_dot.setObjectName("currentActionDot")
        self.current_action_dot.setProperty("state", "Ready")
        current_layout.addWidget(
            self.current_action_dot, 0, Qt.AlignmentFlag.AlignTop
        )
        current_text_layout = QVBoxLayout()
        current_text_layout.setSpacing(2)
        current_caption = QLabel("实时状态")
        current_caption.setObjectName("fieldCaption")
        current_text_layout.addWidget(current_caption)
        self.current_action_label = QLabel("当前状态：等待任务")
        self.current_action_label.setObjectName("currentAction")
        self.current_action_label.setWordWrap(True)
        current_text_layout.addWidget(self.current_action_label)
        current_layout.addLayout(current_text_layout, 1)
        layout.addWidget(current_frame)

        self.approval_frame = QFrame()
        self.approval_frame.setObjectName("approvalCard")
        approval_layout = QVBoxLayout(self.approval_frame)
        approval_layout.setContentsMargins(9, 8, 9, 8)
        self.approval_label = QLabel()
        self.approval_label.setWordWrap(True)
        approval_layout.addWidget(self.approval_label)
        approval_buttons = QHBoxLayout()
        self.reject_button = QPushButton("拒绝")
        self.approve_button = QPushButton("批准")
        self.approve_button.setProperty("accent", True)
        self.reject_button.clicked.connect(lambda: self._resolve_approval(False))
        self.approve_button.clicked.connect(lambda: self._resolve_approval(True))
        approval_buttons.addStretch(1)
        approval_buttons.addWidget(self.reject_button)
        approval_buttons.addWidget(self.approve_button)
        approval_layout.addLayout(approval_buttons)
        self.approval_frame.hide()
        layout.addWidget(self.approval_frame)

        self.evidence_frame = QFrame()
        self.evidence_frame.setObjectName("evidenceCard")
        evidence_layout = QVBoxLayout(self.evidence_frame)
        evidence_layout.setContentsMargins(9, 8, 9, 8)
        evidence_header = QHBoxLayout()
        evidence_title = QLabel("任务证据")
        evidence_title.setObjectName("evidenceTitle")
        evidence_badge = QLabel("自动记录")
        evidence_badge.setObjectName("evidenceBadge")
        evidence_header.addWidget(evidence_title)
        evidence_header.addStretch(1)
        evidence_header.addWidget(evidence_badge)
        evidence_layout.addLayout(evidence_header)
        self.evidence_summary = QLabel("等待任务完成后生成结构化证据。")
        self.evidence_summary.setWordWrap(True)
        evidence_layout.addWidget(self.evidence_summary)
        evidence_buttons = QHBoxLayout()
        self.export_trace_button = QPushButton("导出轨迹")
        self.replay_trace_button = QPushButton("回放轨迹")
        self.export_trace_button.setEnabled(False)
        self.export_trace_button.clicked.connect(self.export_trace)
        self.replay_trace_button.clicked.connect(self.replay_trace)
        evidence_buttons.addWidget(self.export_trace_button)
        evidence_buttons.addWidget(self.replay_trace_button)
        evidence_buttons.addStretch(1)
        evidence_layout.addLayout(evidence_buttons)
        layout.addWidget(self.evidence_frame)
        timeline_header = QHBoxLayout()
        timeline_title = QLabel("执行时间线")
        timeline_title.setObjectName("timelineTitle")
        timeline_hint = QLabel("点击任意步骤查看详情")
        timeline_hint.setObjectName("mutedText")
        timeline_header.addWidget(timeline_title)
        timeline_header.addStretch(1)
        timeline_header.addWidget(timeline_hint)
        layout.addLayout(timeline_header)
        self.activity_list = QListWidget()
        self.activity_list.setObjectName("activityList")
        self.activity_list.setSpacing(5)
        self.activity_list.setWordWrap(True)
        self.activity_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.activity_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.activity_list.setUniformItemSizes(False)
        self.activity_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.activity_list.itemClicked.connect(self.show_trace_details)
        layout.addWidget(self.activity_list, 1)
        hint = QLabel("点击步骤可查看真实命令、参数、退出码和有界输出")
        hint.setObjectName("mutedText")
        layout.addWidget(hint)
        return panel

    @staticmethod
    def _panel(title: str, section: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(11, 10, 11, 11)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        accent = QFrame()
        accent.setObjectName("panelAccent")
        accent.setProperty("section", section)
        accent.setFixedSize(4, 17)
        header.addWidget(accent)
        label = QLabel(title)
        label.setObjectName("panelTitle")
        label.setProperty("section", section)
        header.addWidget(label)
        header.addStretch(1)
        layout.addLayout(header)
        return panel, layout

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self.agent_status = QLabel("智能体：就绪")
        self.agent_status.setObjectName("agentStatus")
        self.agent_status.setProperty("state", "Ready")
        self.steps_status = QLabel(f"步骤：0 / {self.max_steps_spin.value()}")
        self.steps_status.setObjectName("stepsStatus")
        self.verification_status = QLabel("验证：无需验证")
        self.verification_status.setObjectName("verificationStatus")
        self.verification_status.setProperty(
            "verification", VERIFICATION_NOT_REQUIRED
        )
        self.tools_status = QLabel("工具调用：0")
        self.tools_status.setObjectName("toolsStatus")
        self.elapsed_status = QLabel("耗时：0.0 秒")
        self.elapsed_status.setObjectName("elapsedStatus")
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
            "选择工作区",
            str(self.workspace),
        )
        if selected:
            self.activity_list.clear()
            self._trace_items.clear()
            self._trace_presentations.clear()
            self._verification_guidance_item = None
            self.load_workspace(Path(selected))

    def preview_index(self, index: QModelIndex) -> None:
        path = Path(self.file_model.filePath(index))
        if not path.is_file():
            return
        try:
            content = read_preview_text(self.workspace, path)
        except (OSError, ValueError, UnicodeError) as error:
            QMessageBox.information(self, "无法预览", str(error))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"文件预览 — {path.name}")
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
                "补充指令",
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
            self._append_runtime(f"配置错误：{error}", error=True)
            self._set_agent_state("Failed")
            return

        self.model_label.setText(settings.model)
        self._append_message("USER TASK", task, "#1f6feb")
        self.task_input.clear()
        self.activity_list.clear()
        self._trace_items.clear()
        self._trace_presentations.clear()
        self._verification_guidance_item = None
        self._last_verification = VERIFICATION_NOT_REQUIRED
        self._last_run_state = None
        self._evidence_snapshot = None
        self._evidence_path = None
        self._evidence_builder = EvidenceTrailBuilder(
            self.workspace,
            settings.model,
            self.max_steps_spin.value(),
        )
        self.evidence_summary.setText("正在采集本次任务的 Runtime 证据…")
        self.export_trace_button.setEnabled(False)
        self._stream_buffer = ""
        self._set_verification(VERIFICATION_NOT_REQUIRED)
        self._set_running(True)
        self.current_action_label.setText("当前状态：正在启动智能体…")
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
            SafetyMode(str(self.safety_mode_combo.currentData())),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.runtime_event.connect(self._on_runtime_event)
        worker.approval_requested.connect(self._on_approval_requested)
        worker.result_ready.connect(self._on_agent_completed)
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
        if self._evidence_builder is not None:
            self._evidence_builder.record(event)
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
                f"步骤：{event.step or 0} / {self.max_steps_spin.value()}"
            )
            self.current_action_label.setText(
                f"步骤 {event.step or 0}：智能体正在分析下一步…"
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
                    "上下文",
                    "已自动整理较早的上下文",
                    f"压缩 {compacted} 个旧工具结果，省略 {dropped} 组较早消息。",
                    str(payload),
                    "info",
                )
        elif event.kind == RuntimeEventKind.PROGRESS_WARNING:
            count = int(payload.get("inspection_calls", 0))
            self._add_activity(
                "进度提醒",
                "读取较多，但还没有采取下一步行动",
                f"自上次修改或命令执行后已查看 {count} 次；Runtime 已提醒智能体汇总信息、修改、验证或完成任务。",
                str(payload),
                "warning",
            )
            self.current_action_label.setText(
                "已提醒智能体：停止重复查看并采取具体行动"
            )
        elif event.kind == RuntimeEventKind.VERIFICATION_CHANGED:
            reminder = int(payload.get("reminder", 0))
            maximum = int(payload.get("max_reminders", 0))
            outcome = str(payload.get("outcome", ""))
            pending_paths = payload.get("pending_paths", [])
            pending = (
                "、".join(str(path) for path in pending_paths[:6])
                if isinstance(pending_paths, list)
                else ""
            )
            if reminder > 0:
                progress = f"{reminder}/{maximum}" if maximum > 0 else str(reminder)
                title = "智能体想结束，但代码还没有验证"
                summary = (
                    f"Runtime 已拒绝这次完成请求（验证提醒 {progress}）。"
                    f"仍待验证：{pending or '请查看详情'}。"
                )
                self.current_action_label.setText(
                    f"验证提醒 {progress}：任务尚未完成，智能体将继续处理"
                )
            elif outcome == "partial":
                title = "部分文件已经验证"
                summary = f"这次检查有效，但仍待验证：{pending or '请查看详情'}。"
            elif outcome == "no_coverage":
                title = "检查命令没有覆盖待验证文件"
                summary = (
                    f"智能体选错了检查目标；仍待验证：{pending or '请查看详情'}。"
                )
            elif (
                outcome == "verified"
                and self._verification_guidance_item is not None
            ):
                title = "先前的验证警告已经解决"
                summary = "所有待验证代码文件都已获得成功执行证据。"
            elif pending and self._verification_guidance_item is not None:
                title = "待验证文件范围已经更新"
                summary = f"当前仍待验证：{pending}。"
            else:
                return
            text = f"{title}  [验证]\n{summary}"
            tone = "success" if outcome == "verified" else "warning"
            if self._verification_guidance_item is None:
                self._verification_guidance_item = self._add_activity(
                    "验证", title, summary, str(payload), tone
                )
            else:
                self._verification_guidance_item.setText(text)
                self._verification_guidance_item.setData(
                    Qt.ItemDataRole.UserRole, str(payload)
                )
                self._verification_guidance_item.setData(
                    Qt.ItemDataRole.UserRole + 1, tone
                )
                self._style_trace_item(self._verification_guidance_item, tone)
                self.activity_list.scrollToItem(self._verification_guidance_item)
            if outcome == "verified":
                self._verification_guidance_item = None

    def _on_approval_requested(self, request: dict[str, Any]) -> None:
        """显示非阻塞授权卡片；Worker 在 handler 启动前等待用户选择。"""
        request_id = int(request.get("request_id", 0))
        self._pending_approval_id = request_id
        self._pending_approval_request = dict(request)
        tool_name = str(request.get("tool_name", "tool"))
        arguments = request.get("arguments", {})
        details = json.dumps(arguments, ensure_ascii=False, indent=2)
        preview = str(request.get("preview", ""))
        preview_text = f"\n\n请求修改内容\n{preview}" if preview else ""
        self.approval_label.setText(
            f"智能体请求授权\n{tool_name}\n{details}{preview_text}"
        )
        self.approval_frame.show()
        self.current_action_label.setText(
            f"等待授权：{tool_name} 尚未执行"
        )

    def _resolve_approval(self, approved: bool) -> None:
        """把用户决定送回 Worker，并在 Evidence Trail 中留下证据。"""
        if self._worker is None or self._pending_approval_id is None:
            return
        request_id = self._pending_approval_id
        request = self._pending_approval_request or {}
        self._worker.resolve_approval(request_id, approved)
        if self._evidence_builder is not None:
            self._evidence_builder.record_approval(request, approved)
        tool_name = str(request.get("tool_name", "tool"))
        self._add_activity(
            "授权",
            "已批准工具执行" if approved else "已拒绝工具执行",
            (
                f"{tool_name} 现在可以由 Runtime 继续执行。"
                if approved
                else f"{tool_name} 没有执行，智能体将收到拒绝结果。"
            ),
            json.dumps(request, ensure_ascii=False, indent=2),
            "success" if approved else "warning",
        )
        self.approval_frame.hide()
        self._pending_approval_id = None
        self._pending_approval_request = None

    def _finalize_evidence(
        self,
        *,
        result: AgentResult | None = None,
        error: str | None = None,
    ) -> None:
        """完成并自动保存当前任务 Trace；失败不会改变 AgentResult。"""
        if self._evidence_builder is None:
            return
        snapshot = (
            self._evidence_builder.finalize(result)
            if result is not None
            else self._evidence_builder.fail(error or "后台任务失败")
        )
        self._evidence_snapshot = snapshot
        self._evidence_builder = None
        try:
            self._evidence_path = self._evidence_store.save(snapshot)
        except (OSError, ValueError) as save_error:
            self._evidence_path = None
            self._append_runtime(
                f"证据轨迹保存失败：{save_error}", error=True
            )
        self._render_evidence_summary(snapshot)
        self.export_trace_button.setEnabled(True)

    def _render_evidence_summary(self, snapshot: dict[str, Any]) -> None:
        verification = str(snapshot.get("verification", "not_required"))
        verified_text, verification_color = {
            "verified": ("✓ 验证通过", "#2da44e"),
            "failed": ("✗ 验证失败", "#cf222e"),
            "unverified": ("! 等待验证", "#d29922"),
            "not_required": ("– 无需验证", "#8b949e"),
        }.get(verification, (f"– 验证状态：{verification}", "#8b949e"))
        raw_stop_reason = str(snapshot.get("stop_reason", "unknown"))
        stop_reason = STOP_REASON_LABELS.get(raw_stop_reason, raw_stop_reason)
        card_background = "#f1f5f9" if self._theme == "light" else "#0c1119"
        card_text = "#172033" if self._theme == "light" else "#f8fafc"
        muted = "#64748b" if self._theme == "light" else "#8492a6"
        self.evidence_summary.setText(
            f'<table width="100%" cellspacing="4" cellpadding="7">'
            f'<tr><td bgcolor="{card_background}"><span style="color:{muted};">新建文件</span><br>'
            f'<span style="color:{card_text}; font-size:17px; font-weight:700;">{snapshot.get("files_created", 0)}</span></td>'
            f'<td bgcolor="{card_background}"><span style="color:{muted};">修改文件</span><br>'
            f'<span style="color:{card_text}; font-size:17px; font-weight:700;">{snapshot.get("files_modified", 0)}</span></td>'
            f'<td bgcolor="{card_background}"><span style="color:{muted};">新建目录</span><br>'
            f'<span style="color:{card_text}; font-size:17px; font-weight:700;">{snapshot.get("directories_created", 0)}</span></td></tr>'
            f'</table><p style="color:{verification_color}; font-weight:700;">{escape(verified_text)}</p>'
            f'<p style="color:{muted};">步骤 {snapshot.get("steps", 0)}　·　'
            f'工具调用 {snapshot.get("tool_calls", 0)}　·　'
            f'耗时 {float(snapshot.get("duration", 0.0)):.1f} 秒</p>'
            f'<p style="color:{card_text};">停止原因：{escape(stop_reason)}</p>'
        )

    def export_trace(self) -> None:
        """把当前 Evidence Snapshot 导出为用户选择的 JSON 文件。"""
        if self._evidence_snapshot is None:
            return
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出任务证据轨迹",
            str(self.workspace / "agent-evidence.json"),
            "JSON 文件 (*.json)",
        )
        if not selected:
            return
        try:
            path = self._evidence_store.export(
                self._evidence_snapshot, Path(selected)
            )
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导出失败", str(error))
            return
        self._append_runtime(f"证据轨迹已导出：{path}")

    def replay_trace(self) -> None:
        """加载并只读渲染历史 Trace，不调用 LLM、工具或 Workspace。"""
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "回放任务证据轨迹",
            str(self._evidence_store.root),
            "JSON 文件 (*.json)",
        )
        if not selected:
            return
        try:
            snapshot = self._evidence_store.load(Path(selected))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "回放失败", str(error))
            return
        self._evidence_snapshot = snapshot
        self._render_evidence_summary(snapshot)
        self.export_trace_button.setEnabled(True)
        self.activity_list.clear()
        self._trace_items.clear()
        self._trace_presentations.clear()
        for record in snapshot.get("tools", []):
            if not isinstance(record, dict):
                continue
            tool_name = str(record.get("tool", "tool"))
            success = record.get("success") is True
            error = record.get("error")
            result_text = "✓ 已完成" if success else f"✗ {error or '失败'}"
            diff = str(record.get("diff", ""))
            summary = result_text + (f"\n{diff}" if diff else "")
            details = json.dumps(record, ensure_ascii=False, indent=2)
            self._add_activity(
                f"回放 · {tool_name}",
                f"步骤 {record.get('step', 0)} · {tool_name}",
                summary,
                details,
                "success" if success else "error",
            )
        self.current_action_label.setText(
            "回放：正在查看历史证据，不会执行任何工具"
        )

    def stop_task(self) -> None:
        """请求协作式停止；正在等待的网络请求或命令返回后才会生效。"""
        if self._worker is None:
            return
        if (
            self._pending_approval_request is not None
            and self._evidence_builder is not None
        ):
            self._evidence_builder.record_approval(
                self._pending_approval_request, False
            )
        self._worker.request_cancel()
        self.approval_frame.hide()
        self._pending_approval_id = None
        self._pending_approval_request = None
        self.stop_button.setEnabled(False)
        self._set_agent_state("Stopping…")
        self._append_runtime("已请求停止，正在等待当前操作到达安全停止点。")

    def clear_conversation(self) -> None:
        if self._thread is not None:
            return
        self._conversation = Conversation(SYSTEM_PROMPT)
        self._last_run_state = None
        self.conversation_view.clear()
        try:
            self._flush_session_saves()
            self._session_store.delete(self.workspace)
        except OSError as error:
            self._append_runtime(f"无法删除已保存会话：{error}", error=True)
        self.activity_list.clear()
        self._trace_items.clear()
        self._trace_presentations.clear()
        self._verification_guidance_item = None
        self._append_runtime("已开始新会话。")
        self._reset_status_metrics()

    def clear_all_saved_sessions(self) -> None:
        """经用户确认后删除 SessionStore 中的全部会话文件。"""
        if self._thread is not None:
            return
        answer = QMessageBox.question(
            self,
            "清除全部已保存会话",
            "将删除所有工作区的已保存会话。当前内存中的对话不会被清空。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._flush_session_saves()
            removed = self._session_store.clear_all()
        except OSError as error:
            self._append_runtime(f"无法清除已保存会话：{error}", error=True)
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
        self._style_trace_item(item, presentation.tone)
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()
        self._trace_items[tool_call.id] = item
        self._trace_presentations[tool_call.id] = presentation
        self.current_action_label.setText(f"当前正在做：{presentation.title}")
        self.steps_status.setText(f"步骤：{step} / {self.max_steps_spin.value()}")

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
        if presentation.preview:
            item.setText(
                f"步骤 {step}  ·  {action_title}  [{presentation.label}]\n"
                f"{presentation.preview}\n"
                f"✓ 已应用 — {presentation.summary}"
            )
        else:
            item.setText(
                f"步骤 {step}  ·  {action_title}  [{presentation.label}]\n"
                f"{action_summary}\n"
                f"结果：{presentation.title} — {presentation.summary}"
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
        self._style_trace_item(item, presentation.tone)
        tool_count = sum(1 for value in self._trace_items.values() if value is not None)
        self.tools_status.setText(f"工具调用：{tool_count}")
        if verification != self._last_verification:
            self._add_verification_trace(verification)
        self._set_verification(verification)
        self.current_action_label.setText(
            f"刚刚完成：{action_title} · {presentation.title}"
        )
        self.activity_list.scrollToBottom()

    def _on_llm_retry(self, retry: int, maximum: int) -> None:
        self._add_activity(
            "LLM 重试",
            "模型请求暂时失败，正在自动重试",
            f"正在进行第 {retry}/{maximum} 次重试；任务暂时不需要人工操作。",
            "为保护安全，这里不会显示请求正文和凭据。",
            "warning",
        )

    def _on_agent_completed(self, result: AgentResult) -> None:
        self._finalize_evidence(result=result)
        self._last_run_state = {
            "stop_reason": result.stop_reason,
            "steps": result.steps,
            "tool_calls": result.tool_calls,
            "verification_status": result.verification_status,
            "content": result.content,
        }
        if result.stop_reason == "completed":
            self._append_message(
                "AGENT FINAL RESPONSE",
                result.content,
                "#238636",
                markdown=True,
            )
            state = "Completed"
        elif result.stop_reason == "interrupted":
            self._append_runtime("任务已由用户中断。")
            state = "Ready"
        elif result.stop_reason == "verification_required":
            self._append_message(
                "AGENT UNVERIFIED DRAFT",
                result.content,
                "#d29922",
                markdown=True,
            )
            self._append_runtime(
                "智能体执行已经结束，但任务尚未完成：最新代码仍需要有效的验证证据。",
                error=True,
            )
            state = "Needs Verification"
            title, summary = STOP_REASON_TEXT[result.stop_reason]
            self._add_activity(
                result.stop_reason.upper(),
                title,
                summary,
                result.content,
                "warning",
            )
        else:
            self._append_runtime(
                f"智能体已停止：{STOP_REASON_LABELS.get(result.stop_reason, result.stop_reason)}\n"
                f"{result.content}",
                error=True,
            )
            state = "Failed"
            title, summary = STOP_REASON_TEXT.get(
                result.stop_reason,
                ("智能体已停止", result.content),
            )
            self._add_activity(
                "停止原因", title, summary, result.content, "error"
            )
        self.steps_status.setText(
            f"步骤：{result.steps} / {self.max_steps_spin.value()}"
        )
        self.tools_status.setText(f"工具调用：{result.tool_calls}")
        self._set_verification(result.verification_status)
        self._set_agent_state(state)
        self.current_action_label.setText(
            {
                "Completed": "当前状态：任务完成",
                "Ready": "当前状态：任务已停止",
                "Needs Verification": "当前状态：执行已结束，但任务尚未完成验证",
                "Failed": "当前状态：任务未完成，请查看最后一条说明",
            }.get(state, f"当前状态：{state}")
        )
        self._save_session()

    def _on_agent_failed(self, message: str) -> None:
        self._finalize_evidence(error=message)
        self._last_run_state = {
            "stop_reason": "worker_error",
            "steps": 0,
            "tool_calls": 0,
            "verification_status": self._last_verification,
            "content": message,
        }
        self._append_runtime(f"智能体后台任务失败：{message}", error=True)
        self._add_activity(
            "后台错误",
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
        self.approval_frame.hide()
        self._pending_approval_id = None
        self._pending_approval_request = None
        if self._close_pending:
            self._close_pending = False
            QTimer.singleShot(0, self.close)

    def _add_verification_trace(self, verification: str) -> None:
        self._last_verification = verification
        title, summary = {
            VERIFICATION_UNVERIFIED: (
                "代码已修改，等待验证",
                "文件写入成功不代表代码正确，智能体接下来需要运行测试、构建或语法检查。",
            ),
            VERIFICATION_FAILED: (
                "验证失败，继续修复",
                "最新测试或检查没有通过，智能体应根据错误结果继续定位问题。",
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
            "验证",
            title,
            summary,
            f"Runtime 验证状态：{VERIFICATION_TEXT.get(verification, verification)}",
            tone,
        )

    def _add_activity(
        self,
        label: str,
        title: str,
        summary: str,
        details: str,
        tone: str,
    ) -> QListWidgetItem:
        item = QListWidgetItem(f"{title}  [{label}]\n{summary}")
        item.setData(Qt.ItemDataRole.UserRole, details)
        item.setData(Qt.ItemDataRole.UserRole + 1, tone)
        self._style_trace_item(item, tone)
        self.activity_list.addItem(item)
        self.activity_list.scrollToBottom()
        return item

    def show_trace_details(self, item: QListWidgetItem) -> None:
        details = str(item.data(Qt.ItemDataRole.UserRole) or "没有可显示的详情。")
        dialog = QDialog(self)
        dialog.setWindowTitle("执行详情")
        dialog.resize(860, 620)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel(item.text().splitlines()[0] if item.text() else "执行详情")
        title.setObjectName("traceDetailsTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        has_diff = any(
            line.startswith(("+", "-", "@@")) for line in details.splitlines()
        )
        if has_diff:
            legend = QLabel(
                '<span style="color:#2da44e;">● 新增</span>&nbsp;&nbsp;&nbsp;'
                '<span style="color:#cf222e;">● 删除</span>&nbsp;&nbsp;&nbsp;'
                '<span style="color:#218bff;">● 修改位置</span>'
            )
            legend.setObjectName("traceDetailsLegend")
            layout.addWidget(legend)

        viewer = QTextBrowser()
        viewer.setObjectName("traceDetailsViewer")
        viewer.setReadOnly(True)
        viewer.setOpenExternalLinks(False)
        viewer.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        viewer.setHtml(trace_details_to_html(details, self._theme))
        layout.addWidget(viewer, 1)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
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
                "用户 · 任务指令"
                if role == "USER TASK"
                else "用户 · 运行中补充指令"
            )
            row = (
                '<td width="24%"></td>'
                f'<td class="userBubble"><div class="bubbleRole">{role_text}</div>'
                f'<div class="bubbleContent">{body}</div></td>'
            )
        elif role in {
            "AGENT FINAL RESPONSE",
            "AGENT UNVERIFIED DRAFT",
            "AGENT RESPONSE",
        }:
            role_text = {
                "AGENT FINAL RESPONSE": "智能体 · 最终回答",
                "AGENT UNVERIFIED DRAFT": "智能体 · 未验证草稿（不是完成结果）",
                "AGENT RESPONSE": "智能体 · 历史回答（完成状态未知）",
            }[role]
            row = (
                f'<td class="agentBubble"><div class="bubbleRole">{role_text}</div>'
                f'<div class="bubbleContent">{body}</div></td>'
                '<td width="20%"></td>'
            )
        else:
            role_text = escape(role)
            row = (
                '<td width="7%"></td>'
                f'<td class="runtimeBubble"><div class="bubbleRole">{role_text}</div>'
                f'<div class="bubbleContent">{body}</div></td>'
                '<td width="7%"></td>'
            )
        self.conversation_view.append(
            '<table width="100%" cellspacing="0" cellpadding="11">'
            f"<tr>{row}</tr></table>"
        )
        self.conversation_view.verticalScrollBar().setValue(
            self.conversation_view.verticalScrollBar().maximum()
        )

    def _append_runtime(self, content: str, *, error: bool = False) -> None:
        self._append_message("运行环境", content, "#f85149" if error else "#8b949e")

    def _set_running(self, running: bool) -> None:
        self.run_button.setEnabled(True)
        self.run_button.setText("发送补充指令" if running else "运行任务")
        self.stop_button.setEnabled(running)
        self.task_input.setEnabled(True)
        self.task_input.setPlaceholderText(
            "输入补充指令，它会在下一个步骤生效。"
            if running
            else "例如：运行项目测试，定位并修复失败的实现，然后验证结果。"
        )
        self.choose_workspace_button.setEnabled(not running)
        self.max_steps_spin.setEnabled(not running)
        self.new_session_button.setEnabled(not running)
        self.clear_button.setEnabled(not running)
        self.clear_saved_sessions_button.setEnabled(not running)
        self.safety_mode_combo.setEnabled(not running)
        self.replay_trace_button.setEnabled(not running)
        self.export_trace_button.setEnabled(
            not running and self._evidence_snapshot is not None
        )

    def _set_agent_state(self, state: str) -> None:
        self.agent_status.setText(f"智能体：{AGENT_STATE_TEXT.get(state, state)}")
        self.agent_status.setProperty("state", state)
        self.agent_status.style().unpolish(self.agent_status)
        self.agent_status.style().polish(self.agent_status)
        self.current_action_dot.setProperty("state", state)
        self.current_action_dot.style().unpolish(self.current_action_dot)
        self.current_action_dot.style().polish(self.current_action_dot)

    def _set_verification(self, status: str) -> None:
        text = VERIFICATION_TEXT.get(status, status)
        self.verification_status.setText(f"验证：{text}")
        self.verification_status.setProperty("verification", status)
        self.verification_status.style().unpolish(self.verification_status)
        self.verification_status.style().polish(self.verification_status)

    def _reset_status_metrics(self) -> None:
        self._set_agent_state("Ready")
        self.steps_status.setText(f"步骤：0 / {self.max_steps_spin.value()}")
        self.tools_status.setText("工具调用：0")
        self.elapsed_status.setText("耗时：0.0 秒")
        self._set_verification(VERIFICATION_NOT_REQUIRED)
        self.current_action_label.setText("当前状态：等待任务")

    def _update_elapsed(self) -> None:
        if self._started_at is None:
            return
        elapsed = time.monotonic() - self._started_at
        self.elapsed_status.setText(f"耗时：{elapsed:.1f} 秒")

    def _model_name(self) -> str:
        if self._settings is not None:
            return self._settings.model
        try:
            return Settings.from_env().model
        except ConfigurationError:
            return "未配置"

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
            self._last_run_state,
        )
        future.add_done_callback(self._session_save_completed)

    def _session_save_completed(self, future: Future[Path]) -> None:
        """从后台线程把保存异常安全转回 Qt 主线程。"""
        try:
            future.result()
        except (OSError, ValueError) as error:
            self.session_save_failed.emit(f"{type(error).__name__}: {error}")

    def _on_session_save_failed(self, message: str) -> None:
        self._append_runtime(f"会话保存失败：{message}", error=True)

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
            self._last_run_state = None
            self._reset_status_metrics()
            self._append_runtime(f"无法恢复已保存会话：{error}", error=True)
            return
        if snapshot is None:
            self._conversation = Conversation(SYSTEM_PROMPT)
            self._last_run_state = None
            self._reset_status_metrics()
            self.conversation_view.clear()
            self._append_runtime("已就绪。请描述要在当前工作区完成的编码任务。")
            return
        self._conversation = snapshot.to_conversation()
        self._last_run_state = snapshot.last_run
        self.conversation_view.clear()
        restored_message = "已恢复这个工作区最近保存的会话。"
        if snapshot.compacted:
            restored_message += " 较早的工具结果已按存储上限安全压缩。"
        self._append_runtime(restored_message)
        last_user_index = max(
            (
                index
                for index, message in enumerate(snapshot.messages)
                if message.get("role") == "user"
            ),
            default=-1,
        )
        last_stop_reason = (
            str(snapshot.last_run.get("stop_reason"))
            if snapshot.last_run is not None
            else ""
        )
        for index, message in enumerate(snapshot.messages):
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str) or not content:
                continue
            if role == "user":
                self._append_message("USER TASK", content, "#1f6feb")
            elif role == "assistant" and not message.get("tool_calls"):
                if (
                    snapshot.last_run is not None
                    and last_stop_reason != "completed"
                    and index > last_user_index
                ):
                    continue
                self._append_message(
                    (
                        "AGENT FINAL RESPONSE"
                        if last_stop_reason == "completed"
                        else "AGENT RESPONSE"
                    ),
                    content,
                    "#238636",
                    markdown=True,
                )
        if snapshot.last_run is not None and last_stop_reason != "completed":
            draft = snapshot.last_run.get("content")
            if isinstance(draft, str) and draft:
                self._append_message(
                    "AGENT UNVERIFIED DRAFT",
                    draft,
                    "#d29922",
                    markdown=True,
                )
            self._append_runtime(
                "上次执行已结束，但任务没有完成："
                f"{STOP_REASON_LABELS.get(last_stop_reason, last_stop_reason)}。",
                error=True,
            )
        if snapshot.last_run is not None:
            steps = int(snapshot.last_run.get("steps", 0))
            tool_calls = int(snapshot.last_run.get("tool_calls", 0))
            verification = str(
                snapshot.last_run.get(
                    "verification_status", VERIFICATION_NOT_REQUIRED
                )
            )
            restored_state = {
                "completed": "Completed",
                "interrupted": "Ready",
                "verification_required": "Needs Verification",
            }.get(last_stop_reason, "Failed")
            self.steps_status.setText(
                f"步骤：{steps} / {self.max_steps_spin.value()}"
            )
            self.tools_status.setText(f"工具调用：{tool_calls}")
            self._set_verification(verification)
            self._set_agent_state(restored_state)
        else:
            self._reset_status_metrics()

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
            self._style_trace_item(item, tone)
        if self._evidence_snapshot is not None:
            self._render_evidence_summary(self._evidence_snapshot)

    def _tone_color(self, tone: str) -> QColor:
        colors = LIGHT_TONE_COLORS if self._theme == "light" else TONE_COLORS
        return colors.get(tone, colors["normal"])

    def _tone_background(self, tone: str) -> QColor:
        colors = (
            LIGHT_TONE_BACKGROUNDS
            if self._theme == "light"
            else TONE_BACKGROUNDS
        )
        return colors.get(tone, colors["normal"])

    def _style_trace_item(self, item: QListWidgetItem, tone: str) -> None:
        """统一时间线卡片的文字与背景颜色。"""
        item.setForeground(self._tone_color(tone))
        item.setBackground(self._tone_background(tone))

    def _apply_style(self) -> None:
        palette = (
            {
                "background": "#eef2f7",
                "surface": "#ffffff",
                "surface_alt": "#f8fafc",
                "input": "#ffffff",
                "border": "#d7dee8",
                "strong_border": "#bcc7d6",
                "text": "#334155",
                "bright": "#172033",
                "muted": "#64748b",
                "disabled": "#9aa6b5",
                "button": "#f8fafc",
                "hover": "#edf2f7",
                "selected": "#e8f0ff",
                "accent": "#2563eb",
                "accent_soft": "#e8f0ff",
                "project": "#0284c7",
                "conversation": "#7c3aed",
                "activity": "#d97706",
                "success": "#16813a",
                "scroll": "#b8c2cf",
            }
            if self._theme == "light"
            else {
                "background": "#080c12",
                "surface": "#10161f",
                "surface_alt": "#151c26",
                "input": "#0c1119",
                "border": "#253041",
                "strong_border": "#35445a",
                "text": "#cbd5e1",
                "bright": "#f8fafc",
                "muted": "#8492a6",
                "disabled": "#526173",
                "button": "#18212d",
                "hover": "#213043",
                "selected": "#1c3150",
                "accent": "#60a5fa",
                "accent_soft": "#152844",
                "project": "#38bdf8",
                "conversation": "#a78bfa",
                "activity": "#f59e0b",
                "success": "#3fb950",
                "scroll": "#344156",
            }
        )
        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {palette['background']}; }}
            QWidget {{
                color: {palette['text']}; font-size: 13px;
                font-family: "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif;
            }}
            QFrame#topBar {{ background: {palette['surface']}; border: 1px solid {palette['border']}; border-radius: 10px; }}
            QFrame#panel {{ background: {palette['surface']}; border: 1px solid {palette['border']}; border-radius: 10px; }}
            QFrame#topDivider {{ color: {palette['border']}; background: {palette['border']}; border: 0; max-height: 1px; }}
            QFrame#panelAccent {{ border: 0; border-radius: 2px; }}
            QFrame#panelAccent[section="project"] {{ background: {palette['project']}; }}
            QFrame#panelAccent[section="conversation"] {{ background: {palette['conversation']}; }}
            QFrame#panelAccent[section="activity"] {{ background: {palette['activity']}; }}
            QFrame#taskComposer {{ background: {palette['surface_alt']}; border: 1px solid {palette['border']}; border-radius: 8px; }}
            QFrame#currentActionCard {{ background: {palette['accent_soft']}; border: 1px solid {palette['accent']}; border-radius: 8px; }}
            QFrame#evidenceCard {{ background: {palette['surface_alt']}; border: 1px solid {palette['border']}; border-radius: 8px; }}
            QFrame#approvalCard {{ background: {palette['selected']}; border: 1px solid {palette['accent']}; border-radius: 8px; }}
            QLabel#appMark {{ background: {palette['accent']}; color: white; border-radius: 9px; font-size: 13px; font-weight: 800; }}
            QLabel#appTitle {{ color: {palette['bright']}; font-size: 19px; font-weight: 750; }}
            QLabel#appSubtitle {{ color: {palette['muted']}; font-size: 11px; }}
            QLabel#fieldCaption {{ color: {palette['muted']}; font-size: 10px; font-weight: 650; }}
            QLabel#panelTitle {{ color: {palette['bright']}; font-size: 13px; font-weight: 700; }}
            QLabel#panelTitle[section="project"] {{ color: {palette['project']}; }}
            QLabel#panelTitle[section="conversation"] {{ color: {palette['conversation']}; }}
            QLabel#panelTitle[section="activity"] {{ color: {palette['activity']}; }}
            QLabel#composerTitle, QLabel#timelineTitle {{ color: {palette['bright']}; font-size: 12px; font-weight: 700; }}
            QLabel#currentActionDot {{ color: {palette['accent']}; font-size: 14px; }}
            QLabel#currentActionDot[state="Completed"] {{ color: {palette['success']}; }}
            QLabel#currentActionDot[state="Failed"] {{ color: #f85149; }}
            QLabel#currentActionDot[state="Needs Verification"] {{ color: {palette['activity']}; }}
            QLabel#currentActionDot[state="Stopping…"] {{ color: {palette['activity']}; }}
            QLabel#currentAction {{ color: {palette['bright']}; font-weight: 600; }}
            QLabel#evidenceTitle {{ color: {palette['accent']}; font-size: 12px; font-weight: 750; }}
            QLabel#evidenceBadge {{ color: {palette['accent']}; background: {palette['accent_soft']}; border: 1px solid {palette['accent']}; border-radius: 8px; padding: 2px 7px; font-size: 10px; }}
            QLabel#traceDetailsTitle {{ color: {palette['bright']}; font-size: 16px; font-weight: 700; padding: 2px 0 4px 0; }}
            QLabel#traceDetailsLegend {{ color: {palette['muted']}; font-size: 12px; padding-bottom: 4px; }}
            QLabel#workspaceLabel {{ color: {palette['bright']}; font-weight: 600; }}
            QLabel#modelLabel {{ color: {palette['accent']}; background: {palette['accent_soft']}; border-radius: 7px; padding: 3px 8px; font-weight: 650; }}
            QLabel#mutedText {{ color: {palette['muted']}; font-size: 11px; }}
            QPushButton, QToolButton {{ background: {palette['button']}; border: 1px solid {palette['border']}; border-radius: 6px; padding: 7px 12px; font-weight: 600; }}
            QPushButton:hover, QToolButton:hover {{ background: {palette['hover']}; border-color: {palette['strong_border']}; }}
            QPushButton:pressed, QToolButton:pressed {{ background: {palette['selected']}; }}
            QPushButton:disabled {{ color: {palette['disabled']}; background: {palette['surface']}; }}
            QPushButton[accent="true"] {{ background: #16813a; border-color: #24934b; color: white; font-weight: 700; }}
            QPushButton[accent="true"]:hover {{ background: #24934b; border-color: #35a85c; }}
            QTextBrowser, QPlainTextEdit, QTreeView, QListWidget, QSpinBox, QComboBox {{
                background: {palette['input']}; border: 1px solid {palette['border']}; border-radius: 7px; color: {palette['text']};
                selection-background-color: {palette['accent']};
            }}
            QTextBrowser:focus, QPlainTextEdit:focus, QTreeView:focus, QListWidget:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {palette['accent']}; }}
            QComboBox {{ min-height: 28px; padding: 0 8px; }}
            QComboBox::drop-down {{ border: 0; width: 24px; }}
            QComboBox QAbstractItemView {{ background: {palette['surface']}; border: 1px solid {palette['strong_border']}; selection-background-color: {palette['selected']}; padding: 4px; }}
            QSpinBox {{ min-height: 28px; padding-left: 7px; padding-right: 24px; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                subcontrol-origin: border; width: 22px; background: {palette['button']};
                border-left: 1px solid {palette['border']};
            }}
            QSpinBox::up-button {{ subcontrol-position: top right; border-bottom: 1px solid {palette['border']}; }}
            QSpinBox::down-button {{ subcontrol-position: bottom right; }}
            QPlainTextEdit {{ padding: 9px; font-family: Consolas, 'Cascadia Code', monospace; }}
            QPlainTextEdit#taskInput {{ background: {palette['input']}; border-color: {palette['strong_border']}; }}
            QTextBrowser#conversationView {{ border: 0; background: {palette['input']}; padding: 8px; }}
            QTextBrowser#traceDetailsViewer {{ padding: 0; border-radius: 7px; }}
            QListWidget#activityList {{ padding: 5px; background: {palette['input']}; }}
            QListWidget::item {{ border: 1px solid {palette['border']}; border-radius: 7px; padding: 10px; margin: 2px 1px; }}
            QListWidget::item:hover {{ border-color: {palette['strong_border']}; }}
            QListWidget::item:selected {{ background: {palette['selected']}; border-color: {palette['accent']}; }}
            QTreeView#projectTree {{ border: 0; background: {palette['input']}; padding: 4px; }}
            QTreeView::item {{ padding: 5px 3px; border-radius: 4px; }}
            QTreeView::item:hover {{ background: {palette['hover']}; }}
            QTreeView::item:selected {{ background: {palette['selected']}; color: {palette['bright']}; }}
            QCheckBox {{ spacing: 6px; color: {palette['muted']}; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; }}
            QStatusBar {{ background: {palette['surface']}; border-top: 1px solid {palette['border']}; min-height: 30px; }}
            QStatusBar QLabel {{ margin: 4px 3px; padding: 3px 9px; color: {palette['muted']}; background: {palette['surface_alt']}; border-radius: 6px; }}
            QLabel#agentStatus[state="Running"] {{ color: {palette['accent']}; }}
            QLabel#agentStatus[state="Completed"] {{ color: {palette['success']}; }}
            QLabel#agentStatus[state="Failed"], QLabel#verificationStatus[verification="failed"] {{ color: #f85149; }}
            QLabel#agentStatus[state="Needs Verification"], QLabel#verificationStatus[verification="unverified"] {{ color: {palette['activity']}; }}
            QLabel#verificationStatus[verification="verified"] {{ color: {palette['success']}; }}
            QSplitter::handle {{ background: {palette['background']}; width: 7px; }}
            QSplitter::handle:hover {{ background: {palette['accent_soft']}; }}
            QScrollBar:vertical {{ background: {palette['background']}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {palette['scroll']}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QToolTip {{ color: {palette['text']}; background: {palette['surface_alt']}; border: 1px solid {palette['strong_border']}; padding: 5px; }}
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
                "user_bg": "#2563eb",
                "user_text": "#ffffff",
                "user_border": "#1d4ed8",
                "agent_bg": "#f1f5f9",
                "agent_text": "#1e293b",
                "agent_border": "#cbd5e1",
                "runtime_bg": "#f8fafc",
                "runtime_text": "#64748b",
                "runtime_border": "#e2e8f0",
                "code_bg": "#e2e8f0",
            }
        else:
            colors = {
                "user_bg": "#1d4ed8",
                "user_text": "#ffffff",
                "user_border": "#3b82f6",
                "agent_bg": "#172033",
                "agent_text": "#e2e8f0",
                "agent_border": "#334155",
                "runtime_bg": "#111827",
                "runtime_text": "#94a3b8",
                "runtime_border": "#253041",
                "code_bg": "#0b111b",
            }
        self.conversation_view.document().setDefaultStyleSheet(
            f"""
            td.userBubble {{ background-color: {colors['user_bg']}; color: {colors['user_text']}; border: 1px solid {colors['user_border']}; }}
            td.agentBubble {{ background-color: {colors['agent_bg']}; color: {colors['agent_text']}; border: 1px solid {colors['agent_border']}; }}
            td.runtimeBubble {{ background-color: {colors['runtime_bg']}; color: {colors['runtime_text']}; border: 1px solid {colors['runtime_border']}; }}
            .bubbleRole {{ font-weight: 700; margin-bottom: 8px; font-size: 12px; }}
            .bubbleContent {{ line-height: 1.45; }}
            pre {{ background-color: {colors['code_bg']}; padding: 9px; white-space: pre-wrap; }}
            code {{ font-family: Consolas, 'Cascadia Code', monospace; }}
            h1, h2, h3 {{ margin-top: 8px; margin-bottom: 5px; }}
            p {{ margin-top: 4px; margin-bottom: 6px; }}
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
        raise ValueError("预览目标位于当前工作区之外。") from None
    name = target.name.casefold()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        raise ValueError("为保护敏感信息，不允许预览环境变量文件。")
    if target.suffix.casefold() not in PREVIEW_SUFFIXES:
        raise ValueError("文本预览暂不支持这种文件类型。")
    if target.stat().st_size > PREVIEW_MAX_BYTES:
        raise ValueError("文件过大，无法预览。")
    data = target.read_bytes()
    if b"\x00" in data:
        raise ValueError("二进制文件无法预览。")
    return data.decode("utf-8")
