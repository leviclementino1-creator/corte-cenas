from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from . import __version__
from .applog import get_logger, setup as setup_logging
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


def _load_splash_pixmap() -> QPixmap | None:
    """Return the 256px icon variant scaled for the splash screen, or None."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "app" / "assets" / "icon_256.png")
    candidates.append(Path(__file__).resolve().parent / "assets" / "icon_256.png")
    for p in candidates:
        if p.exists():
            pm = QPixmap(str(p))
            if not pm.isNull():
                return pm
    return None


def main() -> int:
    setup_logging()  # no-op if run.py already did it
    app = QApplication(sys.argv)
    app.setApplicationName("Corte Cenas")
    app.setWindowIcon(_load_app_icon())

    # Splash screen — hides while we import the heavy modules that Qt still
    # needs (ui.main_window pulls in a bunch). Kept short since v0.1.6's
    # lazy-imports keep the whole path under ~1s cold.
    splash: QSplashScreen | None = None
    pixmap = _load_splash_pixmap()
    if pixmap is not None:
        splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
        splash.showMessage(
            f"Corte Cenas v{__version__}\nCarregando…",
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            Qt.GlobalColor.white,
        )
        splash.show()
        app.processEvents()

    cfg = Config.load()
    cfg.ensure_dirs()
    get_logger().info(
        "Config: output=%s | cache=%s | models=%s",
        cfg.output_dir, cfg.cache_path, cfg.models_path,
    )

    # Show the window FIRST, then run the slow startup checks (network ping
    # for updates, torch import for the GPU check). When these ran before
    # show(), the multi-second gap let the user focus another window and
    # Windows then denied us the foreground — the app looked "minimized".
    win = MainWindow(cfg)
    win.show()
    if splash is not None:
        splash.finish(win)
    win.raise_()
    win.activateWindow()

    # Ping GitHub Releases. If a newer setup.exe exists, prompt + quit to update.
    check_and_offer_update(parent=win)

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

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
