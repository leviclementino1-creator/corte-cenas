from __future__ import annotations

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..deps_check import cuda_available, gpu_name
from ..pipeline_types import PipelineResult
from .analyze_tab import AnalyzeTab
from .results_tab import ResultsTab
from .settings_dialog import SettingsDialog


def _device_badge_text() -> str:
    if cuda_available():
        # Shorten "NVIDIA GeForce RTX 5080" -> "RTX 5080" so the badge stays
        # narrow. Fallback to "GPU" if the name doesn't fit the pattern.
        name = gpu_name() or "GPU"
        for token in ("GeForce ", "NVIDIA ", "Nvidia "):
            name = name.replace(token, "")
        return f"🟢  {name.strip()}"
    return "🟡  CPU (lento)"


def _device_badge_style() -> str:
    color = "#7FCC7F" if cuda_available() else "#DDB077"
    return (
        f"QLabel{{color:{color};background:#2b2d31;border:1px solid #3a3d43;"
        f"border-radius:4px;padding:5px 10px;font-size:12px;font-weight:600;}}"
    )


_DARK_QSS = """
QMainWindow, QWidget { background: #1e1f22; color: #e6e6e6; }
QGroupBox { border: 1px solid #2e3036; border-radius: 6px; margin-top: 10px; padding: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; color: #aaa; }
QLineEdit, QSpinBox, QListWidget { background: #2b2d31; border: 1px solid #3a3d43; border-radius: 4px; padding: 4px; }
QPushButton { background: #3a3d43; color: #eee; border: 1px solid #4b4f57; padding: 6px 10px; border-radius: 4px; }
QPushButton:hover { background: #4b4f57; }
QProgressBar { background: #2b2d31; border: 1px solid #3a3d43; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #4CAF50; border-radius: 4px; }
QTabBar::tab { background: #2b2d31; color: #ccc; padding: 8px 16px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
QTabBar::tab:selected { background: #1e1f22; color: #fff; border-bottom: 2px solid #4CAF50; }
QListWidget::item:selected { background: #3a5a3f; }
"""


_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".m2ts")


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("Corte Cenas — Analisador de Anime")
        self.resize(1100, 720)
        self.setStyleSheet(_DARK_QSS)
        # Drop an episode file anywhere on the window to load it in Analisar.
        self.setAcceptDrops(True)

        self.tabs = QTabWidget()
        self.analyze = AnalyzeTab(config, self)
        self.results = ResultsTab(config, self)

        self.tabs.addTab(self.analyze, "Analisar")
        self.tabs.addTab(self.results, "Resultados")

        # Top bar: GPU/CPU indicator on the left of the settings button so
        # the user always knows which mode they're in without opening Settings.
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 6, 8, 0)
        top_bar.addStretch(1)

        self.device_label = QLabel(_device_badge_text())
        self.device_label.setStyleSheet(_device_badge_style())
        self.device_label.setToolTip(
            "Verde: rodando em GPU NVIDIA (rápido).\n"
            "Amarelo: sem GPU detectada, roda em CPU (~20x mais lento)."
        )
        top_bar.addWidget(self.device_label)

        self.settings_btn = QPushButton("⚙  Configurações")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setStyleSheet(
            "QPushButton{"
            "background:#2b2d31;color:#ddd;border:1px solid #3a3d43;"
            "border-radius:4px;padding:5px 12px;font-size:12px;"
            "}"
            "QPushButton:hover{background:#3a3d43;color:#fff;}"
        )
        self.settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(self.settings_btn)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        root.addLayout(top_bar)
        root.addWidget(self.tabs)

        self.analyze.pipeline_finished.connect(self._on_pipeline_finished)

        self.setCentralWidget(central)

    @staticmethod
    def _video_from_mime(mime) -> str | None:
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if path.lower().endswith(_VIDEO_EXTS):
                return path
        return None

    def dragEnterEvent(self, event) -> None:
        if self._video_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        path = self._video_from_mime(event.mimeData())
        if not path:
            return
        event.acceptProposedAction()
        self.tabs.setCurrentWidget(self.analyze)
        self.analyze.set_video(path)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            # Sync the output-dir field in AnalyzeTab so the user sees the
            # updated value without restarting the app.
            try:
                self.analyze.output_edit.setText(self.config.output_dir)
            except Exception:
                pass
            # AI review button's enabled state depends on whether a key is set.
            try:
                self.results._refresh_char_buttons()
            except Exception:
                pass

    def _on_pipeline_finished(self, result: PipelineResult) -> None:
        self.results.display_result(result)
        self.tabs.setCurrentWidget(self.results)

    def closeEvent(self, event) -> None:
        """Stop any background workers before letting Qt destroy the window,
        so we don't get 'QThread: Destroyed while thread is still running'.
        """
        running: list[QThread] = []
        for t in (
            getattr(self.analyze, "_thread", None),
            getattr(self.results, "_worker_thread", None),
        ):
            if isinstance(t, QThread) and t.isRunning():
                running.append(t)

        if running:
            reply = QMessageBox.question(
                self,
                "Análise em andamento",
                "Tem uma análise rodando. Fechar mesmo assim?\n"
                "(O processamento vai ser interrompido; shots já cortados ficam salvos.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for t in running:
                t.quit()
                t.wait(3000)
                if t.isRunning():
                    t.terminate()
                    t.wait(1000)
        event.accept()
