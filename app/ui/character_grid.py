from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QImageReader, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..storage.db import Database

_THUMB = QSize(192, 108)
_CACHE_SIZED = False


def _ensure_cache_size() -> None:
    """Miniaturas de keyframe cabem folgado em 64 MB (~80 KB cada depois de
    reduzidas). O padrão do Qt (10 MB) expulsava as antigas no meio de uma
    pasta grande, refazendo o trabalho a cada recarga da grade. Chamado no
    primeiro ShotGrid (com o QApplication já vivo, não no import)."""
    global _CACHE_SIZED
    if not _CACHE_SIZED:
        _CACHE_SIZED = True
        QPixmapCache.setCacheLimit(64 * 1024)  # em KB


def _thumbnail(path: Path) -> QPixmap | None:
    """Miniatura de um keyframe, com cache.

    Duas otimizações contra a 'travadinha' ao recarregar a grade (que
    acontece a cada remover/mover/aprovar):
    - QImageReader.setScaledSize: o JPEG é decodificado JÁ pequeno (o formato
      permite decodificar em resolução reduzida) em vez de abrir o quadro
      1080p inteiro pra depois encolher;
    - QPixmapCache: cada keyframe vira miniatura UMA vez por sessão — as
      recargas seguintes só repovoam a grade com pixmaps prontos.
    """
    key = f"cc_thumb:{path}"
    pix = QPixmapCache.find(key)
    if pix is not None and not pix.isNull():
        return pix
    reader = QImageReader(str(path))
    size = reader.size()
    if size.isValid():
        scaled = size.scaled(_THUMB, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(scaled)
    img = reader.read()
    if img.isNull():
        return None
    pix = QPixmap.fromImage(img)
    QPixmapCache.insert(key, pix)
    return pix


class ShotGrid(QWidget):
    """Thumbnail grid of shots for one character.

    Emits actions that let the user clean up the current folder without
    re-running the pipeline: remove a wrongly-assigned shot, move it to
    another character, or approve it as correct (stored in the DB).
    """

    shot_activated = Signal(dict)
    # action_name in {"remove", "move", "approve"}, plus the SELECTED shot
    # rows (1..N — Ctrl/Shift/laço selecionam vários de uma vez).
    shot_action = Signal(str, list)
    # Seleção mudou: a linha do shot selecionado ({} quando nada) — é o que
    # alimenta o preview em loop na aba Resultados.
    shot_selected = Signal(dict)

    def __init__(self, episode_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _ensure_cache_size()
        self.episode_root = episode_root
        self.character_name: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color:#bbb;")
        layout.addWidget(self.info_label)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(192, 108))
        self.list.setGridSize(QSize(210, 150))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        # Extended = Ctrl+clique adiciona, Shift+clique estende, arrastar no
        # vazio desenha laço — as ações do botão direito valem pra todos.
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setSpacing(6)
        self.list.itemDoubleClicked.connect(self._on_activate)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list, 1)

    def _on_selection(self) -> None:
        items = self.list.selectedItems()
        data = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self.shot_selected.emit(data or {})

    def load_for_character(self, shots: list[dict], character_name: str) -> None:
        self.list.clear()
        self.character_name = character_name
        self.info_label.setText(
            f"{character_name}: {len(shots)} shots · "
            f"confiança média {self._mean([s['confidence'] for s in shots]):.2f}"
        )
        for row in shots:
            icon = self._icon_for(row.get("keyframe"))
            text = f"#{row['idx']:04d}  ({row['confidence']:.2f})"
            it = QListWidgetItem(icon, text)
            it.setData(Qt.ItemDataRole.UserRole, row)
            it.setToolTip(
                f"Shot {row['idx']:04d}\n"
                f"{row['start']:.2f}s → {row['end']:.2f}s  ({row['duration']:.2f}s)\n"
                f"confiança: {row['confidence']:.3f}"
            )
            self.list.addItem(it)

    def _icon_for(self, rel: str | None) -> QIcon:
        if not rel:
            return QIcon()
        p = self.episode_root / rel
        if not p.exists():
            return QIcon()
        pix = _thumbnail(p)
        return QIcon(pix) if pix is not None else QIcon()

    @staticmethod
    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    def _on_activate(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.shot_activated.emit(data)

    def _show_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        # Right-click on an unselected thumb targets just it (and selects it,
        # like the Explorer); on a selected one, the action hits the whole
        # selection.
        if not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        rows = [
            it.data(Qt.ItemDataRole.UserRole)
            for it in self.list.selectedItems()
            if it.data(Qt.ItemDataRole.UserRole)
        ]
        if not rows:
            return

        n = len(rows)
        suffix = f" ({n} shots)" if n > 1 else ""
        pending = [r for r in rows if r.get("approved") != 1]

        menu = QMenu(self)
        if not pending:
            approve_label = "✓ Aprovado" if n == 1 else f"✓ Aprovados ({n})"
        else:
            approve_label = f"Aprovar (marcar correto){suffix}"
        act_approve = QAction(approve_label, self)
        act_approve.setEnabled(bool(pending))
        act_approve.triggered.connect(lambda: self.shot_action.emit("approve", pending))

        act_remove = QAction(f"Remover dessa pasta{suffix}", self)
        act_remove.triggered.connect(lambda: self.shot_action.emit("remove", rows))

        act_move = QAction(f"Mover pra outro personagem...{suffix}", self)
        act_move.triggered.connect(lambda: self.shot_action.emit("move", rows))

        menu.addAction(act_approve)
        menu.addSeparator()
        menu.addAction(act_remove)
        menu.addAction(act_move)
        menu.exec(self.list.mapToGlobal(pos))
