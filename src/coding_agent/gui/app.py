"""桌面 GUI 的参数解析与 QApplication 入口。"""

import argparse
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from coding_agent.agent import DEFAULT_MAX_STEPS, MAX_MAX_STEPS, MIN_MAX_STEPS
from coding_agent.gui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    """启动 Mini Coding Agent 桌面应用。"""
    parser = argparse.ArgumentParser(description="Mini Coding Agent GUI")
    parser.add_argument("--workspace", default=".", help="initial workspace")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    arguments = parser.parse_args(argv)
    workspace = Path(arguments.workspace).resolve()
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    if not MIN_MAX_STEPS <= arguments.max_steps <= MAX_MAX_STEPS:
        parser.error(
            f"max-steps must be between {MIN_MAX_STEPS} and {MAX_MAX_STEPS}"
        )

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("Mini Coding Agent")
    window = MainWindow(workspace, max_steps=arguments.max_steps)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
