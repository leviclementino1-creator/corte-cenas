"""Aba Biblioteca: tudo que já foi cortado, num lugar só.

As outras abas são sobre UMA análise — a que acabou de rodar. Esta é sobre o
ACERVO: anime → temporada → episódio, com as cenas do episódio, quem aparece
nelas, e o clipe tocando em loop ao lado.

Duas decisões que valem o comentário:

1. A árvore é montada a partir do BANCO, não varrendo o disco. O banco já
   sabe anime/temporada/episódio e é instantâneo; varrer o Output seria
   lento e ainda mentiria sobre episódios que o usuário apagou pela metade.
   Episódio cuja pasta sumiu aparece esmaecido, em vez de sumir da lista —
   quem apagou merece ver que apagou.

2. O player toca ciclando frames com cv2 num QTimer, NÃO com o QMediaPlayer.
   Isso não é preciosismo: a v0.4.4 saiu com o player nativo do Qt e travou
   o app do dono em produção (foi retirada do ar no mesmo dia). Ciclar
   frames é o mesmo método do scrub que já roda liso há versões.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..storage.db import Database
from ..storage.organizer import refresh_shot_links
from . import quiet, theme
from .character_grid import SORT_MODES, SORT_TIP, ShotGrid


def _primeiro_nome(nome: str) -> str:
    """"Farion, Nina" -> "Nina"; "Greyrat, Eris Boreas" -> "Eris".

    As fontes guardam SOBRENOME, NOME. Cortar na vírgula e pegar o começo
    devolvia "Farion" e "Greyrat" — e como meio elenco divide sobrenome
    (os Greyrat), a faixa do cartão ficava com três cenas seguidas
    "Greyrat" sem dizer qual deles. Gente é chamada pelo primeiro nome."""
    if "," in nome:
        nome = nome.split(",", 1)[1]
    return nome.strip().split(" ")[0] or nome.strip()


def _mmss(segundos: float) -> str:
    """Timecode mm:ss.d — como qualquer programa de edição mostra. Minuto
    com zero à esquerda pra as duas pontas do intervalo ficarem alinhadas."""
    s = max(0.0, float(segundos))
    return f"{int(s // 60):02d}:{s % 60:04.1f}"


def _mmss_curto(segundos: float) -> str:
    """23:41 — duração cheia do episódio, sem décimos."""
    s = int(max(0.0, float(segundos)))
    return f"{s // 60}:{s % 60:02d}"


# As famílias sem as aspas do CSS — QFont quer o nome puro.
_FAMILIA_SANS = "Segoe UI Variable Text"
_FAMILIA_MONO = "Cascadia Mono"

# Papéis guardados no item da árvore (o Qt reserva UserRole pro nosso uso).
_NIVEL = Qt.ItemDataRole.UserRole + 2    # 0 anime · 1 temporada · 2 episódio
_CONTA = Qt.ItemDataRole.UserRole + 3    # número à direita
_ABERTO = Qt.ItemDataRole.UserRole + 4   # None = folha; True/False = ramo
_PILULA = Qt.ItemDataRole.UserRole + 5   # (ícone, nome, contagem)

_PREVIEW_W = 320          # largura do player lateral
_PREVIEW_FPS = 12         # suave o bastante pra leitura, leve pra UI
_MAX_FRAMES = 96          # teto de memória por clipe (~8s a 12 fps)


class _Bridge(QObject):
    ready = Signal(str, list)   # (caminho do clipe, frames já em QImage)


def _fonte(familia: str, px: int, negrito: bool = False) -> QFont:
    """Fonte em PIXEL. O sistema visual fala em px e QFont(fam, n) usa
    PONTO — passar um pelo outro dá outro tamanho na tela."""
    f = QFont(familia)
    f.setPixelSize(px)
    if negrito:
        f.setWeight(QFont.Weight.DemiBold)
    return f


class _Trilho(QWidget):
    """Seletor de poucas opções fixas: os três botões ficam num trilho e o
    escolhido acende. Lista suspensa esconde as alternativas atrás de um
    clique — com três opções que nunca mudam, esconder não paga o preço."""

    def __init__(self, opcoes: list[tuple[str, str]], ao_mudar) -> None:
        super().__init__()
        self._ao_mudar = ao_mudar
        self._chave = opcoes[0][1]
        self._botoes: dict[str, QPushButton] = {}
        self._rotulos = {c: t for t, c in opcoes}
        self._compacto = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.setStyleSheet(
            f"_Trilho{{background:{theme.WELL};border:1px solid {theme.LINE};"
            f"border-radius:{theme.R_S}px;}}"
        )
        for texto, chave in opcoes:
            b = QPushButton(texto)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=chave: self.escolher(k))
            self._botoes[chave] = b
            lay.addWidget(b)
        self._pintar()

    def _pintar(self) -> None:
        for chave, b in self._botoes.items():
            ligado = chave == self._chave
            b.setChecked(ligado)
            b.setStyleSheet(
                f"QPushButton{{border:none;border-radius:3px;padding:0 12px;"
                f"min-height:26px;font-size:12.5px;font-weight:{600 if ligado else 400};"
                f"background:{theme.ACCENT_INK if ligado else 'transparent'};"
                f"color:{theme.ACCENT if ligado else theme.TXT_DIM};}}"
                f"QPushButton:hover{{color:{theme.TXT};}}"
            )

    def compactar(self, sim: bool) -> None:
        """Em coluna estreita fica só o ícone: o trilho inteiro empurrava o
        título e o texto das opções saía cortado pela metade."""
        if getattr(self, "_compacto", None) == sim:
            return
        self._compacto = sim
        for chave, b in self._botoes.items():
            texto = self._rotulos[chave]
            b.setText(texto.split()[0] if sim else texto)
            b.setToolTip(texto if sim else "")
        self._pintar()

    def escolher(self, chave: str) -> None:
        if chave == self._chave:
            self._pintar()
            return
        self._chave = chave
        self._pintar()
        if self._ao_mudar:
            self._ao_mudar()

    def currentData(self) -> str:   # noqa: N802 (mesma API do QComboBox)
        return self._chave


class _Pilula(QStyledItemDelegate):
    """Filtro de personagem: ícone · nome · contagem.

    O nome vem em texto e a contagem em MONO ÂMBAR — nunca os dois na mesma
    cor. É isso que deixa a fileira legível de longe: o olho pega a coluna
    de números sem ler os nomes. Item de lista comum pinta tudo de uma cor
    só, por isso a pílula é desenhada aqui.
    """

    ALTURA = theme.H_PILL

    def _partes(self, index) -> tuple[str, str, str]:
        d = index.data(Qt.ItemDataRole.UserRole + 5) or ("", "", "")
        return d

    def paint(self, painter, option, index) -> None:  # noqa: N802 (API Qt)
        from PySide6.QtWidgets import QStyle as _S
        icone, nome, conta = self._partes(index)
        sel = bool(option.state & _S.StateFlag.State_Selected)
        hov = bool(option.state & _S.StateFlag.State_MouseOver)
        r = option.rect

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if sel:
            fundo, borda = theme.ACCENT_INK, theme.ACCENT
        elif hov:
            fundo, borda = theme.SURFACE_3, theme.LINE_BRIGHT
        else:
            fundo, borda = theme.SURFACE_2, theme.LINE
        painter.setBrush(QColor(fundo))
        painter.setPen(QPen(QColor(borda), 1))
        painter.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 17, 17)

        x = r.left() + 15
        meio = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if icone:
            painter.setFont(_fonte(_FAMILIA_SANS, 13))
            painter.setPen(QColor(theme.TXT_DIM))
            larg = painter.fontMetrics().horizontalAdvance(icone)
            painter.drawText(QRect(x, r.top(), larg, r.height()), meio, icone)
            x += larg + 8
        painter.setFont(_fonte(_FAMILIA_SANS, 13, sel))
        painter.setPen(QColor(theme.TXT))
        larg = painter.fontMetrics().horizontalAdvance(nome)
        painter.drawText(QRect(x, r.top(), larg, r.height()), meio, nome)
        x += larg + 8
        painter.setFont(_fonte(_FAMILIA_MONO, 12))
        painter.setPen(QColor(theme.TIME))
        painter.drawText(
            QRect(x, r.top(), r.right() - x, r.height()), meio, conta
        )
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802 (API Qt)
        icone, nome, conta = self._partes(index)
        larg = 30
        if icone:
            larg += QFontMetrics(_fonte(_FAMILIA_SANS, 13)).horizontalAdvance(icone) + 8
        larg += QFontMetrics(_fonte(_FAMILIA_SANS, 13, True)).horizontalAdvance(nome) + 8
        larg += QFontMetrics(_fonte(_FAMILIA_MONO, 12)).horizontalAdvance(conta)
        return QSize(larg, self.ALTURA)


class _LinhaAcervo(QStyledItemDelegate):
    """Pinta a linha da árvore inteira: rótulo à esquerda, contagem à
    direita, e a barra ciano de 3px quando selecionada.

    Por que um delegate em vez de duas colunas com QSS: numa QTreeWidget o
    `::item` do QSS vale POR CÉLULA, então a linha selecionada virava dois
    retângulos arredondados separados, cada um com a sua barra à esquerda —
    e a coluna da contagem ainda era espremida pela borda. Uma coluna só,
    pintada à mão, resolve os dois de uma vez.
    """

    ALTURA = theme.H_ROW

    def paint(self, painter, option, index) -> None:  # noqa: N802 (API Qt)
        from PySide6.QtWidgets import QStyle as _S
        sel = bool(option.state & _S.StateFlag.State_Selected)
        hov = bool(option.state & _S.StateFlag.State_MouseOver)
        r = option.rect.adjusted(2, 1, -2, -1)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if sel or hov:
            painter.setPen(QPen(QColor(theme.ACCENT_DIM if sel else theme.SURFACE_3), 1))
            painter.setBrush(QColor(theme.ACCENT_INK if sel else theme.SURFACE_2))
            painter.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5),
                                    theme.R_S, theme.R_S)
        if sel:
            # A barra é o que sobrevive à falta de sombra: fundo sozinho
            # some contra a superfície vizinha.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(QRectF(r.left(), r.top() + 1, 3, r.height() - 2),
                                    1.5, 1.5)

        dados = index.data(Qt.ItemDataRole.UserRole) or {}
        nivel = int(index.data(_NIVEL) or 0)
        sumiu = bool(dados) and not dados.get("ok", True)
        recuo = (10, 24, 38)[min(nivel, 2)]
        meio = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        # triângulo pequeno e apagado: é affordance, não conteúdo
        aberto = index.data(_ABERTO)
        if aberto is not None:
            painter.setFont(_fonte(_FAMILIA_SANS, 9))
            painter.setPen(QColor(theme.TXT_FAINT))
            painter.drawText(QRect(r.left() + recuo, r.top(), 12, r.height()),
                             meio, "▾" if aberto else "▸")
            recuo += 14

        # a contagem é NÚMERO: mono âmbar, como toda medida do app
        larg_conta = 0
        conta = index.data(_CONTA)
        if conta is not None:
            painter.setFont(_fonte(_FAMILIA_MONO, 12))
            txt = str(conta)
            larg_conta = painter.fontMetrics().horizontalAdvance(txt) + 12
            painter.setPen(QColor(theme.TXT_GHOST if sumiu else theme.TIME))
            painter.drawText(
                r.adjusted(0, 0, -10, 0),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, txt,
            )

        # Hierarquia por VALOR do texto: anime claro, temporada média,
        # episódio médio (e claro quando é o aberto). O ciano fica reservado
        # pra barra da seleção — dois destaques na mesma linha brigam.
        fonte = _fonte(_FAMILIA_SANS, 13)
        fonte.setItalic(sumiu)
        painter.setFont(fonte)
        if sumiu:
            cor = theme.TXT_GHOST
        elif sel or nivel == 0 or hov:
            cor = theme.TXT
        else:
            cor = theme.TXT_DIM
        painter.setPen(QColor(cor))
        caixa = r.adjusted(recuo, 0, -larg_conta, 0)
        painter.drawText(
            caixa, meio,
            painter.fontMetrics().elidedText(
                str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
                Qt.TextElideMode.ElideRight, caixa.width(),
            ),
        )
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802 (API Qt)
        s = super().sizeHint(option, index)
        s.setHeight(self.ALTURA)
        return s


class _Tela(QLabel):
    """A tela do clipe: mantém 16:9 sobre a largura que TIVER.

    Recalcular isso no resize da aba não funciona — arrastar a divisória do
    splitter muda a largura DESTE widget sem que a aba mude de tamanho, e a
    tela ficava com a altura de outro momento (um retângulo alto com o vídeo
    boiando no meio). Quem sabe a largura é ela mesma."""

    def __init__(self, texto: str) -> None:
        super().__init__(texto)
        self.ao_redimensionar = None   # a Biblioteca pendura o selo aqui

    def resizeEvent(self, event) -> None:   # noqa: N802 (API Qt)
        alt = int(max(self.width(), 200) * 9 / 16)
        if alt != self.height():
            self.setFixedHeight(alt)   # o guard corta o vaivém
        if self.ao_redimensionar is not None:
            self.ao_redimensionar()
        super().resizeEvent(event)


class _LoadClip(QRunnable):
    """Decodifica o clipe fora da thread da interface e devolve os quadros
    prontos. Amostra até _MAX_FRAMES: clipe de 30s não pode virar 700
    imagens na memória só porque alguém clicou nele."""

    def __init__(self, path: Path, bridge: _Bridge) -> None:
        super().__init__()
        self.path = path
        self.bridge = bridge

    def run(self) -> None:
        import cv2

        frames: list[QImage] = []
        try:
            cap = cv2.VideoCapture(str(self.path))
            if cap.isOpened():
                total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                passo = max(1, total // _MAX_FRAMES) if total else 1
                i = 0
                while len(frames) < _MAX_FRAMES:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if i % passo == 0:
                        h, w = frame.shape[:2]
                        nh = max(1, int(h * (_PREVIEW_W / max(w, 1))))
                        frame = cv2.resize(frame, (_PREVIEW_W, nh))
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        img = QImage(
                            frame.data, frame.shape[1], frame.shape[0],
                            frame.strides[0], QImage.Format.Format_RGB888,
                        ).copy()   # .copy(): o buffer do numpy morre aqui
                        frames.append(img)
                    i += 1
                cap.release()
        except Exception:
            frames = []
        try:
            self.bridge.ready.emit(str(self.path), frames)
        except RuntimeError:
            # A janela fechou enquanto o clipe carregava — o destinatário do
            # sinal já não existe. Nada a entregar, e nada a quebrar.
            pass


class LibraryTab(QWidget):
    """Acervo navegável: árvore à esquerda, cenas no meio, player à direita."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.db = Database(self.config.cache_path / "index.db")
        self._episode: dict | None = None
        self._root: Path | None = None
        self._frames: list[QImage] = []
        self._frame_i = 0
        self._pending: str | None = None
        self._current_shot: dict | None = None
        self._filtro: str | None = None          # personagem filtrado agora
        self._recarregando = False               # guarda contra sinal de seleção
        self._by_shot: dict[int, list[dict]] = {}
        self._shots: list[dict] = []

        self._bridge = _Bridge()
        self._bridge.ready.connect(self._on_clip_ready)
        self._pool = QThreadPool.globalInstance()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / _PREVIEW_FPS))
        self._timer.timeout.connect(self._tick)

        split = QSplitter(Qt.Orientation.Horizontal)

        # --- esquerda: árvore do acervo
        left = QWidget()
        left.setObjectName("painelAcervo")
        left.setStyleSheet(
            f"QWidget#painelAcervo{{background:{theme.SURFACE};"
            f"border-right:1px solid {theme.LINE};}}"
        )
        lv = QVBoxLayout(left)
        # Medidas da maquete: painel 8 nas laterais, 14 em cima, 12 embaixo;
        # as linhas quase encostadas umas nas outras (2).
        lv.setContentsMargins(8, 14, 8, 12)
        lv.setSpacing(2)
        lbl_acervo = QLabel("ACERVO")
        lbl_acervo.setStyleSheet(theme.label("eyebrow") + "padding:0 6px 8px;")
        lv.addWidget(lbl_acervo)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        # UMA coluna, pintada pelo delegate: o rótulo à esquerda e a
        # contagem à direita na mesma linha (ver _LinhaAcervo). O recuo é
        # desenhado por nós, então a árvore não precisa nem de coluna de
        # ramo — que era onde o Qt pintava as guias na cor de destaque.
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegate(_LinhaAcervo(self.tree))
        # A árvore não é um "campo": ela É o painel. Sem caixa em volta —
        # a divisória vertical já separa.
        self.tree.setStyleSheet("QTreeWidget{background:transparent;border:none;}")
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.itemExpanded.connect(lambda it: self._marcar_seta(it, True))
        self.tree.itemCollapsed.connect(lambda it: self._marcar_seta(it, False))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu_acervo)
        lv.addWidget(self.tree, 1)

        # A faixa de ações se separa da árvore por um traço, como na
        # maquete — sem ele, o último episódio e o primeiro atalho viram a
        # mesma lista.
        risco_acoes = QWidget()
        risco_acoes.setFixedHeight(1)
        risco_acoes.setStyleSheet(f"background:{theme.LINE_SOFT};")
        lv.addSpacing(8)
        lv.addWidget(risco_acoes)
        lv.addSpacing(8)
        lbl_acoes = QLabel("AÇÕES")
        lbl_acoes.setStyleSheet(theme.label("eyebrow") + "padding:0 10px 6px;")
        lv.addWidget(lbl_acoes)
        # Ações da barra lateral em texto (ghost), não em dois botões
        # sólidos: elas são atalhos permanentes, não a ação principal da
        # tela — botão cheio aqui rouba o olho da grade de cenas.
        self.btn_reload = QPushButton("↻   Atualizar lista")
        self.btn_reload.setStyleSheet(theme.button("ghost"))
        self.btn_reload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reload.clicked.connect(self.reload)
        lv.addWidget(self.btn_reload)
        self.btn_open = QPushButton("📂   Abrir pasta do episódio")
        self.btn_open.setStyleSheet(theme.button("ghost"))
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_folder)
        lv.addWidget(self.btn_open)
        # Só aparece quando há o que fazer: pasta com clipes que o banco não
        # conhece (reorganizou no Explorer, trocou de banco, apagou do acervo
        # sem levar a pasta). Sem isso a Biblioteca esconde clipes que estão
        # ali — mente sobre o acervo tanto quanto listando o que não existe.
        self.btn_readotar = QPushButton()
        self.btn_readotar.setStyleSheet(theme.button("ghost"))
        self.btn_readotar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_readotar.setVisible(False)
        self.btn_readotar.clicked.connect(self._readotar)
        lv.addWidget(self.btn_readotar)
        split.addWidget(left)

        # --- meio: cenas + personagens
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(0)
        cabecalho = QWidget()
        cv = QVBoxLayout(cabecalho)
        cv.setContentsMargins(16, 16, 16, 12)
        cv.setSpacing(12)
        # TÍTULO e DADOS em linhas separadas. Amontoados na mesma linha
        # ("Mushoku Tensei III: … — S03E02 · 332 cenas · 6 personagens") o
        # nome do episódio e a contagem disputavam o mesmo olhar e nenhum dos
        # dois era lido. Título é nome; a linha de baixo é medida.
        linha_titulo = QHBoxLayout()
        linha_titulo.setSpacing(20)
        self._titulo_cheio = "Escolha um episódio na lista"
        self.header = QLabel(self._titulo_cheio)
        # Sem mínimo, o QLabel exige a largura do texto inteiro e empurra o
        # seletor de ordem pra fora; com ele, o título encolhe e é
        # ELIDIDO por _elide_titulo (o Qt não elide QLabel sozinho — ele
        # simplesmente corta a palavra no meio, que era o que acontecia).
        self.header.setMinimumWidth(120)
        self.header.setStyleSheet(
            f"font-family:{theme.DISP};font-size:19px;font-weight:600;color:{theme.TXT};padding-left:4px;"
        )
        linha_titulo.addWidget(self.header, 1)
        lbl_ordem = QLabel("ordem")
        lbl_ordem.setStyleSheet(theme.label("faint"))
        linha_titulo.addWidget(lbl_ordem)
        # O seletor de ordem é um TRILHO de três opções, não uma lista
        # suspensa: são três, sempre as mesmas, e a escolhida fica visível
        # sem abrir nada. Ele mora aqui (e não dentro da grade) pra
        # sobreviver à troca de episódio, que reconstrói a grade.
        self.sort_box = _Trilho(
            [(t, c) for t, c in SORT_MODES], self._on_sort
        )
        self.sort_box.setToolTip(SORT_TIP)
        linha_titulo.addWidget(self.sort_box)
        cv.addLayout(linha_titulo)

        self.meta = QLabel("")
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.meta.setWordWrap(True)
        self.meta.setStyleSheet(
            f"font-family:{theme.MONO};font-size:12.5px;color:{theme.TXT_FAINT};"
            f"padding-left:4px;"
        )
        cv.addWidget(self.meta)

        self.chars = QListWidget()
        self.chars.setItemDelegate(_Pilula(self.chars))
        self.chars.setFlow(QListWidget.Flow.LeftToRight)
        # UMA linha, com rolagem horizontal. Quebrando em duas, a fileira de
        # filtros crescia pra baixo e empurrava a grade — o conteúdo perdia
        # altura por causa do controle que serve a ele.
        self.chars.setWrapping(False)
        self.chars.setResizeMode(QListWidget.ResizeMode.Adjust)
        # A altura é CALCULADA depois de encher (ver _ajustar_altura_pills).
        # Chutar 78px cortava a segunda fileira de pílulas exatamente no meio
        # da palavra — a caixa comendo o texto.
        self.chars.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chars.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chars.setHorizontalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.chars.setSpacing(4)   # 4 de cada lado = os 8 da maquete
        self.chars.setFrameShape(QListWidget.Shape.NoFrame)
        # PÍLULAS, como na referência: filtro de personagem é uma escolha
        # rápida entre poucos, não uma lista pra percorrer. Arredondado e
        # espaçado lê como "botão"; item de lista lê como "linha".
        # A pílula é desenhada pelo delegate (_Pilula) porque nome e contagem
        # têm cores diferentes. Aqui só se apaga a moldura da lista.
        self.chars.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;}}"
            f"QListWidget::item{{border:none;background:transparent;}}"
        )
        self.chars.itemSelectionChanged.connect(self._on_char_filter)
        self.chars.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chars.customContextMenuRequested.connect(self._menu_personagem)
        cv.addWidget(self.chars)
        mv.addWidget(cabecalho)

        risco = QWidget()
        risco.setFixedHeight(1)
        risco.setStyleSheet(f"background:{theme.LINE_SOFT};")
        mv.addWidget(risco)

        self.grid_box = QWidget()
        self._grid_layout = QVBoxLayout(self.grid_box)
        # O `spacing` da grade (6) já cria margem em VOLTA de cada cartão,
        # inclusive nas bordas. Se o container também usasse 20/14, a
        # primeira coluna ficaria a 26 e as outras a 12 entre si. Descontando
        # o spacing aqui, a distância até a borda fica igual em todos os
        # lados e igual ao respiro entre os cartões.
        self._grid_layout.setContentsMargins(20 - 6, 14 - 6, 20 - 6, 14 - 6)
        self.grid: ShotGrid | None = None
        mv.addWidget(self.grid_box, 1)
        split.addWidget(mid)

        # --- direita: player em loop
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 14, 16, 14)
        rv.setSpacing(14)
        cap = QLabel("A CENA")
        cap.setStyleSheet(theme.label("eyebrow"))
        rv.addWidget(cap)
        # A tela do clipe acompanha a LARGURA do painel mantendo 16:9 (ver
        # _ajustar_tela). Altura livre fazia o quadro boiar no meio de um
        # painel vazio; altura fixa em pixel deixava um selo de 320px num
        # monitor de 2560. Proporção é a única regra que serve aos dois.
        self.player = _Tela("Clique numa cena\npra ela tocar aqui, em loop")
        self.player.ao_redimensionar = self._posicionar_pill
        self.player.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player.setFixedHeight(int(_PREVIEW_W * 9 / 16))
        self.player.setMinimumWidth(240)
        # SEM padding: o clipe preenche a caixa. Com respiro interno, o vídeo
        # ficava boiando dentro de uma moldura e nunca encostava na borda —
        # e a caixa deixava de ser "a tela" pra virar um quadro.
        self.player.setStyleSheet(
            f"background:{theme.SURFACE_2};border:1px solid {theme.LINE};"
            f"border-radius:6px;color:{theme.TXT_FAINT};padding:0;"
        )
        rv.addWidget(self.player)

        # Barra de posição do loop: 3px no pé da tela, como na referência. É
        # o que diz que o clipe está andando (e onde) sem escrever nada.
        self.loop_bar = QWidget(self.player)
        self.loop_bar.setStyleSheet(f"background:{theme.TIME};")
        self.loop_bar.hide()

        # Selo "em loop" POR CIMA da tela, como na referência: quem chega no
        # meio da sessão precisa saber que aquilo repete sozinho e não é um
        # quadro congelado.
        self.loop_pill = QLabel("▸ em loop", self.player)
        self.loop_pill.setStyleSheet(theme.chip("accent"))
        self.loop_pill.hide()

        # Ficha da cena: rótulo numa coluna FIXA de 66px e o valor do lado —
        # com o rótulo colado no valor, os dados de quatro cenas seguidas
        # nunca alinham e o olho tem que reler cada linha. Tabela em rich
        # text é o jeito de o Qt garantir a coluna.
        self.player_info = QLabel("")
        self.player_info.setTextFormat(Qt.TextFormat.RichText)
        self.player_info.setStyleSheet(
            f"font-family:{theme.MONO};font-size:12.5px;color:{theme.TXT_DIM};"
        )
        rv.addWidget(self.player_info)

        divisor = QWidget()
        divisor.setFixedHeight(1)
        divisor.setStyleSheet(f"background:{theme.LINE_SOFT};")
        rv.addWidget(divisor)

        # Ações da cena — curar sem sair da Biblioteca. A principal é
        # centralizada e cheia; as outras duas são linhas de menu: ícone à
        # esquerda, texto alinhado, altura menor.
        acoes = QVBoxLayout()
        acoes.setSpacing(8)
        self.btn_merge = QPushButton("⛓   Juntar com a próxima")
        self.btn_merge.setStyleSheet(theme.button("primary"))
        self.btn_merge.clicked.connect(lambda: self._scene_action("merge_next"))
        acoes.addWidget(self.btn_merge)

        self.btn_move = QPushButton("↗    Mover pra outro personagem")
        self.btn_move.setStyleSheet(theme.button("linha"))
        self.btn_move.clicked.connect(lambda: self._scene_action("move"))
        acoes.addWidget(self.btn_move)

        self.btn_drop = QPushButton("⤫    Remover desta pasta")
        self.btn_drop.setStyleSheet(theme.button("linha-danger"))
        self.btn_drop.clicked.connect(lambda: self._scene_action("remove"))
        acoes.addWidget(self.btn_drop)
        rv.addLayout(acoes)

        for b in (self.btn_merge, self.btn_move, self.btn_drop):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setEnabled(False)

        # Sem stretch entre as ações e os atalhos: eles pertencem ao mesmo
        # bloco. O vazio vai TODO pro fim do painel.
        atalhos = QLabel(
            f"<span style='color:{theme.TXT_FAINT}'>Atalhos:</span> "
            + " · ".join(
                f"<span style='font-family:{theme.MONO};color:{theme.TXT_DIM}'>{t}</span>"
                f" <span style='color:{theme.TXT_FAINT}'>{d}</span>"
                for t, d in (("J", "juntar"), ("M", "mover"), ("Del", "remover"),
                             ("←→", "cena anterior/próxima"))
            )
        )
        atalhos.setWordWrap(True)
        atalhos.setStyleSheet(
            f"font-size:12px;color:{theme.TXT_FAINT};"
            f"border-top:1px solid {theme.LINE_SOFT};padding-top:12px;"
            f"margin-top:2px;"
        )
        rv.addWidget(atalhos)
        rv.addStretch(1)
        split.addWidget(right)
        self._painel_cena = right
        self._cena_forcada = False

        # Os atalhos do rodapé precisam EXISTIR: escrever tecla que não faz
        # nada é pior que não escrever.
        for tecla, acao in (("J", "merge_next"), ("M", "move"), ("Del", "remove")):
            QShortcut(QKeySequence(tecla), self,
                      activated=lambda a=acao: self._scene_action(a))
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=lambda: self._passo_cena(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=lambda: self._passo_cena(1))
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.alternar_painel_cena)

        # A grade de cenas é o conteúdo; as colunas laterais servem a ela.
        # Com 240+700+424 numa janela de 1265 sobravam ~600px no meio = duas
        # colunas de miniatura, com a terceira cortada pela metade.
        # Esticar a janela dá espaço PRA GRADE (mais cenas por fileira); as
        # laterais têm largura de leitura e ficam onde estão. Os limites
        # existem pra ninguém arrastar a divisória até a grade sumir.
        # As duas colunas laterais têm largura FIXA — elas não ganham nada
        # com mais espaço (a árvore tem largura de leitura; o painel da cena
        # é do tamanho do player 16:9 + os três botões sem quebrar linha).
        # Quem estica é a GRADE, o único elástico da janela.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        left.setFixedWidth(theme.W_ACERVO)
        right.setFixedWidth(theme.W_CENA)
        split.setSizes([theme.W_ACERVO, 900, theme.W_CENA])
        split.setCollapsible(1, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(split)
        self.reload()

    # ---------- árvore ----------

    # Guardado no item pra o triângulo poder ser reescrito sem comer o rótulo.
    _ROTULO = Qt.ItemDataRole.UserRole + 1

    @staticmethod
    def _marcar_seta(item: QTreeWidgetItem, aberto: bool) -> None:
        item.setData(0, _ABERTO, aberto)

    def _no_ramo(self, rotulo: str, contagem: int, nivel: int) -> QTreeWidgetItem:
        """Nó que abre e fecha: triângulo no rótulo, contagem à direita."""
        item = QTreeWidgetItem([rotulo])
        item.setData(0, self._ROTULO, rotulo)
        item.setData(0, _ABERTO, True)
        item.setData(0, _NIVEL, nivel)
        item.setData(0, _CONTA, contagem)
        item.setToolTip(0, rotulo)
        return item

    def _ver_esquecidas(self) -> None:
        """Atualiza o aviso de pasta que o banco não conhece."""
        from ..storage import readocao

        try:
            achadas = readocao.orfas(self.config.output_path, self.db)
        except Exception:  # noqa: BLE001 — aviso não pode derrubar a aba
            achadas = []
        self._esquecidas = achadas
        if achadas:
            clipes = sum(o["clipes"] for o in achadas)
            self.btn_readotar.setText(
                f"＋   {len(achadas)} pasta(s) esquecida(s), {clipes} clipes"
            )
            self.btn_readotar.setToolTip(
                "Estas pastas têm clipes no disco mas o banco não as conhece.\n"
                "Trazer pro acervo NÃO reanalisa nada — o mapa das cenas sai do\n"
                "metadata que a análise deixou."
            )
        self.btn_readotar.setVisible(bool(achadas))

    def _readotar(self) -> None:
        from ..storage import readocao

        achadas = getattr(self, "_esquecidas", [])
        if not achadas:
            return
        linhas = "\n".join(
            f"• {o['anime']} T{o['temporada']}E{o['episodio']:02d} — {o['clipes']} clipes"
            for o in achadas[:6]
        )
        if len(achadas) > 6:
            linhas += f"\n• … e mais {len(achadas) - 6}"
        if quiet.question(
            self, "Pastas esquecidas",
            f"{linhas}\n\n"
            "Trazer pro acervo?\n\n"
            "• NADA é reanalisado — o mapa das cenas sai do metadata\n"
            "• Personagem que o banco não conhece fica de fora; a cena volta "
            "sem dono",
        ) != QMessageBox.StandardButton.Yes:
            return
        total = 0
        for o in achadas:
            r = readocao.readotar(self.db, Path(o["pasta"]))
            if r["ok"]:
                total += r["cenas"]
        quiet.information(
            self, "Pronto",
            f"{len(achadas)} episódio(s) de volta no acervo, {total} cenas.",
        )
        self.reload()

    def reload(self) -> None:
        """(Re)monta anime → temporada → episódio a partir do banco."""
        self._ver_esquecidas()
        self.tree.clear()
        with self.db.connect() as c:
            rows = c.execute(
                """SELECT e.id, e.season, e.episode, e.source_file, a.title
                   FROM episode e JOIN anime a ON a.id = e.anime_id
                   ORDER BY a.title, e.season, e.episode"""
            ).fetchall()
            # Quantas cenas cada episódio tem, numa consulta só (uma por
            # episódio deixaria a árvore lenta assim que o acervo crescesse).
            n_cenas = {
                int(r["episode_id"]): int(r["n"])
                for r in c.execute(
                    "SELECT episode_id, COUNT(*) AS n FROM shot GROUP BY episode_id"
                ).fetchall()
            }
        por_anime: dict[str, dict[int, list]] = {}
        for r in rows:
            por_anime.setdefault(r["title"], {}).setdefault(r["season"], []).append(r)

        for titulo, temporadas in por_anime.items():
            n_eps = sum(len(v) for v in temporadas.values())
            no_anime = self._no_ramo(titulo, n_eps, 0)
            for temp, eps in sorted(temporadas.items()):
                no_temp = self._no_ramo(f"Temporada {temp}", len(eps), 1)
                for r in sorted(eps, key=lambda x: x["episode"]):
                    root = self._episode_root(r["title"], r["season"], r["episode"])
                    existe = root is not None and (root / "shots").exists()
                    label = f"Episódio {r['episode']:02d}"
                    # Pasta ausente: itálico apagado (o delegate cuida), sem
                    # ícone de erro — e continua na lista, porque quem apagou
                    # merece ver que apagou.
                    no_ep = QTreeWidgetItem([
                        label if existe else f"{label}  (sumiu do disco)"
                    ])
                    no_ep.setData(0, _NIVEL, 2)
                    no_ep.setData(0, _CONTA, n_cenas.get(int(r["id"]), 0))
                    if not existe:
                        no_ep.setToolTip(
                            0, "A pasta deste episódio não está mais no disco."
                        )
                    no_ep.setData(0, Qt.ItemDataRole.UserRole, {
                        "id": int(r["id"]), "title": r["title"],
                        "season": int(r["season"]), "episode": int(r["episode"]),
                        "root": str(root) if root else "", "ok": existe,
                    })
                    no_temp.addChild(no_ep)
                no_anime.addChild(no_temp)
            self.tree.addTopLevelItem(no_anime)
            # Tudo aberto de saída, e o triângulo já nasce apontando pra
            # baixo — seta que mente sobre o estado é pior que seta nenhuma.
            no_anime.setExpanded(True)
            for i in range(no_anime.childCount()):
                no_anime.child(i).setExpanded(True)
        if not rows:
            self.tree.addTopLevelItem(
                QTreeWidgetItem(["Nada cortado ainda — analise um episódio primeiro"])
            )

    # ---------- apagar do acervo ----------

    def _episodios_sob(self, item: QTreeWidgetItem) -> list[dict]:
        """Os episódios pendurados neste nó — ele mesmo, se for um episódio.
        Assim o apagar funciona igual no anime, na temporada e no episódio,
        sem três caminhos diferentes pra dar errado de três jeitos."""
        dados = item.data(0, Qt.ItemDataRole.UserRole)
        if dados:
            return [dados]
        fora: list[dict] = []
        for i in range(item.childCount()):
            fora += self._episodios_sob(item.child(i))
        return fora

    def _menu_acervo(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        eps = self._episodios_sob(item)
        if not eps:
            return
        e_episodio = item.data(0, Qt.ItemDataRole.UserRole) is not None
        rotulo = item.data(0, self._ROTULO) or item.text(0)

        menu = QMenu(self)
        if e_episodio:
            abrir = QAction("📂  Abrir pasta", self)
            abrir.setEnabled(bool(eps[0].get("ok")))
            abrir.triggered.connect(lambda: self._abrir(Path(eps[0]["root"])))
            menu.addAction(abrir)
            menu.addSeparator()
            texto = "🗑  Apagar este episódio"
        elif rotulo.startswith("Temporada"):
            texto = f"🗑  Apagar esta temporada ({len(eps)} episódios)"
        else:
            texto = f"🗑  Apagar este anime ({len(eps)} episódios)"
        apagar = QAction(texto, self)
        apagar.triggered.connect(lambda: self._apagar_do_acervo(eps, rotulo))
        menu.addAction(apagar)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _apagar_do_acervo(self, eps: list[dict], rotulo: str) -> None:
        """Tira do acervo: as pastas vão pra lixeira e as cenas saem do
        banco. Referências e o que o app aprendeu do personagem ficam."""
        n_cenas = 0
        with self.db.connect() as c:
            for ep in eps:
                r = c.execute(
                    "SELECT COUNT(*) AS n FROM shot WHERE episode_id = ?", (ep["id"],)
                ).fetchone()
                n_cenas += int(r["n"]) if r else 0
        alvo = rotulo if len(eps) == 1 else f"{rotulo} — {len(eps)} episódios"
        if quiet.question(
            self, "Apagar do acervo",
            f"Apagar \"{alvo}\"?\n\n"
            f"• {n_cenas} cenas saem do banco\n"
            "• As pastas vão pra Output\\_lixeira (NÃO são destruídas —\n"
            "  se você se arrepender, é só arrastar de volta)\n"
            "• As fotos de referência e o que o app aprendeu ficam intactos\n"
            "• Pra ter o espaço de volta, apague a _lixeira no Explorer",
        ) != QMessageBox.StandardButton.Yes:
            return

        from ..curation import enviar_para_lixeira
        apagados = {int(ep["id"]) for ep in eps}
        destino = None
        for ep in eps:
            if ep.get("root"):
                try:
                    destino = enviar_para_lixeira(
                        Path(ep["root"]), self.config.output_path
                    ) or destino
                except OSError as e:
                    quiet.information(
                        self, "Não deu pra mover a pasta",
                        f"{ep['root']}\n\n{e}\n\nProvavelmente um arquivo dessa "
                        "pasta está aberto em outro programa. O acervo não foi "
                        "alterado.",
                    )
                    return
            self.db.delete_episode(int(ep["id"]))

        if self._episode is not None and int(self._episode["id"]) in apagados:
            self._limpar_episodio()
        self.reload()
        quiet.information(
            self, "Apagado do acervo",
            f"{len(eps)} episódio(s) fora do acervo.\n\n"
            + (f"As pastas estão em:\n{destino.parent}" if destino else
               "Não havia pasta no disco — só as cenas do banco saíram."),
        )

    def _limpar_episodio(self) -> None:
        """Zera o meio e a direita — usado quando o episódio aberto some."""
        self._episode = None
        self._root = None
        self._shots = []
        self._by_shot = {}
        self._filtro = None
        self._current_shot = None
        self._stop_player()
        self.chars.clear()
        self._titulo_cheio = "Escolha um episódio na lista"
        self._elide_titulo()
        self.meta.setText("")
        self.player_info.setText("")
        self.btn_open.setEnabled(False)
        for b in (self.btn_merge, self.btn_move, self.btn_drop):
            b.setEnabled(False)
        if self.grid is not None:
            try:
                self.grid.shot_activated.disconnect()
                self.grid.shot_action.disconnect()
                self.grid.list.itemSelectionChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.grid.setParent(None)
            self.grid.deleteLater()
            self.grid = None

    def _menu_personagem(self, pos) -> None:
        """Botão direito numa pílula: tirar o personagem do episódio inteiro
        (é o "apagar a pasta dele" que o Explorer faria, mas com o banco
        acompanhando e a decisão lembrada na reanálise)."""
        item = self.chars.itemAt(pos)
        nome = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not nome or self._episode is None or self._root is None:
            return
        menu = QMenu(self)
        acao = QAction(f"🗑  Tirar {nome} deste episódio", self)
        acao.triggered.connect(lambda: self._tirar_personagem(nome))
        menu.addAction(acao)
        menu.exec(self.chars.viewport().mapToGlobal(pos))

    def _tirar_personagem(self, nome: str) -> None:
        cid = self._char_id(nome)
        if cid is None or self._episode is None or self._root is None:
            return
        n = len(self.db.shots_for_character(cid, episode_id=self._episode["id"]))
        if quiet.question(
            self, "Tirar personagem do episódio",
            f"Tirar \"{nome}\" deste episódio?\n\n"
            f"• {n} cenas saem dele — a pasta real esvazia e some\n"
            "• O app LEMBRA: a reanálise não traz essas cenas de volta\n"
            "• Os clipes continuam em shots/ e nos outros personagens\n"
            "• As fotos de referência dele não são tocadas",
        ) != QMessageBox.StandardButton.Yes:
            return
        from ..curation import remove_character_from_episode
        remove_character_from_episode(
            self.db, self._episode["id"], cid, self._root,
            by_character=self.config.organize_by_character_enabled,
            by_pair=self.config.organize_by_pair_enabled,
        )
        if self._filtro == nome:
            self._filtro = None
        self._load_episode()

    @staticmethod
    def _abrir(pasta: Path) -> None:
        import os
        if pasta.exists():
            os.startfile(str(pasta))  # noqa: S606 (Windows)

    def _episode_root(self, title: str, season: int, episode: int) -> Path | None:
        """Onde a análise gravou este episódio. O nome da pasta vem do que o
        usuário DIGITOU, não do título oficial, então além do palpite direto
        procuramos qualquer pasta que tenha a temporada/episódio certos."""
        from ..storage.organizer import sanitize

        slug = f"S{season:02d}E{episode:02d}"
        direto = self.config.output_path / sanitize(title) / slug
        if direto.exists():
            return direto
        try:
            for pasta in self.config.output_path.iterdir():
                cand = pasta / slug
                if pasta.is_dir() and cand.exists():
                    return cand
        except OSError:
            pass
        return direto if direto.parent.exists() else None

    def _on_tree_selection(self) -> None:
        itens = self.tree.selectedItems()
        if not itens:
            return
        dados = itens[0].data(0, Qt.ItemDataRole.UserRole)
        if not dados:
            return
        self._episode = dados
        self._root = Path(dados["root"]) if dados["root"] else None
        self.btn_open.setEnabled(bool(dados["ok"]))
        self._stop_player()
        self.header.setText(
            f"{dados['title']} — S{dados['season']:02d}E{dados['episode']:02d}"
        )
        self._load_episode()

    # ---------- cenas e personagens ----------

    def _load_episode(self) -> None:
        if self._episode is None or self._root is None:
            return
        ep_id = self._episode["id"]
        shots = self.db.shots_for_episode(ep_id)
        by_shot = self.db.assignments_for_episode(ep_id)
        self._by_shot = by_shot

        # O filtro de personagem SOBREVIVE à recarga. Sem isto, remover uma
        # cena da pasta da Nina jogava a vista de volta pra "Todas" e o
        # usuário perdia o lugar exatamente enquanto limpava a pasta dela.
        anterior = self._filtro
        self._recarregando = True
        self.chars.clear()
        todos = QListWidgetItem()
        todos.setData(Qt.ItemDataRole.UserRole, None)
        todos.setData(_PILULA, ("📼", "Todas", str(len(shots))))
        self.chars.addItem(todos)
        contagem: dict[str, int] = {}
        for ass in by_shot.values():
            for a in ass:
                contagem[a["name"]] = contagem.get(a["name"], 0) + 1
        alvo = 0
        for i, (nome, n) in enumerate(
            sorted(contagem.items(), key=lambda kv: -kv[1]), start=1
        ):
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, nome)
            it.setData(_PILULA, ("", nome, str(n)))
            self.chars.addItem(it)
            if nome == anterior:
                alvo = i
        self.chars.setCurrentRow(alvo)
        self._filtro = self.chars.item(alvo).data(Qt.ItemDataRole.UserRole)
        self._recarregando = False
        self._ajustar_altura_pills()

        if self.grid is not None:
            # Desligar os sinais ANTES de destruir: sem isso o Qt ainda
            # entrega uma mudança de seleção do widget que já morreu e o
            # app estoura com "Signal source has been deleted".
            try:
                self.grid.shot_activated.disconnect()
                self.grid.shot_action.disconnect()
                self.grid.list.itemSelectionChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.grid.setParent(None)
            self.grid.deleteLater()
        self.grid = ShotGrid(self._root)
        self.grid.set_header_visible(False)     # contagem e ordem vivem no topo
        self.grid.set_sort_mode(self.sort_box.currentData() or "idx")
        self.grid.shot_activated.connect(self._play_shot)
        # O menu do botão direito da grade — remover, mover, aprovar, juntar.
        # Sem esta linha ele emitia pro vazio: o menu abria, a opção clicava,
        # e nada acontecia.
        self.grid.shot_action.connect(self._handle_shot_action)
        self.grid.list.itemSelectionChanged.connect(self._on_grid_selection)
        self._grid_layout.addWidget(self.grid)
        quem = {
            sid: ", ".join(_primeiro_nome(a["name"]) for a in ass)
            for sid, ass in by_shot.items() if ass
        }
        # cena que veio de uma junção ganha ⛓ no cartão (as junções vêm do
        # banco UMA vez — consultar dentro do laço era uma ida ao banco por
        # cena, 332 delas só pra desenhar a grade)
        juncoes = self.db.shot_merges(ep_id)
        for r in shots:
            r["merged"] = any(
                m["start"] - 0.05 <= float(r["start"]) and float(r["end"]) <= m["end"] + 0.05
                for m in juncoes
            )
        self._shots = shots
        self._titulo_cheio = (
            f"{self._episode['title']} — "
            f"S{self._episode['season']:02d}E{self._episode['episode']:02d}"
        )
        self._elide_titulo()
        dur = max((float(r["end"]) for r in shots), default=0.0)
        # Números em âmbar (é a cor de medida deste app); as DUVIDOSAS em
        # vermelho, porque é a única contagem que pede ação.
        duvidosas = sum(
            1 for ass in by_shot.values()
            for a in ass
            if a.get("confidence") is not None and float(a["confidence"]) < 0.80
        )
        t = f"color:{theme.TIME}"
        partes = [
            f"<span style='{t}'>{len(shots)}</span> cenas",
            f"<span style='{t}'>{len(contagem)}</span> personagens",
            f"<span style='{t}'>{_mmss_curto(dur)}</span>",
        ]
        if duvidosas:
            partes.append(
                f"<span style='color:{theme.DANGER}'>{duvidosas}</span> duvidosas"
            )
        self.meta.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(partes))
        self._aplicar_filtro()

    def _ajustar_altura_pills(self) -> None:
        """Altura da fileira de personagens MEDIDA, não chutada: a altura de
        uma pílula de verdade × no máximo duas fileiras. Elenco maior que
        isso rola dentro da faixa em vez de empurrar a grade de cenas pra
        baixo — e nenhuma fileira aparece cortada pela metade."""
        if not self.chars.count():
            return
        alt = self.chars.sizeHintForRow(0)
        if alt <= 0:
            return
        e = self.chars.spacing()
        # UMA fileira; a barra de rolagem horizontal entra por baixo quando
        # o elenco não cabe.
        novo = alt + 2 * e + 4
        if self.chars.horizontalScrollBar().isVisible():
            novo += self.chars.horizontalScrollBar().height()
        if novo != self.chars.height():   # só mexe se mudou (evita laço)
            self.chars.setFixedHeight(novo)

    def _on_char_filter(self) -> None:
        if self._recarregando:
            return
        itens = self.chars.selectedItems()
        self._filtro = itens[0].data(Qt.ItemDataRole.UserRole) if itens else None
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        """Põe na grade o que o filtro atual pede — o episódio inteiro ou as
        cenas de um personagem."""
        if self._episode is None or self.grid is None:
            return
        ep_id = self._episode["id"]
        quem = {
            sid: ", ".join(_primeiro_nome(a["name"]) for a in ass)
            for sid, ass in self._by_shot.items() if ass
        }
        if self._filtro is None:
            self.grid.load_for_character(
                self._shots, "Episódio inteiro", who_by_shot=quem
            )
            return
        cid = self._char_id(self._filtro)
        if cid is None:
            return
        cenas = self.db.shots_for_character(cid, episode_id=ep_id)
        juntadas = {int(r["id"]): r.get("merged") for r in self._shots}
        for r in cenas:
            r["merged"] = juntadas.get(int(r["id"]), False)
        self.grid.load_for_character(cenas, self._filtro, who_by_shot=quem)

    # ---------- player ----------

    def _on_grid_selection(self) -> None:
        """Um clique já toca — sem precisar de duplo clique."""
        if self.grid is None:
            return
        itens = self.grid.list.selectedItems()
        if len(itens) == 1:
            dados = itens[0].data(Qt.ItemDataRole.UserRole)
            if dados:
                self._play_shot(dados)

    def _play_shot(self, row: dict) -> None:
        if self._root is None or not row.get("file"):
            return
        clipe = self._root / row["file"]
        if not clipe.exists():
            self._stop_player()
            self.player.setText("O arquivo dessa cena não está mais na pasta")
            return
        ini = float(row.get("start") or 0)
        fim = float(row.get("end") or 0)
        conf = row.get("confidence")
        donos = self._by_shot.get(int(row["id"]), [])
        quem = [a["name"] for a in donos]
        linhas = [
            ("cena", f"#{int(row['idx']):04d}", theme.TXT),
            ("tempo", f"{_mmss(ini)} → {_mmss(fim)}", theme.TIME),
            ("duração", f"{fim - ini:.1f}s", theme.TIME),
            ("quem", ", ".join(quem) if quem else "—",
             theme.TXT if quem else theme.TXT_GHOST),
        ]
        confs = [a["confidence"] for a in donos if a.get("confidence") is not None]
        if confs:
            # Verde = evidência boa; âmbar quando alguma está morna. Cor de
            # estado aqui vale mais que o número: diz se dá pra confiar.
            linhas.append((
                "confiança", " / ".join(f"{c:.2f}" for c in confs),
                theme.OK if min(confs) >= 0.80 else theme.TIME,
            ))
        self.player_info.setText(
            "<table cellspacing='0' cellpadding='0'>"
            + "".join(
                f"<tr><td width='66' style='color:{theme.TXT_FAINT}'>{r}</td>"
                f"<td style='color:{c}'>{v}</td></tr>"
                f"<tr><td colspan='2' height='7'></td></tr>"
                for r, v, c in linhas
            )
            + "</table>"
        )
        self._current_shot = row
        # As ações por personagem valem na vista "Todas" TAMBÉM. Antes elas
        # exigiam um filtro ligado: na vista do episódio inteiro — que é onde
        # se passa a maior parte do tempo — os botões ficavam apagados e
        # clicar não fazia nada. Agora a ação mira o personagem da própria
        # cena (e pergunta qual, se houver mais de um).
        tem_dono = bool(donos) or self._filtro is not None
        self.btn_merge.setEnabled(True)
        self.btn_move.setEnabled(tem_dono)
        self.btn_drop.setEnabled(tem_dono)
        self.btn_drop.setText(
            f"⤫   Remover de {_primeiro_nome(self._filtro)}"
            if self._filtro else "⤫   Remover desta pasta"
        )
        dica = "" if tem_dono else "Esta cena não tem personagem identificado."
        self.btn_move.setToolTip(dica)
        self.btn_drop.setToolTip(dica)
        self._stop_player()
        self.player.setText("carregando…")
        self._pending = str(clipe)
        self._pool.start(_LoadClip(clipe, self._bridge))

    def _on_clip_ready(self, path: str, frames: list) -> None:
        # O usuário pode ter clicado em outra cena enquanto esta carregava.
        if path != self._pending:
            return
        if not frames:
            self.player.setText("Não consegui ler esse clipe")
            return
        self._frames = frames
        self._frame_i = 0
        self._timer.start()
        self.loop_pill.adjustSize()
        self._posicionar_pill()
        self.loop_pill.show()

    def _posicionar_pill(self) -> None:
        self.loop_pill.move(
            max(6, self.player.width() - self.loop_pill.width() - 8), 8
        )

    def _elide_titulo(self) -> None:
        larg = max(120, self.header.width())
        self.header.setToolTip(self._titulo_cheio)
        self.header.setText(
            QFontMetrics(self.header.font()).elidedText(
                self._titulo_cheio, Qt.TextElideMode.ElideRight, larg
            )
        )

    # Abaixo desta largura o painel da cena se recolhe: 248 + 320 fixos numa
    # janela de 980 deixam a grade com UMA coluna, e a grade é o conteúdo.
    LARGURA_CENA_SOME = 1180

    def _ajustar_colunas(self) -> None:
        # Sem guardar em `isVisible()`: durante a construção o painel ainda
        # não foi mostrado, então comparar com ele fazia a regra concluir
        # "já está do jeito certo" e nunca aplicar nada. setVisible com o
        # mesmo valor não custa nada — o estado desejado é que manda.
        self._painel_cena.setVisible(
            self.width() >= self.LARGURA_CENA_SOME or self._cena_forcada
        )
        # Coluna do meio apertada: o trilho de ordem fica só com os ícones.
        self.sort_box.compactar(self.grid_box.width() < 620)

    def showEvent(self, event) -> None:   # noqa: N802 (API Qt)
        super().showEvent(event)
        self._ajustar_colunas()

    def alternar_painel_cena(self) -> None:
        """Ctrl+P: traz o painel de volta numa janela estreita (ou o esconde
        pra dar espaço à grade numa larga)."""
        self._cena_forcada = not self._painel_cena.isVisible()
        self._painel_cena.setVisible(self._cena_forcada)

    def resizeEvent(self, event) -> None:   # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._ajustar_altura_pills()
        self._elide_titulo()
        self._ajustar_colunas()

    def _tick(self) -> None:
        if not self._frames:
            self._timer.stop()
            return
        i = self._frame_i % len(self._frames)
        img = self._frames[i]
        pm = QPixmap.fromImage(img)
        # Os quadros são decodificados pequenos (320px) pra caberem na
        # memória; aqui eles ocupam a tela inteira, sem deformar e sempre
        # centralizados (o QLabel está com AlignCenter).
        if pm.size() != self.player.size():
            pm = pm.scaled(
                self.player.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.player.setPixmap(pm)
        frac = (i + 1) / len(self._frames)
        self.loop_bar.setGeometry(
            1, self.player.height() - 4, int((self.player.width() - 2) * frac), 3
        )
        self.loop_bar.show()
        self._frame_i += 1

    def _stop_player(self) -> None:
        self._timer.stop()
        self._frames = []
        self._frame_i = 0
        self._pending = None
        self.loop_pill.hide()
        self.loop_bar.hide()
        self.player.setPixmap(QPixmap())
        self.player.setText("Clique numa cena\npra ela tocar aqui, em loop")

    def _on_sort(self) -> None:
        if self.grid is not None:
            self.grid.set_sort_mode(self.sort_box.currentData() or "idx")

    def _passo_cena(self, passo: int) -> None:
        """←/→ andam pela grade sem tirar a mão do teclado."""
        if self.grid is None or not self.grid.list.count():
            return
        atual = self.grid.list.currentRow()
        novo = max(0, min(self.grid.list.count() - 1, atual + passo))
        if novo != atual:
            self.grid.list.setCurrentRow(novo)

    def _char_id(self, nome: str) -> int | None:
        """Id do personagem DESTE anime. Sem o vínculo com o anime, um
        homônimo de outra série (o acervo tem vários) podia ser escolhido —
        e a cena ia parar na pasta do anime errado."""
        if self._episode is None:
            return None
        with self.db.connect() as c:
            row = c.execute(
                "SELECT c.id FROM character c JOIN episode e ON e.anime_id = c.anime_id "
                "WHERE e.id = ? AND c.name = ? LIMIT 1",
                (self._episode["id"], nome),
            ).fetchone()
        return int(row["id"]) if row else None

    def _elenco_do_anime(self) -> list[str]:
        """Todo o elenco conhecido deste anime — não só quem já aparece no
        episódio. Mover uma cena pra alguém que o app ainda não achou aqui é
        justamente o caso em que ele errou."""
        if self._episode is None:
            return []
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT c.name FROM character c JOIN episode e ON e.anime_id = c.anime_id "
                "WHERE e.id = ? ORDER BY c.name",
                (self._episode["id"],),
            ).fetchall()
        return [r["name"] for r in rows]

    def _alvo_da_acao(self, row: dict, acao: str) -> tuple[int, str] | None:
        """Em qual pasta a ação bate: a filtrada, se houver; senão a do
        personagem da cena — perguntando qual quando há mais de um."""
        if self._filtro:
            cid = self._char_id(self._filtro)
            return (cid, self._filtro) if cid is not None else None
        donos = self._by_shot.get(int(row["id"]), [])
        if not donos:
            return None
        if len(donos) == 1:
            return int(donos[0]["id"]), donos[0]["name"]
        nomes = [d["name"] for d in donos]
        escolha, ok = QInputDialog.getItem(
            self,
            "Esta cena está em mais de uma pasta",
            "Remover de qual?" if acao == "remove" else "Mover a partir de qual?",
            nomes, 0, False,
        )
        if not ok or not escolha:
            return None
        cid = self._char_id(escolha)
        return (cid, escolha) if cid is not None else None

    def _scene_action(self, acao: str) -> None:
        """Os botões do painel da cena entram pelo MESMO portão do menu do
        botão direito — uma ação, um caminho."""
        row = getattr(self, "_current_shot", None)
        if row is not None:
            self._handle_shot_action(acao, [row])

    def _handle_shot_action(self, acao: str, rows: list) -> None:
        """Curar sem sair da Biblioteca.

        Este é o portão ÚNICO das ações, e é o que faltava ligar: o menu do
        botão direito da grade emitia `shot_action` pra ninguém aqui — clicar
        em "Juntar com a próxima cena" pelo menu não fazia absolutamente
        nada, enquanto o botão ao lado, que chamava outro código, funcionava.
        Duas portas pra mesma ação é como uma delas fica quebrada sem
        ninguém perceber.
        """
        if not rows or self._episode is None or self._root is None:
            return
        ep_id = self._episode["id"]

        if acao in ("merge", "merge_next", "unmerge"):
            self._juntar(acao, rows)
            return

        if acao == "approve":
            for r in rows:
                donos = self._by_shot.get(int(r["id"]), [])
                for d in donos:
                    if self._filtro in (None, d["name"]):
                        self.db.set_assignment_review(int(r["id"]), int(d["id"]), True)
            self._load_episode()
            return

        escolhido = self._alvo_da_acao(rows[0], acao)
        if escolhido is None:
            quiet.information(
                self, "Sem personagem",
                "Esta cena não está na pasta de ninguém — não há de onde "
                "remover nem de onde mover.",
            )
            return
        cid, nome = escolhido

        alvo_id = None
        if acao == "move":
            outros = [n for n in self._elenco_do_anime() if n != nome]
            if not outros:
                quiet.information(
                    self, "Sem destino",
                    "Não há outro personagem deste anime pra receber a cena.",
                )
                return
            alvo, ok = QInputDialog.getItem(
                self, "Mover cena", f"Tirar de {nome} e passar para:", outros, 0, False
            )
            if not ok or not alvo:
                return
            alvo_id = self._char_id(alvo)
            if alvo_id is None:
                return
        elif len(rows) > 1:
            # Selecionou várias e mandou remover: confirma, porque some da
            # pasta de uma vez só.
            if quiet.question(
                self, "Remover cenas",
                f"Tirar {len(rows)} cenas da pasta de {nome}?\n\n"
                "• Os clipes continuam em shots/ (nada é apagado de verdade)\n"
                "• O app LEMBRA: a reanálise não devolve essas cenas pra ele",
            ) != QMessageBox.StandardButton.Yes:
                return

        for r in rows:
            self.db.remove_shot_character(int(r["id"]), cid)
            self.db.record_manual(ep_id, int(r["idx"]), cid, "block")
            if alvo_id is not None:
                self.db.assign_character_manual(int(r["id"]), alvo_id, 1.0)
                self.db.record_manual(ep_id, int(r["idx"]), alvo_id, "add", 1.0)
            # as pastas reais acompanham na hora
            nomes = [a["name"] for a in self.db.characters_in_shot(int(r["id"]))]
            try:
                refresh_shot_links(
                    self._root, self._root / r["file"], nomes,
                    by_character=self.config.organize_by_character_enabled,
                    by_pair=self.config.organize_by_pair_enabled,
                )
            except Exception:
                pass
        self._stop_player()
        self._load_episode()

    def _juntar(self, acao: str, rows: list) -> None:
        """Juntar cenas vizinhas num clipe só (ou desfazer a junção). Mexe
        nas CENAS, não em quem aparece nelas — vale em qualquer vista."""
        ep_id = self._episode["id"]

        if acao == "unmerge":
            meio = (float(rows[0]["start"]) + float(rows[0]["end"])) / 2.0
            if self.db.remove_shot_merge(ep_id, meio):
                quiet.information(
                    self, "Junção desfeita",
                    "Essa junção não vale mais. As cenas voltam separadas na "
                    "próxima análise deste episódio (o clipe atual continua "
                    "juntado até lá).",
                )
                self._load_episode()
            else:
                quiet.information(
                    self, "Nada a desfazer", "Essa cena não veio de uma junção."
                )
            return

        alvo = sorted(rows, key=lambda r: float(r["start"]))
        if acao == "merge_next":
            todas = sorted(self._shots, key=lambda r: float(r["start"]))
            pos = next(
                (i for i, r in enumerate(todas) if int(r["id"]) == int(alvo[0]["id"])),
                None,
            )
            if pos is None or pos + 1 >= len(todas):
                quiet.information(
                    self, "Sem próxima cena", "Essa é a última cena do episódio."
                )
                return
            alvo = [todas[pos], todas[pos + 1]]

        dur = sum(float(r["end"]) - float(r["start"]) for r in alvo)
        if quiet.question(
            self, "Juntar cenas",
            f"Juntar {len(alvo)} cenas num clipe só de {dur:.1f}s?\n\n"
            f"• Cenas #{int(alvo[0]['idx']):04d} a #{int(alvo[-1]['idx']):04d}\n"
            "• Vira um arquivo só, sem recodificar (rápido e sem perda)\n"
            "• O app LEMBRA: as próximas análises já saem juntadas\n"
            "• As outras cenas não mudam de número",
        ) != QMessageBox.StandardButton.Yes:
            return

        from ..curation import merge_shots
        novo = merge_shots(
            self.db, ep_id, alvo, self._root,
            keyframes_per_shot=self.config.keyframes_per_shot,
            by_character=self.config.organize_by_character_enabled,
            by_pair=self.config.organize_by_pair_enabled,
        )
        if novo is None:
            quiet.information(
                self, "Não deu pra juntar",
                "As cenas precisam ser VIZINHAS (uma logo depois da outra) e "
                "os clipes precisam existir na pasta shots/.",
            )
            return
        self._stop_player()
        self._load_episode()

    def _open_folder(self) -> None:
        if self._root and self._root.exists():
            import os
            os.startfile(str(self._root))  # noqa: S606 (Windows)
        else:
            quiet.information(
                self, "Pasta não encontrada",
                "A pasta desse episódio não está mais no lugar."
            )
