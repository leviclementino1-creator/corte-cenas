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

from PySide6.QtCore import Qt, QThreadPool, QTimer, QRunnable, QObject, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..storage.db import Database
from ..storage.organizer import refresh_shot_links
from . import quiet, theme
from .character_grid import ShotGrid, fill_sort_box


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


_PREVIEW_W = 320          # largura do player lateral
_PREVIEW_FPS = 12         # suave o bastante pra leitura, leve pra UI
_MAX_FRAMES = 96          # teto de memória por clipe (~8s a 12 fps)


class _Bridge(QObject):
    ready = Signal(str, list)   # (caminho do clipe, frames já em QImage)


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
        lv.setContentsMargins(10, 10, 6, 10)
        lv.setSpacing(6)
        lbl_acervo = QLabel("ACERVO")
        lbl_acervo.setStyleSheet(theme.label("eyebrow"))
        lv.addWidget(lbl_acervo)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # DUAS colunas: nome à esquerda, CONTAGEM à direita — episódio mostra
        # quantas cenas tem, anime/temporada quantos episódios. Era a
        # informação que a referência trazia e que aqui vinha empurrada pro
        # meio do rótulo, entre parênteses, competindo com o nome.
        self.tree.setColumnCount(2)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        # Sem coluna de ramo: era ali que o Qt desenhava as guias da
        # hierarquia na cor de destaque (as barras cianas). O triângulo de
        # abrir/fechar vira TEXTO no próprio rótulo (▾/▸) — mesma affordance,
        # desenhada por nós, sem a coluna que sujava a seleção.
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        cab = self.tree.header()
        cab.setStretchLastSection(False)
        cab.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        cab.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        # A árvore não é um "campo": ela É o painel. Sem caixa em volta, como
        # na referência — a divisória vertical do painel já separa.
        self.tree.setStyleSheet(
            f"QTreeWidget{{background:transparent;border:none;}}"
            f"QTreeWidget::item{{padding:6px 6px;border-radius:5px;}}"
        )
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.itemExpanded.connect(lambda it: self._marcar_seta(it, True))
        self.tree.itemCollapsed.connect(lambda it: self._marcar_seta(it, False))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu_acervo)
        lv.addWidget(self.tree, 1)

        lbl_acoes = QLabel("AÇÕES")
        lbl_acoes.setStyleSheet(theme.label("eyebrow"))
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
        split.addWidget(left)

        # --- meio: cenas + personagens
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(14, 10, 8, 8)
        mv.setSpacing(8)
        # TÍTULO e DADOS em linhas separadas. Amontoados na mesma linha
        # ("Mushoku Tensei III: … — S03E02 · 332 cenas · 6 personagens") o
        # nome do episódio e a contagem disputavam o mesmo olhar e nenhum dos
        # dois era lido. Título é nome; a linha de baixo é medida.
        self.header = QLabel("Escolha um episódio na lista")
        self.header.setStyleSheet(theme.label("title"))
        mv.addWidget(self.header)

        linha_meta = QHBoxLayout()
        linha_meta.setContentsMargins(0, 0, 0, 0)
        linha_meta.setSpacing(8)
        self.meta = QLabel("")
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.meta.setStyleSheet(
            f"font-family:{theme.MONO};font-size:11.5px;color:{theme.TXT_DIM};"
        )
        linha_meta.addWidget(self.meta)
        linha_meta.addStretch(1)
        lbl_ordem = QLabel("ordem:")
        lbl_ordem.setStyleSheet(theme.label("faint"))
        linha_meta.addWidget(lbl_ordem)
        # O seletor de ordem mora AQUI, na linha de dados, e não dentro da
        # grade: assim ele sobrevive à troca de episódio (a grade é
        # reconstruída a cada um) e a escolha do usuário não se perde.
        self.sort_box = QComboBox()
        fill_sort_box(self.sort_box)
        self.sort_box.currentIndexChanged.connect(self._on_sort)
        linha_meta.addWidget(self.sort_box)
        mv.addLayout(linha_meta)

        self.chars = QListWidget()
        self.chars.setFlow(QListWidget.Flow.LeftToRight)
        self.chars.setWrapping(True)
        self.chars.setResizeMode(QListWidget.ResizeMode.Adjust)
        # A altura é CALCULADA depois de encher (ver _ajustar_altura_pills).
        # Chutar 78px cortava a segunda fileira de pílulas exatamente no meio
        # da palavra — a caixa comendo o texto.
        self.chars.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chars.setSpacing(3)
        self.chars.setFrameShape(QListWidget.Shape.NoFrame)
        # PÍLULAS, como na referência: filtro de personagem é uma escolha
        # rápida entre poucos, não uma lista pra percorrer. Arredondado e
        # espaçado lê como "botão"; item de lista lê como "linha".
        self.chars.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;}}"
            f"QListWidget::item{{background:{theme.SURFACE_2};"
            f"border:1px solid {theme.LINE};border-radius:13px;"
            f"padding:5px 13px;margin:2px;color:{theme.TXT_DIM};}}"
            f"QListWidget::item:hover{{border-color:{theme.ACCENT_DARK};"
            f"color:{theme.TXT};}}"
            f"QListWidget::item:selected{{background:{theme.ACCENT_INK};"
            f"border-color:{theme.ACCENT_DARK};color:{theme.ACCENT};}}"
        )
        self.chars.itemSelectionChanged.connect(self._on_char_filter)
        self.chars.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chars.customContextMenuRequested.connect(self._menu_personagem)
        mv.addWidget(self.chars)
        self.grid_box = QWidget()
        self._grid_layout = QVBoxLayout(self.grid_box)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid: ShotGrid | None = None
        mv.addWidget(self.grid_box, 1)
        split.addWidget(mid)

        # --- direita: player em loop
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 10, 10, 10)
        rv.setSpacing(8)
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
        self.player.setStyleSheet(
            f"background:{theme.SURFACE_2};border:1px solid {theme.LINE};"
            f"border-radius:6px;color:{theme.TXT_FAINT};padding:10px;"
        )
        rv.addWidget(self.player)

        # Selo "em loop" POR CIMA da tela, como na referência: quem chega no
        # meio da sessão precisa saber que aquilo repete sozinho e não é um
        # quadro congelado.
        self.loop_pill = QLabel("▸ em loop", self.player)
        self.loop_pill.setStyleSheet(theme.chip("accent"))
        self.loop_pill.hide()

        self.player_info = QLabel("")
        self.player_info.setTextFormat(Qt.TextFormat.RichText)
        self.player_info.setWordWrap(True)
        self.player_info.setStyleSheet(
            f"font-family:{theme.MONO};font-size:11.5px;color:{theme.TXT_DIM};"
            f"padding:10px 2px;"
        )
        rv.addWidget(self.player_info)

        # Ações da cena — o que a referência prometia e faltava aqui: dá pra
        # curar sem sair da Biblioteca.
        self.btn_merge = QPushButton("⛓   Juntar com a próxima")
        self.btn_merge.setStyleSheet(theme.button("primary"))
        self.btn_merge.clicked.connect(lambda: self._scene_action("merge_next"))
        rv.addWidget(self.btn_merge)

        self.btn_move = QPushButton("↗   Mover pra outro personagem")
        self.btn_move.clicked.connect(lambda: self._scene_action("move"))
        rv.addWidget(self.btn_move)

        self.btn_drop = QPushButton("⤫   Remover desta pasta")
        self.btn_drop.setStyleSheet(theme.button("danger"))
        self.btn_drop.clicked.connect(lambda: self._scene_action("remove"))
        rv.addWidget(self.btn_drop)

        for b in (self.btn_merge, self.btn_move, self.btn_drop):
            b.setEnabled(False)
        rv.addStretch(1)
        split.addWidget(right)

        # A grade de cenas é o conteúdo; as colunas laterais servem a ela.
        # Com 240+700+424 numa janela de 1265 sobravam ~600px no meio = duas
        # colunas de miniatura, com a terceira cortada pela metade.
        # Esticar a janela dá espaço PRA GRADE (mais cenas por fileira); as
        # laterais têm largura de leitura e ficam onde estão. Os limites
        # existem pra ninguém arrastar a divisória até a grade sumir.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        left.setMinimumWidth(150)
        left.setMaximumWidth(320)
        right.setMinimumWidth(_PREVIEW_W - 40)
        right.setMaximumWidth(520)
        split.setSizes([196, 900, _PREVIEW_W + 24])
        split.setCollapsible(1, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(split)
        self.reload()

    # ---------- árvore ----------

    # Guardado no item pra o triângulo poder ser reescrito sem comer o rótulo.
    _ROTULO = Qt.ItemDataRole.UserRole + 1

    def _marcar_seta(self, item: QTreeWidgetItem, aberto: bool) -> None:
        base = item.data(0, self._ROTULO)
        if base:
            item.setText(0, ("▾  " if aberto else "▸  ") + base)

    def _no_ramo(self, rotulo: str, contagem: int) -> QTreeWidgetItem:
        """Nó que abre e fecha: triângulo + rótulo na coluna 0, contagem
        discreta na 1."""
        item = QTreeWidgetItem(["▾  " + rotulo, str(contagem)])
        item.setData(0, self._ROTULO, rotulo)
        item.setTextAlignment(
            1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        item.setForeground(1, QBrush(QColor(theme.TXT_FAINT)))
        item.setToolTip(0, rotulo)
        return item

    def reload(self) -> None:
        """(Re)monta anime → temporada → episódio a partir do banco."""
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
            no_anime = self._no_ramo(titulo, n_eps)
            for temp, eps in sorted(temporadas.items()):
                no_temp = self._no_ramo(f"Temporada {temp}", len(eps))
                for r in sorted(eps, key=lambda x: x["episode"]):
                    root = self._episode_root(r["title"], r["season"], r["episode"])
                    existe = root is not None and (root / "shots").exists()
                    label = f"Episódio {r['episode']:02d}"
                    no_ep = QTreeWidgetItem([
                        label if existe else f"{label}  (sumiu)",
                        str(n_cenas.get(int(r["id"]), 0)),
                    ])
                    if not existe:
                        no_ep.setToolTip(
                            0, "A pasta deste episódio não está mais no disco."
                        )
                    no_ep.setTextAlignment(
                        1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    no_ep.setForeground(1, QBrush(QColor(theme.TXT_FAINT)))
                    no_ep.setData(0, Qt.ItemDataRole.UserRole, {
                        "id": int(r["id"]), "title": r["title"],
                        "season": int(r["season"]), "episode": int(r["episode"]),
                        "root": str(root) if root else "", "ok": existe,
                    })
                    if not existe:
                        # Itálico apagado, não some da lista: quem apagou a
                        # pasta merece ver que apagou.
                        fonte = QFont()
                        fonte.setItalic(True)
                        no_ep.setFont(0, fonte)
                        no_ep.setForeground(0, QBrush(QColor(theme.TXT_FAINT)))
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
        self.header.setText("Escolha um episódio na lista")
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
        todos = QListWidgetItem(f"📼 Todas ({len(shots)})")
        todos.setData(Qt.ItemDataRole.UserRole, None)
        self.chars.addItem(todos)
        contagem: dict[str, int] = {}
        for ass in by_shot.values():
            for a in ass:
                contagem[a["name"]] = contagem.get(a["name"], 0) + 1
        alvo = 0
        for i, (nome, n) in enumerate(
            sorted(contagem.items(), key=lambda kv: -kv[1]), start=1
        ):
            it = QListWidgetItem(f"{nome} ({n})")
            it.setData(Qt.ItemDataRole.UserRole, nome)
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
        self.header.setText(
            f"{self._episode['title']} — "
            f"S{self._episode['season']:02d}E{self._episode['episode']:02d}"
        )
        dur = max((float(r["end"]) for r in shots), default=0.0)
        a = f"color:{theme.ACCENT}"
        self.meta.setText(
            f"<b style='{a}'>{len(shots)}</b> cenas &nbsp;·&nbsp; "
            f"<b style='{a}'>{len(contagem)}</b> personagens &nbsp;·&nbsp; "
            f"<b style='color:{theme.TIME}'>{_mmss_curto(dur)}</b>"
        )
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
        novo = 2 * (alt + 2 * e) + 4
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
        k = f"color:{theme.TXT_FAINT}"
        t = f"color:{theme.TIME}"
        self.player_info.setText(
            f"<span style='{k}'>cena</span> &nbsp;#{int(row['idx']):04d}<br>"
            f"<span style='{k}'>tempo</span> &nbsp;{_mmss(ini)} → {_mmss(fim)}<br>"
            f"<span style='{k}'>duração</span> &nbsp;<b style='{t}'>{fim - ini:.1f}s</b>"
            + (f" &nbsp;<span style='{k}'>conf</span> {conf:.2f}" if conf is not None else "")
            + f"<br><span style='{k}'>quem</span> &nbsp;"
            + (", ".join(quem) if quem else "—")
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

    def resizeEvent(self, event) -> None:   # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._ajustar_altura_pills()

    def _tick(self) -> None:
        if not self._frames:
            self._timer.stop()
            return
        img = self._frames[self._frame_i % len(self._frames)]
        pm = QPixmap.fromImage(img)
        # Os quadros são decodificados pequenos (320px) pra caberem na
        # memória; aqui eles acompanham o tamanho da tela, sem deformar.
        if pm.width() != self.player.width() or pm.height() > self.player.height():
            pm = pm.scaled(
                self.player.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.player.setPixmap(pm)
        self._frame_i += 1

    def _stop_player(self) -> None:
        self._timer.stop()
        self._frames = []
        self._frame_i = 0
        self._pending = None
        self.loop_pill.hide()
        self.player.setPixmap(QPixmap())
        self.player.setText("Clique numa cena\npra ela tocar aqui, em loop")

    def _on_sort(self) -> None:
        if self.grid is not None:
            self.grid.set_sort_mode(self.sort_box.currentData() or "idx")

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
