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
from .updater import check_and_offer_update, fetch_release


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

    # Splash "estilo After Effects": fica na tela DURANTE todo o carregamento
    # lento (rede + torch), com o status trocando embaixo do ícone. A janela
    # principal só aparece quando está pronta de verdade — e os diálogos que
    # precisam do usuário (update, deps, GPU) vêm depois, por cima dela.
    splash: QSplashScreen | None = None
    pixmap = _load_splash_pixmap()
    if pixmap is not None:
        splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
        splash.show()

    def status(text: str) -> None:
        if splash is not None:
            splash.showMessage(
                f"Corte Cenas v{__version__}\n{text}",
                Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
                Qt.GlobalColor.white,
            )
        app.processEvents()

    status("Carregando…")
    cfg = Config.load()
    cfg.ensure_dirs()
    get_logger().info(
        "Config: output=%s | cache=%s | models=%s",
        cfg.output_dir, cfg.cache_path, cfg.models_path,
    )

    # Trabalho lento e SILENCIOSO debaixo do splash (nenhum diálogo aqui,
    # senão o splash stay-on-top cobriria ele):
    status("Verificando atualizações…")
    release = fetch_release()               # rede, ~0.5-5s

    status("Verificando dependências…")
    missing = missing_optional_deps()
    ffmpeg_ok = ffmpeg_available()

    status("Detectando GPU…")
    has_cuda = cuda_available()             # importa torch: ~5s no cold start

    status("Abrindo…")
    win = MainWindow(cfg)                   # rápido: torch já está em memória
    win.show()
    if splash is not None:
        splash.finish(win)
    # O usuário pode ter focado outra janela durante o load — sem isto o
    # Windows nega o primeiro plano e o app nasce atrás de tudo.
    win.raise_()
    win.activateWindow()

    # Agora sim os diálogos interativos, por cima da janela visível:
    check_and_offer_update(parent=win, release=release)

    if missing:
        MissingDepsDialog(missing).exec()

    if not ffmpeg_ok:
        FFmpegMissingDialog().exec()

    # Warn about CPU-only mode (no NVIDIA GPU with CUDA). App still runs,
    # just ~20x slower. Once dismissed with "don't ask again", we stay quiet.
    if not has_cuda and not cfg.gpu_warning_dismissed:
        dlg = NoGpuDialog()
        dlg.exec()
        if dlg.dont_ask_again:
            cfg.gpu_warning_dismissed = True
            cfg.save()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
