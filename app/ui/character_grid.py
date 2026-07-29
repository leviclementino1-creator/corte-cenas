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
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPixmapCache,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..storage.db import Database
from . import theme

# As famílias sem as aspas do CSS — QFont quer o nome puro.
_MONO_FAMILY = "Cascadia Mono"
_SANS_FAMILY = "Segoe UI Variable Text"

_THUMB = QSize(192, 108)
_CACHE_SIZED = False

# Uma fonte só pra ordem das cenas: a grade usa, e a Biblioteca (que mostra o
# seletor no seu próprio cabeçalho) usa a mesma lista em vez de repetir os
# rótulos e correr o risco de as duas telas discordarem.
SORT_MODES: list[tuple[str, str]] = [
    ("⏱  cronológica", "idx"),
    ("⚠  duvidosas primeiro", "confidence"),
    ("⏳  mais longas primeiro", "duration"),
]
SORT_TIP = (
    "Cronológica: na ordem do episódio — é a ordem pra achar cenas\n"
    "vizinhas e juntá-las.\n"
    "Duvidosas primeiro: as de menor confiança na frente, pra revisar\n"
    "o que tem mais chance de estar errado.\n"
    "Mais longas primeiro: as cenas com mais material."
)


def fill_sort_box(combo: QComboBox) -> None:
    for texto, chave in SORT_MODES:
        combo.addItem(texto, chave)
    combo.setToolTip(SORT_TIP)


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


