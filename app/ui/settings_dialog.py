from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import Config
from ..deps_check import cuda_available, gpu_name
from ..updater import check_and_offer_update


class SettingsDialog(QDialog):
    """Central settings panel. Today it holds the NavyAI / AI integration
    credentials; more groups can be added here (paths, cache, etc.)
    without burying them in the Analisar tab.
    """

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Configurações")
        # Fits comfortably on a 1366×768 laptop with room for the taskbar;
        # anything smaller gets scrolled via the scroll area.
        self.setMinimumSize(540, 420)
        self.resize(580, 640)
        self._build_ui()

    def _build_ui(self) -> None:
        # OUTER layout: scroll area on top (grows), fixed buttons on bottom.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # INNER container that holds all the option groups. Wrapped in a
        # QScrollArea so the dialog stays usable on small screens — the Save
        # button never gets pushed offscreen.
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # --- Output folder ---
        out_group = QGroupBox("Pasta de saída dos clipes")
        out_form = QFormLayout(out_group)

        self.output_edit = QLineEdit(self.config.output_dir)
        self.output_edit.setToolTip(
            "Onde ficam os shots cortados e as pastas by_character/by_pair "
            "de cada episódio analisado."
        )
        browse_btn = QPushButton("Escolher...")
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(self._pick_output_dir)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.output_edit, 1)
        row.addWidget(browse_btn)
        wrap = QWidget()
        wrap.setLayout(row)
        out_form.addRow("Saída:", wrap)

        info_out = QLabel(
            "Aqui dentro vão ser criadas subpastas por anime/episódio. "
            "Ex: <code>&lt;saída&gt;/Dr. Stone/S04E25/shots/</code>."
        )
        info_out.setWordWrap(True)
        info_out.setStyleSheet("color:#aaa;font-size:11px;")
        out_form.addRow("", info_out)

        root.addWidget(out_group)

        # --- Primary AI: NavyAI ---
        ai_group = QGroupBox("AI principal (NavyAI / OpenAI-compatible)")
        ai_form = QFormLayout(ai_group)

        self.key_edit = QLineEdit(self.config.navyai_api_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-navy-...")
        show_key = QPushButton("Mostrar")
        show_key.setCheckable(True)
        show_key.setFixedWidth(80)
        show_key.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(show_key)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)
        ai_form.addRow("API key:", key_wrap)

        self.model_edit = QLineEdit(self.config.navyai_model or "gemini-2.5-flash")
        ai_form.addRow("Modelo:", self.model_edit)

        self.base_edit = QLineEdit(self.config.navyai_base_url or "https://api.navy/v1")
        ai_form.addRow("Endpoint:", self.base_edit)

        info = QLabel(
            "Usado por padrão pelos botões <b>Analisar com IA</b>. "
            "Se falhar (rate-limit, quota, 5xx), cai automaticamente no Gemini abaixo."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#aaa;font-size:11px;")
        ai_form.addRow("", info)

        root.addWidget(ai_group)

        # --- Fallback AI: Gemini direto ---
        gem_group = QGroupBox("AI fallback (Gemini direto, plano free)")
        gem_form = QFormLayout(gem_group)

        self.gem_key_edit = QLineEdit(self.config.gemini_api_key)
        self.gem_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.gem_key_edit.setPlaceholderText("AIza...")
        show_gem_key = QPushButton("Mostrar")
        show_gem_key.setCheckable(True)
        show_gem_key.setFixedWidth(80)
        show_gem_key.toggled.connect(
            lambda on: self.gem_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        gem_row = QHBoxLayout()
        gem_row.setContentsMargins(0, 0, 0, 0)
        gem_row.addWidget(self.gem_key_edit, 1)
        gem_row.addWidget(show_gem_key)
        gem_wrap = QWidget()
        gem_wrap.setLayout(gem_row)
        gem_form.addRow("API key:", gem_wrap)

        self.gem_model_edit = QLineEdit(self.config.gemini_model or "gemini-2.5-flash")
        gem_form.addRow("Modelo:", self.gem_model_edit)

        gem_info = QLabel(
            "Pega a key gratuita em <a href='https://aistudio.google.com/apikey' style='color:#7FCC7F'>"
            "aistudio.google.com/apikey</a>. "
            "Se as duas keys estiverem preenchidas, NavyAI é usada primeiro e o Gemini "
            "só entra em ação se ela falhar. Se só uma tiver, ela é usada sozinha. "
            "As keys ficam salvas em <code>~/AppData/Local/CorteCenas/config.json</code>."
        )
        gem_info.setOpenExternalLinks(True)
        gem_info.setWordWrap(True)
        gem_info.setStyleSheet("color:#aaa;font-size:11px;")
        gem_form.addRow("", gem_info)

        root.addWidget(gem_group)

        # --- App / Atualizações ---
        app_group = QGroupBox("Sobre / Atualizações")
        app_layout = QVBoxLayout(app_group)

        version_row = QHBoxLayout()
        version_label = QLabel(
            f"Corte Cenas <b>v{__version__}</b> — "
            "<a href='https://github.com/leviclementino1-creator/corte-cenas/releases' "
            "style='color:#7FCC7F'>ver histórico de versões</a>"
        )
        version_label.setOpenExternalLinks(True)
        version_row.addWidget(version_label)
        version_row.addStretch(1)
        app_layout.addLayout(version_row)

        # GPU / device status
        if cuda_available():
            gpu_html = f"GPU: <span style='color:#7FCC7F'>{gpu_name() or 'CUDA'}</span>"
        else:
            gpu_html = "GPU: <span style='color:#DDB077'>não detectada — rodando em CPU (~20x mais lento)</span>"
        gpu_label = QLabel(gpu_html)
        gpu_label.setStyleSheet("font-size:11px;")
        app_layout.addWidget(gpu_label)

        update_row = QHBoxLayout()
        self.update_btn = QPushButton("🔄  Verificar atualizações agora")
        self.update_btn.clicked.connect(self._check_updates)
        update_row.addWidget(self.update_btn)
        logs_btn = QPushButton("📂  Abrir pasta de logs")
        logs_btn.clicked.connect(self._open_logs)
        update_row.addWidget(logs_btn)
        update_row.addStretch(1)
        app_layout.addLayout(update_row)

        upd_info = QLabel(
            "O app já verifica atualizações ao abrir. Deu algum problema numa análise? "
            "Abra a pasta de logs e mande o arquivo <code>app.log</code> pra quem "
            "te passou o app — ele registra tudo que aconteceu na última execução."
        )
        upd_info.setWordWrap(True)
        upd_info.setStyleSheet("color:#aaa;font-size:11px;")
        app_layout.addWidget(upd_info)

        root.addWidget(app_group)
        root.addStretch(1)  # push groups up; empty space below scrolls last

        # Scroll wrapper around the inner content.
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        # A subtle separator line so the fixed button bar reads as its own strip.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#3a3d43;")
        outer.addWidget(sep)

        # Fixed button bar at the bottom of the dialog — never scrolls away.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 8, 12, 12)
        btn_row.addWidget(btns)
        outer.addLayout(btn_row)

    def _pick_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _open_logs(self) -> None:
        from ..applog import log_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def _check_updates(self) -> None:
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Verificando...")
        try:
            check_and_offer_update(parent=self, verbose=True)
        finally:
            self.update_btn.setEnabled(True)
            self.update_btn.setText("🔄  Verificar atualizações agora")

    def _save(self) -> None:
        out_path = self.output_edit.text().strip()
        if out_path:
            self.config.output_dir = out_path
        self.config.navyai_api_key = self.key_edit.text().strip()
        self.config.navyai_model = self.model_edit.text().strip() or "gemini-2.5-flash"
        self.config.navyai_base_url = self.base_edit.text().strip() or "https://api.navy/v1"
        self.config.gemini_api_key = self.gem_key_edit.text().strip()
        self.config.gemini_model = self.gem_model_edit.text().strip() or "gemini-2.5-flash"
        self.config.save()
        self.accept()
