from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..deps_check import OPTIONAL_DEPS, install_with_pip


class _InstallWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, packages: list[str]) -> None:
        super().__init__()
        self.packages = packages

    def run(self) -> None:
        ok, output = install_with_pip(self.packages)
        self.finished.emit(ok, output)


class MissingDepsDialog(QDialog):
    """Shown at startup when optional deps are missing. Lets the user
    install with one click using the current Python's pip.
    """

    def __init__(self, missing: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.missing = missing
        self.setWindowTitle("Dependências opcionais faltando")
        self.setMinimumWidth(580)
        self._thread: QThread | None = None
        self._worker: _InstallWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        lines = [
            "O app tá funcionando mas com fallback pior porque alguns pacotes opcionais",
            "não estão instalados neste Python.",
            "",
            f"Python usado: <code>{sys.executable}</code>",
            "",
            "<b>Faltando:</b>",
        ]
        for dep in self.missing:
            reason = OPTIONAL_DEPS.get(dep, "")
            lines.append(f"• <b>{dep}</b> — {reason}")
        lines.append("")
        lines.append(
            "Clique <b>Instalar agora</b> pra rodar <code>pip install --user</code> "
            "automaticamente neste Python. Depois reinicie o app."
        )

        hdr = QLabel("<br>".join(lines))
        hdr.setWordWrap(True)
        hdr.setTextFormat(1)   # Qt.RichText
        root.addWidget(hdr)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setVisible(False)
        self.output.setMaximumBlockCount(500)
        self.output.setStyleSheet("font-family:Consolas,monospace;font-size:10px;")
        root.addWidget(self.output)

        buttons = QDialogButtonBox()
        self.install_btn = QPushButton("Instalar agora")
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self._start_install)
        buttons.addButton(self.install_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self.skip_btn = QPushButton("Continuar sem (fallback)")
        self.skip_btn.clicked.connect(self.reject)
        buttons.addButton(self.skip_btn, QDialogButtonBox.ButtonRole.RejectRole)
        root.addWidget(buttons)

    def _start_install(self) -> None:
        self.install_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.output.setVisible(True)
        self.output.setPlainText(f"Rodando: {sys.executable} -m pip install --user {' '.join(self.missing)}\n")

        self._thread = QThread(self)
        self._worker = _InstallWorker(self.missing)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_finished(self, ok: bool, output: str) -> None:
        self.progress.setVisible(False)
        self.output.appendPlainText(output)
        if ok:
            self.output.appendPlainText(
                "\n✓ Instalação concluída. Feche e reabra o app pra carregar os pacotes novos."
            )
            self.install_btn.setText("Concluído — fechar")
            self.install_btn.setEnabled(True)
            self.install_btn.clicked.disconnect()
            self.install_btn.clicked.connect(self.accept)
        else:
            self.install_btn.setText("Tentar de novo")
            self.install_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)
            self.output.appendPlainText(
                "\n✗ Falhou. Você pode tentar manualmente no PowerShell:"
                f"\n   {sys.executable} -m pip install --user {' '.join(self.missing)}"
            )

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
