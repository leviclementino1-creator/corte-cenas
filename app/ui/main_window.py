from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QThread, QTimer
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
from ..deps_check import cuda_available, cuda_known, gpu_name
from ..pipeline_types import PipelineResult
from . import quiet, theme
from .analyze_tab import AnalyzeTab
from .library_tab import LibraryTab
from .results_tab import ResultsTab
from .settings_dialog import SettingsDialog


def _device_tone() -> str:
    # Verde só quando a GPU está REALMENTE ativa: cor de estado não se gasta
    # com enfeite, senão para de significar alguma coisa.
    if not cuda_known():
        return "neutral"
    return "ok" if cuda_available() else "time"


def _device_badge_text() -> str:
    # Enquanto a detecção (que carrega o torch) não terminou lá no fundo, o
    # selo mostra "detectando" em vez de segurar a janela fechada.
    # O estado é um PONTO pintado na cor do token, não um emoji: emoji
    # colorido não aceita cor de tema e fica sujo a 150% de escala.
    if not cuda_known():
        texto = "detectando…"
    elif cuda_available():
        # "NVIDIA GeForce RTX 5080" -> "RTX 5080", pra o selo ficar estreito.
        name = gpu_name() or "GPU"
        for token in ("GeForce ", "NVIDIA ", "Nvidia "):
            name = name.replace(token, "")
        texto = name.strip()
    else:
        texto = "CPU (lento)"
    return theme.chip_dot(_device_tone(), texto)


def _device_badge_style() -> str:
    return theme.chip(_device_tone())


# A folha de estilo inteira vem de app/ui/theme.py — as decisões de cor,
# fonte e espaçamento moram lá, com o porquê de cada uma.
_DARK_QSS = theme.QSS


_VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".m2ts")


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("Corte Cenas — Analisador de Anime")
        # Mínimo real da janela: abaixo disto a Biblioteca (três colunas) e o
        # formulário de análise deixam de caber e a tela vira sopa. E o
        # tamanho que a pessoa escolheu VOLTA na próxima abertura — quem
        # arrasta a janela pro tamanho certo uma vez não deve fazer de novo.
        self.setMinimumSize(980, 640)
        self._settings = QSettings("CorteCenas", "CorteCenas")
        geo = self._settings.value("janela/geometria")
        if not (geo and self.restoreGeometry(geo)):
            self.resize(1180, 760)
        self.setStyleSheet(_DARK_QSS)
        # Drop an episode file anywhere on the window to load it in Analisar.
        self.setAcceptDrops(True)

        self.tabs = QTabWidget()
        self.analyze = AnalyzeTab(config, self)
        self.results = ResultsTab(config, self)

        self.tabs.addTab(self.analyze, "Analisar")
        self.tabs.addTab(self.results, "Resultados")
        # A Biblioteca é construída na primeira vez que o usuário abre a aba:
        # ela varre o banco e monta a árvore, e isso não pode entrar no custo
        # de abrir o app (que a v0.4.10 acabou de enxugar).
        self._library: LibraryTab | None = None
        self._lib_index = self.tabs.addTab(QWidget(), "Biblioteca")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Selo de GPU e Configurações vão no CANTO da barra de abas, não numa
        # faixa própria acima dela: uma linha inteira da janela só pra dois
        # controles é altura roubada da grade de cenas, que é o conteúdo que
        # importa. O QTabWidget tem encaixe pra isso (corner widget).
        canto = QWidget()
        top_bar = QHBoxLayout(canto)
        top_bar.setContentsMargins(0, 0, 8, 0)
        top_bar.setSpacing(8)

        self.device_label = QLabel(_device_badge_text())
        self.device_label.setTextFormat(Qt.TextFormat.RichText)
        self.device_label.setStyleSheet(_device_badge_style())
        self.device_label.setToolTip(
            "Verde: rodando em GPU NVIDIA (rápido).\n"
            "Amarelo: sem GPU detectada, roda em CPU (~20x mais lento)."
        )
        top_bar.addWidget(self.device_label)
        if not cuda_known():
            # A detecção roda em paralelo com a abertura; quando terminar, o
            # selo se corrige sozinho (sem bloquear a janela pra esperar).
            self._badge_timer = QTimer(self)
            self._badge_timer.setInterval(250)
            self._badge_timer.timeout.connect(self._refresh_device_badge)
            self._badge_timer.start()

        self.settings_btn = QPushButton("⚙  Configurações")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setStyleSheet(theme.button())
        self.settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(self.settings_btn)
        self.tabs.setCornerWidget(canto, Qt.Corner.TopRightCorner)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.tabs)

        self.analyze.pipeline_finished.connect(self._on_pipeline_finished)

        self.setCentralWidget(central)

    def _on_tab_changed(self, idx: int) -> None:
        if idx != self._lib_index:
            return
        if self._library is None:
            self._library = LibraryTab(self.config, self)
            placeholder = self.tabs.widget(self._lib_index)
            self.tabs.removeTab(self._lib_index)
            self.tabs.insertTab(self._lib_index, self._library, "Biblioteca")
            self.tabs.setCurrentIndex(self._lib_index)
            placeholder.deleteLater()
        else:
            self._library.reload()   # pode ter analisado algo desde a última vez

    def _refresh_device_badge(self) -> None:
        """Troca o '⏳ detectando…' pelo selo real assim que a detecção de
        GPU (que roda em paralelo com a abertura) termina."""
        if not cuda_known():
            return
        self.device_label.setText(_device_badge_text())
        self.device_label.setStyleSheet(_device_badge_style())
        timer = getattr(self, "_badge_timer", None)
        if timer is not None:
            timer.stop()

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
        try:
            self._settings.setValue("janela/geometria", self.saveGeometry())
        except Exception:
            pass
        running: list[QThread] = []
        for t in (
            getattr(self.analyze, "_thread", None),
            getattr(self.results, "_worker_thread", None),
        ):
            if isinstance(t, QThread) and t.isRunning():
                running.append(t)

        if running:
            reply = quiet.question(
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
