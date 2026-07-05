from __future__ import annotations

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..pipeline_types import PipelineResult
from .analyze_tab import AnalyzeTab
from .results_tab import ResultsTab
from .settings_dialog import SettingsDialog


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


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("Corte Cenas — Analisador de Anime")
        self.resize(1100, 720)
        self.setStyleSheet(_DARK_QSS)

        self.tabs = QTabWidget()
        self.analyze = AnalyzeTab(config, self)
        self.results = ResultsTab(config, self)

        self.tabs.addTab(self.analyze, "Analisar")
        self.tabs.addTab(self.results, "Resultados")

        # Top bar: stretch on the left pushes the Settings button to the right.
        # Kept above (not inside) the tab widget so it doesn't collide with the
        # tab bar or the window title on any Qt style.
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(8, 6, 8, 0)
        top_bar.addStretch(1)

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
