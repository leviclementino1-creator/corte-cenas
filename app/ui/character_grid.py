from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QAction, QIcon, QImage, QImageReader, QPixmap, QPixmapCache
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


class _StripBridge(QObject):
    """Ponte thread→GUI: o job roda no pool, o QIcon nasce na thread certa."""
    ready = Signal(int, list)   # (shot_id, list[QImage])


class _StripJob(QRunnable):
    """Extrai ~8 frames espaçados do CLIPE da cena com cv2 (sem processo
    ffmpeg, sem backend de vídeo do Qt) pra alimentar o scrub do preview.
    Clipes de cena são pequenos (1-10s) — isso custa ~100-200ms uma vez e
    fica cacheado em memória pela grade."""

    N_FRAMES = 8

    def __init__(self, shot_id: int, clip_path: str, bridge: _StripBridge) -> None:
        super().__init__()
        self.shot_id = shot_id
        self.clip_path = clip_path
        self.bridge = bridge
        self.setAutoDelete(True)

    def run(self) -> None:
        images: list[QImage] = []
        try:
            import cv2
            cap = cv2.VideoCapture(self.clip_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total > 0:
                for k in range(self.N_FRAMES):
                    pos = int(total * (k + 0.5) / self.N_FRAMES)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue
                    h, w = frame.shape[:2]
                    scale = _THUMB.width() / max(w, 1)
                    if scale < 1.0:
                        frame = cv2.resize(
                            frame, (int(w * scale), int(h * scale))
                        )
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    hh, ww = rgb.shape[:2]
                    img = QImage(
                        rgb.data, ww, hh, 3 * ww, QImage.Format.Format_RGB888
                    ).copy()   # .copy(): o buffer numpy morre com o job
                    images.append(img)
            cap.release()
        except Exception:
            images = []
        self.bridge.ready.emit(self.shot_id, images)


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

        # Preview SCRUB estilo YouTube: a POSIÇÃO do mouse dentro da
        # miniatura escolhe o momento da cena (esquerda = começo, direita =
        # fim). Nos primeiros ms usa os 3 keyframes do disco; uma tira de
        # frames extra é extraída do clipe em background (cv2, sem processo
        # ffmpeg) e refina o scrub quando fica pronta. Zero backend de
        # vídeo do Qt — a 1ª versão usava QMediaPlayer e travou o app.
        self.list.setMouseTracking(True)
        self.list.viewport().installEventFilter(self)
        self._hover_item: QListWidgetItem | None = None
        self._hover_frames: list[QIcon] = []
        self._hover_icon0: QIcon | None = None
        self._hover_idx = -1
        self._strips: dict[int, list[QIcon]] = {}   # shot_id -> tira pronta
        self._strip_pending: set[int] = set()
        self._strip_bridge = _StripBridge()
        self._strip_bridge.ready.connect(self._on_strip_ready)
        self._pool = QThreadPool.globalInstance()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.list.viewport():
            if event.type() == QEvent.Type.Leave:
                self._hover_stop()
            elif event.type() == QEvent.Type.MouseMove:
                self._hover_move(event.position().toPoint())
        return super().eventFilter(obj, event)

    def _hover_move(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is not self._hover_item:
            self._hover_start(item)
        if self._hover_item is None or not self._hover_frames:
            return
        rect = self.list.visualItemRect(self._hover_item)
        if rect.width() <= 0:
            return
        frac = max(0.0, min(0.999, (pos.x() - rect.x()) / rect.width()))
        idx = int(frac * len(self._hover_frames))
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._hover_item.setIcon(self._hover_frames[idx])

    def _hover_start(self, item: QListWidgetItem | None) -> None:
        self._hover_stop()
        if item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        if not row:
            return
        shot_id = int(row.get("id") or 0)
        frames = self._strips.get(shot_id)
        if frames is None:
            # keyframes do disco seguram o scrub enquanto a tira não chega
            kf = row.get("keyframe")
            if not kf:
                return
            kf_path = self.episode_root / kf
            stem = kf_path.stem.rsplit("_", 1)[0]
            frames = []
            for f in sorted(kf_path.parent.glob(f"{stem}_*.jpg")):
                pm = _thumbnail(str(f))
                if pm is not None:
                    frames.append(QIcon(pm))
            if not frames:
                return
            self._request_strip(shot_id, row)
        self._hover_item = item
        self._hover_icon0 = item.icon()
        self._hover_frames = frames
        self._hover_idx = -1

    def _request_strip(self, shot_id: int, row: dict) -> None:
        if shot_id in self._strip_pending or shot_id in self._strips:
            return
        file_rel = row.get("file")
        if not file_rel:
            return
        clip = self.episode_root / file_rel
        if not clip.exists():
            return
        self._strip_pending.add(shot_id)
        self._pool.start(_StripJob(shot_id, str(clip), self._strip_bridge))

    def _on_strip_ready(self, shot_id: int, images: list) -> None:
        self._strip_pending.discard(shot_id)
        if not images:
            return
        icons = [QIcon(QPixmap.fromImage(img)) for img in images]
        self._strips[shot_id] = icons
        # se o mouse ainda está na mesma cena, refina o scrub na hora
        if self._hover_item is not None:
            row = self._hover_item.data(Qt.ItemDataRole.UserRole)
            if row and int(row.get("id") or 0) == shot_id:
                self._hover_frames = icons
                self._hover_idx = -1

    def _hover_stop(self) -> None:
        if self._hover_item is not None and self._hover_icon0 is not None:
            try:
                self._hover_item.setIcon(self._hover_icon0)
            except RuntimeError:
                pass  # item já foi destruído junto com a lista
        self._hover_item = None
        self._hover_frames = []
        self._hover_icon0 = None
        self._hover_idx = -1

    def load_for_character(self, shots: list[dict], character_name: str) -> None:
        self.list.clear()
        self.character_name = character_name
        # A vista "Episódio inteiro" traz shots SEM personagem — linha sem
        # a chave confidence. O grid mostra o que existe, sem inventar 0.00.
        confs = [s["confidence"] for s in shots if s.get("confidence") is not None]
        info = f"{character_name}: {len(shots)} shots"
        if confs:
            info += f" · confiança média {self._mean(confs):.2f}"
        self.info_label.setText(info)
        for row in shots:
            icon = self._icon_for(row.get("keyframe"))
            conf = row.get("confidence")
            text = (
                f"#{row['idx']:04d}  ({conf:.2f})" if conf is not None
                else f"#{row['idx']:04d}"
            )
            it = QListWidgetItem(icon, text)
            it.setData(Qt.ItemDataRole.UserRole, row)
            tip = (
                f"Shot {row['idx']:04d}\n"
                f"{row['start']:.2f}s → {row['end']:.2f}s  ({row['duration']:.2f}s)"
            )
            if conf is not None:
                tip += f"\nconfiança: {conf:.3f}"
            it.setToolTip(tip)
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
