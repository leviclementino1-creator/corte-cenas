from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import Config
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
        self.setMinimumWidth(520)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
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

        self.model_edit = QLineEdit(self.config.navyai_model or "gemini-2.0-flash")
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

        self.gem_model_edit = QLineEdit(self.config.gemini_model or "gemini-2.0-flash")
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

        update_row = QHBoxLayout()
        self.update_btn = QPushButton("🔄  Verificar atualizações agora")
        self.update_btn.clicked.connect(self._check_updates)
        update_row.addWidget(self.update_btn)
        update_row.addStretch(1)
        app_layout.addLayout(update_row)

        upd_info = QLabel(
            "O app já verifica automaticamente ao abrir. Clique aqui pra checar "
            "manualmente sem reiniciar."
        )
        upd_info.setWordWrap(True)
        upd_info.setStyleSheet("color:#aaa;font-size:11px;")
        app_layout.addWidget(upd_info)

        root.addWidget(app_group)

        # --- buttons ---
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _pick_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

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
        self.config.navyai_model = self.model_edit.text().strip() or "gemini-2.0-flash"
        self.config.navyai_base_url = self.base_edit.text().strip() or "https://api.navy/v1"
        self.config.gemini_api_key = self.gem_key_edit.text().strip()
        self.config.gemini_model = self.gem_model_edit.text().strip() or "gemini-2.0-flash"
        self.config.save()
        self.accept()
