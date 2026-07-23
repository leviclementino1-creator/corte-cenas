from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
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
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list, 1)

        # Preview no HOVER: passar o mouse numa cena cicla os 3 keyframes
        # dela (JPEGs que já estão no disco e no QPixmapCache) — sensação
        # de loop tipo YouTube SEM decodificar vídeo nenhum. Player de
        # verdade só no duplo clique. (A 1ª versão usava QMediaPlayer e o
        # backend de codec travou o app em produção — zero vídeo aqui.)
        self.list.setMouseTracking(True)
        self.list.itemEntered.connect(self._hover_start)
        self.list.viewport().installEventFilter(self)
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(450)
        self._hover_timer.timeout.connect(self._hover_tick)
        self._hover_item: QListWidgetItem | None = None
        self._hover_frames: list[QIcon] = []
        self._hover_idx = 0
        self._hover_icon0: QIcon | None = None

    def eventFilter(self, obj, event) -> bool:
        if obj is self.list.viewport() and event.type() == QEvent.Type.Leave:
            self._hover_stop()
        return super().eventFilter(obj, event)

    def _hover_start(self, item: QListWidgetItem) -> None:
        if item is self._hover_item:
            return
        self._hover_stop()
        row = item.data(Qt.ItemDataRole.UserRole)
        kf = (row or {}).get("keyframe")
        if not kf:
            return
        # keyframes/NNNN_K.jpg → irmãos NNNN_*.jpg = os frames do "loop"
        kf_path = self.episode_root / kf
        stem = kf_path.stem.rsplit("_", 1)[0]
        frames = sorted(kf_path.parent.glob(f"{stem}_*.jpg"))
        if len(frames) < 2:
            return
        icons = []
        for f in frames:
            pm = _thumbnail(str(f))
            if pm is not None:
                icons.append(QIcon(pm))
        if len(icons) < 2:
            return
        self._hover_item = item
        self._hover_icon0 = item.icon()
        self._hover_frames = icons
        self._hover_idx = 0
        self._hover_timer.start()

    def _hover_tick(self) -> None:
        if self._hover_item is None or not self._hover_frames:
            self._hover_stop()
            return
        self._hover_idx = (self._hover_idx + 1) % len(self._hover_frames)
        self._hover_item.setIcon(self._hover_frames[self._hover_idx])

    def _hover_stop(self) -> None:
        self._hover_timer.stop()
        if self._hover_item is not None and self._hover_icon0 is not None:
            try:
                self._hover_item.setIcon(self._hover_icon0)
            except RuntimeError:
                pass  # item já foi destruído junto com a lista
        self._hover_item = None
        self._hover_frames = []
        self._hover_icon0 = None

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
