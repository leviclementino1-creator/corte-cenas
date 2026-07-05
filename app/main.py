from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .config import Config
from .deps_check import cuda_available, ffmpeg_available, missing_optional_deps
from .ui.deps_dialog import FFmpegMissingDialog, MissingDepsDialog, NoGpuDialog
from .ui.main_window import MainWindow
from .updater import check_and_offer_update


def _load_app_icon() -> QIcon:
    """Load the multi-resolution app icon from app/assets/. Works both when
    running from source and when PyInstaller-bundled (sys._MEIPASS)."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "app" / "assets" / "icon.ico")
    candidates.append(Path(__file__).resolve().parent / "assets" / "icon.ico")
    for p in candidates:
        if p.exists():
            return QIcon(str(p))
    return QIcon()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Corte Cenas")
    app.setWindowIcon(_load_app_icon())
    cfg = Config.load()
    cfg.ensure_dirs()

    # Ping GitHub Releases. If a newer setup.exe exists, prompt + quit to update.
    check_and_offer_update()

    # Prompt to install YOLO/HF Hub if they're missing in the current Python.
    missing = missing_optional_deps()
    if missing:
        MissingDepsDialog(missing).exec()

    # Warn if FFmpeg is missing before user gets frustrated mid-analysis.
    if not ffmpeg_available():
        FFmpegMissingDialog().exec()

    # Warn about CPU-only mode (no NVIDIA GPU with CUDA). App still runs,
    # just ~20x slower. Once dismissed with "don't ask again", we stay quiet.
    if not cuda_available() and not cfg.gpu_warning_dismissed:
        dlg = NoGpuDialog()
        dlg.exec()
        if dlg.dont_ask_again:
            cfg.gpu_warning_dismissed = True
            cfg.save()

    win = MainWindow(cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
