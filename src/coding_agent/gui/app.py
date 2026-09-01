"""桌面 GUI 的参数解析与 QApplication 入口。"""

import argparse
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from coding_agent.agent import DEFAULT_MAX_STEPS, MIN_MAX_STEPS
from coding_agent.gui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    """启动 Mini Coding Agent 桌面应用。"""
    parser = argparse.ArgumentParser(description="Mini Coding Agent 图形界面")
    parser.add_argument("--workspace", default=".", help="初始工作区")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="最大步骤数")
    arguments = parser.parse_args(argv)
    workspace = Path(arguments.workspace).resolve()
    if not workspace.is_dir():
        parser.error(f"工作区不是有效目录：{workspace}")
    if arguments.max_steps < MIN_MAX_STEPS:
        parser.error(f"max-steps 必须至少为 {MIN_MAX_STEPS}")

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("Mini Coding Agent")
    window = MainWindow(workspace, max_steps=arguments.max_steps)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
