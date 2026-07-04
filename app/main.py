from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .config import Config
from .deps_check import missing_optional_deps
from .ui.deps_dialog import MissingDepsDialog
from .ui.main_window import MainWindow
from .updater import check_and_offer_update


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Corte Cenas")
    cfg = Config.load()
    cfg.ensure_dirs()

    # Ping GitHub Releases. If a newer setup.exe exists, prompt + quit to update.
    check_and_offer_update()

    # Prompt to install YOLO/HF Hub if they're missing in the current Python.
    missing = missing_optional_deps()
    if missing:
        MissingDepsDialog(missing).exec()

    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
