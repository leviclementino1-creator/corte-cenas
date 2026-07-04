from __future__ import annotations

from PySide6.QtCore import Qt
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

from ..config import Config


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

        # --- AI (NavyAI) ---
        ai_group = QGroupBox("AI Review (NavyAI / OpenAI-compatible)")
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
            "A API key fica salva em <code>~/AppData/Local/CorteCenas/config.json</code>. "
            "Usada pelos botões <b>Analisar com IA</b> (pipeline completa via Gemini) "
            "e <b>Revisar com AI</b> (desempate dos shots ambíguos)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#aaa;font-size:11px;")
        ai_form.addRow("", info)

        root.addWidget(ai_group)

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

    def _save(self) -> None:
        out_path = self.output_edit.text().strip()
        if out_path:
            self.config.output_dir = out_path
        self.config.navyai_api_key = self.key_edit.text().strip()
        self.config.navyai_model = self.model_edit.text().strip() or "gemini-2.0-flash"
        self.config.navyai_base_url = self.base_edit.text().strip() or "https://api.navy/v1"
        self.config.save()
        self.accept()
