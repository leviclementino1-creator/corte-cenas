"""Tela de batismo do Modo Descoberta.

Mostra cada grupo de rostos descoberto (thumbnails + contagem) com um campo
de nome. Regras simples, explicadas no topo: vazio = ignorar o grupo; o
mesmo nome em dois grupos = fusão (acontece quando o clustering divide um
personagem entre "de frente" e "de perfil").
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..pipeline_types import DiscoveryResult
from . import quiet


_THUMB = 84


class DiscoveryNamingDialog(QDialog):
    def __init__(self, result: DiscoveryResult, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result = result
        self._edits: dict[int, QComboBox] = {}
        self.setWindowTitle("Modo Descoberta — quem é quem?")
        self.setMinimumSize(680, 480)
        self.resize(760, 640)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QLabel(
            f"<b>{len(self.result.groups)} personagens encontrados</b> em "
            f"{self.result.total_faces} rostos de {len(self.result.shots)} shots.<br>"
            "Dê nome a quem você quer organizar — <b>vazio = ignorar</b>. "
            "Se o mesmo personagem aparecer em dois grupos (de frente / de perfil), "
            "use o <b>mesmo nome</b> nos dois que o app funde.<br>"
            "Os rostos nomeados viram referências: os próximos episódios desse "
            "anime já saem no modo automático."
        )
        header.setWordWrap(True)
        header.setStyleSheet("color:#ccc;")
        root.addWidget(header)

        for n, g in enumerate(self.result.groups, 1):
            row_box = QFrame()
            row_box.setStyleSheet(
                "QFrame{background:#26282c;border:1px solid #33363b;border-radius:6px;}"
            )
            row = QVBoxLayout(row_box)
            row.setContentsMargins(10, 8, 10, 8)
            row.setSpacing(6)

            thumbs = QHBoxLayout()
            thumbs.setSpacing(4)
            for jpg in g.thumbs_jpg[:5]:
                pm = QPixmap()
                pm.loadFromData(jpg)
                lab = QLabel()
                lab.setPixmap(pm.scaled(
                    _THUMB, _THUMB,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                lab.setFixedSize(_THUMB, _THUMB)
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lab.setStyleSheet("QLabel{background:#1a1b1e;border-radius:4px;}")
                thumbs.addWidget(lab)
            thumbs.addStretch(1)
            row.addLayout(thumbs)

            name_row = QHBoxLayout()
            info_txt = f"{g.n_shots} shots · {g.n_faces} rostos"
            if g.suggested_name:
                info_txt += f" · parece {int(g.suggested_sim * 100)}% com o sugerido"
            info = QLabel(info_txt)
            info.setStyleSheet("color:#999;font-size:11px;border:none;background:transparent;")
            # Editável + dropdown: com anime conhecido, o elenco oficial vem
            # como lista — escolher em vez de digitar. Sem anime, texto livre.
            edit = QComboBox()
            edit.setEditable(True)
            edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            edit.addItem("")  # opção "ignorar"
            if self.result.roster:
                edit.addItems(self.result.roster)
            if edit.lineEdit() is not None:
                edit.lineEdit().setPlaceholderText(
                    f"Nome do personagem {n} (vazio = ignorar)"
                )
            if g.suggested_name:
                # O app já reconheceu o grupo — o usuário só confirma
                # (ou corrige/apaga).
                edit.setCurrentText(g.suggested_name)
            else:
                edit.setCurrentIndex(0)
            self._edits[g.key] = edit
            name_row.addWidget(edit, 1)
            name_row.addWidget(info)
            row.addLayout(name_row)

            root.addWidget(row_box)

        root.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#3a3d43;")
        outer.addWidget(sep)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 8, 12, 12)
        btn_row.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("Salvar personagens")
        confirm.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;"
            "padding:8px 18px;border-radius:6px;}"
            "QPushButton:hover{background:#5CBF60;}"
        )
        confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm.clicked.connect(self._confirm)
        btn_row.addWidget(cancel)
        btn_row.addWidget(confirm)
        outer.addLayout(btn_row)

    def _confirm(self) -> None:
        if not any(e.currentText().strip() for e in self._edits.values()):
            quiet.information(
                self, "Nenhum nome",
                "Dê nome a pelo menos um personagem — os grupos sem nome "
                "são descartados."
            )
            return
        self.accept()

    def names(self) -> dict[int, str]:
        return {key: e.currentText().strip() for key, e in self._edits.items()}