class _CardDelegate(QStyledItemDelegate):
    """Desenha cada cena como CARTÃO, não como miniatura pelada.

    O padrão do QListWidget é ícone com uma linha de texto embaixo — o que
    dá pra ler ali é só o número. O cartão carrega o que a pessoa usa pra
    decidir: o número sobreposto no canto (some no fundo da imagem, não
    rouba altura), quem aparece na cena, e a DURAÇÃO em âmbar — a cor que
    neste app significa tempo. Cena que veio de uma junção leva ⛓.
    """

    PAD = 6          # respiro interno do cartão
    BAR = 20         # faixa de baixo (nome + duração)

    def paint(self, painter, option, index) -> None:  # noqa: N802 (API Qt)
        row = index.data(Qt.ItemDataRole.UserRole) or {}
        r = option.rect.adjusted(2, 2, -2, -2)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # corpo do cartão
        painter.setBrush(QColor(theme.SURFACE_2))
        painter.setPen(QPen(QColor(theme.ACCENT if sel else
                                   (theme.ACCENT_DARK if hov else theme.LINE_SOFT)),
                            2 if sel else 1))
        painter.drawRoundedRect(r, 6, 6)

        # imagem
        img_rect = r.adjusted(self.PAD, self.PAD, -self.PAD, -(self.BAR + self.PAD))
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pix, QIcon):
            pix = pix.pixmap(img_rect.size())
        if isinstance(pix, QPixmap) and not pix.isNull():
            escala = pix.scaled(
                img_rect.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            destino = QRect(0, 0, escala.width(), escala.height())
            destino.moveCenter(img_rect.center())
            path = QPainterPath()
            path.addRoundedRect(QRectF(destino), 4, 4)
            painter.setClipPath(path)
            painter.drawPixmap(destino, escala)
            painter.setClipping(False)

        # etiqueta do número, sobreposta no canto da imagem
        num = f"#{int(row.get('idx') or 0):04d}"
        juntada = bool(row.get("merged"))
        if juntada:
            num += "  ⛓"
        painter.setFont(QFont(_MONO_FAMILY, 7, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(num) + 10
        tag = QRect(img_rect.left() + 4, img_rect.top() + 4, tw, fm.height() + 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 12, 16, 205))
        painter.drawRoundedRect(tag, 3, 3)
        painter.setPen(QColor(theme.TIME if juntada else theme.TXT_DIM))
        painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, num)

        # faixa de baixo: quem aparece | duração
        bar = QRect(r.left() + self.PAD, r.bottom() - self.BAR - 2,
                    r.width() - 2 * self.PAD, self.BAR)
        dur = float(row.get("duration") or 0)
        painter.setFont(QFont(_MONO_FAMILY, 7))
        fmb = painter.fontMetrics()
        txt_dur = f"{dur:.1f}s"
        w_dur = fmb.horizontalAdvance(txt_dur)
        painter.setPen(QColor(theme.TIME))
        painter.drawText(bar, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         txt_dur)
        quem = row.get("who") or ""
        if quem:
            painter.setFont(QFont(_SANS_FAMILY, 7))
            elid = painter.fontMetrics().elidedText(
                quem, Qt.TextElideMode.ElideRight, bar.width() - w_dur - 10
            )
            painter.setPen(QColor(theme.ACCENT if sel else theme.TXT_DIM))
            painter.drawText(bar, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             elid)
        painter.restore()


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
        self._rows: list[dict] = []      # o que está na tela, antes de ordenar
        self._who: dict[int, str] = {}   # id da cena -> quem aparece nela

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Cabeçalho: contagem à esquerda, ordem à direita. A ordem CRONOLÓGICA
        # é o padrão porque é a única em que "cenas vizinhas" ficam vizinhas
        # na tela — sem isso, juntar duas cenas partidas vira caça ao tesouro.
        # Vive num WIDGET (não num layout solto) porque a Biblioteca esconde
        # este cabeçalho: lá a contagem já está na linha do título e o
        # seletor de ordem mora ao lado dela, como na referência.
        self.head_widget = QWidget()
        head = QHBoxLayout(self.head_widget)
        head.setContentsMargins(2, 0, 2, 2)
        self.info_label = QLabel("")
        self.info_label.setStyleSheet(theme.label("dim"))
        head.addWidget(self.info_label, 1)
        head.addWidget(QLabel("ordem:"))
        self.sort_box = QComboBox()
        fill_sort_box(self.sort_box)
        self.sort_box.currentIndexChanged.connect(self._resort)
        head.addWidget(self.sort_box)
        layout.addWidget(self.head_widget)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(192, 108))
        self.list.setGridSize(QSize(214, 162))
        self.list.setItemDelegate(_CardDelegate(self.list))
        self.list.setUniformItemSizes(True)
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

    def set_header_visible(self, visivel: bool) -> None:
        """A Biblioteca desliga o cabeçalho: lá a contagem e o seletor de
        ordem vivem na linha do título do episódio."""
        self.head_widget.setVisible(visivel)

    def set_sort_mode(self, chave: str) -> None:
        i = self.sort_box.findData(chave)
        if i >= 0:
            self.sort_box.setCurrentIndex(i)

    def _resort(self) -> None:
        """Reordena o que já está na tela — sem ir ao banco de novo."""
        if self._rows:
            self.load_for_character(self._rows, self.character_name or "", _keep=True)

    def _sorted_rows(self, shots: list[dict]) -> list[dict]:
        modo = self.sort_box.currentData() or "idx"
        if modo == "confidence":
            return sorted(
                shots,
                key=lambda r: (r.get("confidence") if r.get("confidence") is not None else 2.0,
                               int(r.get("idx") or 0)),
            )
        if modo == "duration":
            return sorted(shots, key=lambda r: -float(r.get("duration") or 0))
        return sorted(shots, key=lambda r: int(r.get("idx") or 0))

    def load_for_character(
        self,
        shots: list[dict],
        character_name: str,
        _keep: bool = False,
        who_by_shot: dict[int, str] | None = None,
    ) -> None:
        """`who_by_shot` (id da cena -> "Nina, Eris") alimenta a faixa do
        cartão. Sem ele, o cartão usa o personagem da vista atual."""
        if who_by_shot is not None:
            self._who = who_by_shot
        for r in shots:
            if self._who:
                # Cena sem ninguém fica em branco — herdar o nome da VISTA
                # ("Episódio inteiro") era mentira: dizia que alguém está
                # ali quando o app não identificou ninguém.
                r["who"] = self._who.get(int(r.get("id") or -1), "")
            else:
                r["who"] = character_name
        self._rows = list(shots)
        shots = self._sorted_rows(shots)
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
            # Sem texto no item: quem escreve é o cartão (número sobreposto
            # na imagem, nome e duração na faixa de baixo).
            it = QListWidgetItem(icon, "")
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

        # --- juntar cenas ---
        # Dois caminhos de propósito: o atalho de um clique resolve o caso
        # comum (uma cena partida em duas por um flash) sem obrigar a
        # selecionar nada; a seleção múltipla cobre o resto.
        menu.addSeparator()
        ordenadas = sorted(rows, key=lambda r: float(r.get("start") or 0))
        if n >= 2 and self._sao_vizinhas(ordenadas):
            act_merge = QAction(f"⛓  Juntar as {n} cenas num clipe só", self)
            act_merge.triggered.connect(
                lambda: self.shot_action.emit("merge", ordenadas)
            )
            menu.addAction(act_merge)
        elif n >= 2:
            aviso = QAction("⛓  Juntar (só funciona em cenas vizinhas)", self)
            aviso.setEnabled(False)
            menu.addAction(aviso)
        elif n == 1:
            act_next = QAction("⛓  Juntar com a próxima cena", self)
            act_next.triggered.connect(
                lambda: self.shot_action.emit("merge_next", rows)
            )
            menu.addAction(act_next)
        if n == 1:
            act_unmerge = QAction("↩  Desfazer junção desta cena", self)
            act_unmerge.triggered.connect(
                lambda: self.shot_action.emit("unmerge", rows)
            )
            menu.addAction(act_unmerge)

        menu.exec(self.list.mapToGlobal(pos))

    @staticmethod
    def _sao_vizinhas(rows: list[dict]) -> bool:
        """Cenas encostadas no tempo (folga de meio segundo cobre a margem de
        meio frame que o corte usa)."""
        for a, b in zip(rows, rows[1:]):
            if float(b.get("start") or 0) - float(a.get("end") or 0) > 0.5:
                return False
        return True
