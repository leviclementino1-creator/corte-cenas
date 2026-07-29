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
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
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
from .character_grid import ShotGrid


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
    """Timecode m:ss.d — como qualquer programa de edição mostra."""
    s = max(0.0, float(segundos))
    return f"{int(s // 60)}:{s % 60:04.1f}"


_PREVIEW_W = 320          # largura do player lateral
_PREVIEW_FPS = 12         # suave o bastante pra leitura, leve pra UI
_MAX_FRAMES = 96          # teto de memória por clipe (~8s a 12 fps)


class _Bridge(QObject):
    ready = Signal(str, list)   # (caminho do clipe, frames já em QImage)


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

        self._bridge = _Bridge()
        self._bridge.ready.connect(self._on_clip_ready)
        self._pool = QThreadPool.globalInstance()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / _PREVIEW_FPS))
        self._timer.timeout.connect(self._tick)

        split = QSplitter(Qt.Orientation.Horizontal)

        # --- esquerda: árvore do acervo
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 4, 8)
        lbl_acervo = QLabel("ACERVO")
        lbl_acervo.setStyleSheet(theme.label("eyebrow"))
        lv.addWidget(lbl_acervo)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        # Sem coluna de ramo: era ali que o Qt desenhava as guias da
        # hierarquia na cor de destaque (as barras cianas). Aqui a hierarquia
        # já está escrita — "Temporada 3", "Episódio 02" —, então indentação
        # sozinha basta, e é o que a referência aprovada mostrava.
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        lv.addWidget(self.tree, 1)
        self.btn_reload = QPushButton("↻  Atualizar lista")
        self.btn_reload.clicked.connect(self.reload)
        lv.addWidget(self.btn_reload)
        self.btn_open = QPushButton("📂  Abrir pasta do episódio")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_folder)
        lv.addWidget(self.btn_open)
        split.addWidget(left)

        # --- meio: cenas + personagens
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(4, 8, 4, 8)
        self.header = QLabel("Escolha um episódio na lista")
        self.header.setStyleSheet(theme.label("title"))
        mv.addWidget(self.header)
        self.chars = QListWidget()
        self.chars.setFlow(QListWidget.Flow.LeftToRight)
        self.chars.setWrapping(True)
        self.chars.setResizeMode(QListWidget.ResizeMode.Adjust)
        # Altura pra DUAS fileiras de personagens. Com uma só, elenco de 6
        # já cortava a segunda linha no meio — o usuário via meia palavra e
        # achava que o app tinha bugado.
        self.chars.setMaximumHeight(78)
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
        rv.setContentsMargins(4, 8, 8, 8)
        cap = QLabel("A CENA")
        cap.setStyleSheet(theme.label("eyebrow"))
        rv.addWidget(cap)
        # A tela do clipe tem altura FIXA (proporção de vídeo). Antes ela
        # esticava pra ocupar a coluna inteira e o quadro ficava boiando no
        # meio de um painel vazio, com a informação empurrada pro rodapé.
        self.player = QLabel("Clique numa cena\npra ela tocar aqui, em loop")
        self.player.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.player.setFixedHeight(int(_PREVIEW_W * 9 / 16))
        self.player.setMinimumWidth(_PREVIEW_W)
        self.player.setStyleSheet(
            f"background:{theme.SURFACE_2};border:1px solid {theme.LINE};"
            f"border-radius:6px;color:{theme.TXT_FAINT};padding:10px;"
        )
        rv.addWidget(self.player)

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

        self.btn_drop = QPushButton("⤫   Remover deste personagem")
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
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([196, 900, _PREVIEW_W + 24])
        split.setCollapsible(1, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(split)
        self.reload()

    # ---------- árvore ----------

    def reload(self) -> None:
        """(Re)monta anime → temporada → episódio a partir do banco."""
        self.tree.clear()
        with self.db.connect() as c:
            rows = c.execute(
                """SELECT e.id, e.season, e.episode, e.source_file, a.title
                   FROM episode e JOIN anime a ON a.id = e.anime_id
                   ORDER BY a.title, e.season, e.episode"""
            ).fetchall()
        por_anime: dict[str, dict[int, list]] = {}
        for r in rows:
            por_anime.setdefault(r["title"], {}).setdefault(r["season"], []).append(r)

        for titulo, temporadas in por_anime.items():
            n_eps = sum(len(v) for v in temporadas.values())
            no_anime = QTreeWidgetItem([f"{titulo}  ({n_eps})"])
            no_anime.setFirstColumnSpanned(True)
            for temp, eps in sorted(temporadas.items()):
                no_temp = QTreeWidgetItem([f"Temporada {temp}  ({len(eps)})"])
                for r in sorted(eps, key=lambda x: x["episode"]):
                    root = self._episode_root(r["title"], r["season"], r["episode"])
                    existe = root is not None and (root / "shots").exists()
                    label = f"Episódio {r['episode']:02d}"
                    no_ep = QTreeWidgetItem([label if existe else f"{label}  (pasta sumiu)"])
                    no_ep.setData(0, Qt.ItemDataRole.UserRole, {
                        "id": int(r["id"]), "title": r["title"],
                        "season": int(r["season"]), "episode": int(r["episode"]),
                        "root": str(root) if root else "", "ok": existe,
                    })
                    if not existe:
                        no_ep.setForeground(0, Qt.GlobalColor.darkGray)
                    no_temp.addChild(no_ep)
                no_anime.addChild(no_temp)
            self.tree.addTopLevelItem(no_anime)
            no_anime.setExpanded(True)
        if not rows:
            self.tree.addTopLevelItem(
                QTreeWidgetItem(["Nada cortado ainda — analise um episódio primeiro"])
            )

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

        self.chars.clear()
        todos = QListWidgetItem(f"📼 Todas ({len(shots)})")
        todos.setData(Qt.ItemDataRole.UserRole, None)
        self.chars.addItem(todos)
        contagem: dict[str, int] = {}
        for ass in by_shot.values():
            for a in ass:
                contagem[a["name"]] = contagem.get(a["name"], 0) + 1
        for nome, n in sorted(contagem.items(), key=lambda kv: -kv[1]):
            it = QListWidgetItem(f"{nome} ({n})")
            it.setData(Qt.ItemDataRole.UserRole, nome)
            self.chars.addItem(it)
        self.chars.setCurrentRow(0)

        if self.grid is not None:
            # Desligar os sinais ANTES de destruir: sem isso o Qt ainda
            # entrega uma mudança de seleção do widget que já morreu e o
            # app estoura com "Signal source has been deleted".
            try:
                self.grid.shot_activated.disconnect()
                self.grid.list.itemSelectionChanged.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.grid.setParent(None)
            self.grid.deleteLater()
        self.grid = ShotGrid(self._root)
        self.grid.shot_activated.connect(self._play_shot)
        self.grid.list.itemSelectionChanged.connect(self._on_grid_selection)
        self._grid_layout.addWidget(self.grid)
        quem = {
            sid: ", ".join(_primeiro_nome(a["name"]) for a in ass)
            for sid, ass in by_shot.items() if ass
        }
        # cena que veio de uma junção ganha ⛓ no cartão
        for r in shots:
            r["merged"] = any(
                m["start"] - 0.05 <= float(r["start"]) and float(r["end"]) <= m["end"] + 0.05
                for m in self.db.shot_merges(ep_id)
            )
        self.grid.load_for_character(shots, "Episódio inteiro", who_by_shot=quem)
        self.header.setText(
            f"{self._episode['title']} — "
            f"S{self._episode['season']:02d}E{self._episode['episode']:02d}"
            f"   ·   {len(shots)} cenas   ·   {len(contagem)} personagens"
        )

    def _on_char_filter(self) -> None:
        if self._episode is None or self.grid is None:
            return
        itens = self.chars.selectedItems()
        if not itens:
            return
        nome = itens[0].data(Qt.ItemDataRole.UserRole)
        ep_id = self._episode["id"]
        if nome is None:
            self.grid.load_for_character(self.db.shots_for_episode(ep_id), "Episódio inteiro")
            return
        with self.db.connect() as c:
            row = c.execute(
                "SELECT c.id FROM character c JOIN shot_character sc ON sc.character_id=c.id "
                "JOIN shot s ON s.id=sc.shot_id WHERE s.episode_id=? AND c.name=? LIMIT 1",
                (ep_id, nome),
            ).fetchone()
        if row:
            self.grid.load_for_character(
                self.db.shots_for_character(int(row["id"]), episode_id=ep_id), nome
            )

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
        quem = [
            a["name"]
            for a in self.db.assignments_for_episode(self._episode["id"]).get(
                int(row["id"]), []
            )
        ]
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
        filtro = self._char_filter()
        self.btn_merge.setEnabled(True)
        self.btn_move.setEnabled(filtro is not None)
        self.btn_drop.setEnabled(filtro is not None)
        self.btn_drop.setText(
            f"⤫   Remover de {filtro}" if filtro else "⤫   Remover deste personagem"
        )
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

    def _tick(self) -> None:
        if not self._frames:
            self._timer.stop()
            return
        img = self._frames[self._frame_i % len(self._frames)]
        self.player.setPixmap(QPixmap.fromImage(img))
        self._frame_i += 1

    def _stop_player(self) -> None:
        self._timer.stop()
        self._frames = []
        self._frame_i = 0
        self._pending = None
        self.player.setPixmap(QPixmap())
        self.player.setText("Clique numa cena\npra ela tocar aqui, em loop")

    def _char_filter(self) -> str | None:
        """Personagem filtrado agora, se houver (as ações por personagem só
        fazem sentido quando existe um)."""
        itens = self.chars.selectedItems()
        return itens[0].data(Qt.ItemDataRole.UserRole) if itens else None

    def _char_id(self, nome: str) -> int | None:
        with self.db.connect() as c:
            row = c.execute(
                "SELECT id FROM character WHERE name = ? LIMIT 1", (nome,)
            ).fetchone()
        return int(row["id"]) if row else None

    def _scene_action(self, acao: str) -> None:
        """Curar sem sair da Biblioteca."""
        row = getattr(self, "_current_shot", None)
        if row is None or self._episode is None or self._root is None:
            return
        ep_id = self._episode["id"]

        if acao == "merge_next":
            todas = sorted(
                self.db.shots_for_episode(ep_id), key=lambda r: float(r["start"])
            )
            pos = next(
                (i for i, r in enumerate(todas) if int(r["id"]) == int(row["id"])), None
            )
            if pos is None or pos + 1 >= len(todas):
                quiet.information(self, "Sem próxima", "Essa é a última cena.")
                return
            par = [todas[pos], todas[pos + 1]]
            dur = sum(float(r["end"]) - float(r["start"]) for r in par)
            if quiet.question(
                self, "Juntar cenas",
                f"Juntar #{int(par[0]['idx']):04d} e #{int(par[1]['idx']):04d} "
                f"num clipe só de {dur:.1f}s?\n\n"
                "• Sem recodificar (rápido e sem perda)\n"
                "• O app lembra: as próximas análises já saem juntadas\n"
                "• As outras cenas não mudam de número",
            ) != QMessageBox.StandardButton.Yes:
                return
            from ..curation import merge_shots
            novo = merge_shots(
                self.db, ep_id, par, self._root,
                keyframes_per_shot=self.config.keyframes_per_shot,
                by_character=self.config.organize_by_character_enabled,
                by_pair=self.config.organize_by_pair_enabled,
            )
            if novo is None:
                quiet.information(
                    self, "Não deu pra juntar",
                    "As cenas precisam ser vizinhas e os clipes precisam existir.",
                )
                return
            self._stop_player()
            self._load_episode()
            return

        nome = self._char_filter()
        if not nome:
            return
        cid = self._char_id(nome)
        if cid is None:
            return

        if acao == "remove":
            self.db.remove_shot_character(int(row["id"]), cid)
            self.db.record_manual(ep_id, int(row["idx"]), cid, "block")
        elif acao == "move":
            outros = sorted(
                {a["name"] for ass in self.db.assignments_for_episode(ep_id).values()
                 for a in ass} - {nome}
            )
            if not outros:
                quiet.information(
                    self, "Sem destino",
                    "Não há outro personagem neste episódio pra receber a cena.",
                )
                return
            alvo, ok = QInputDialog.getItem(
                self, "Mover cena", "Passar esta cena para:", outros, 0, False
            )
            if not ok or not alvo:
                return
            alvo_id = self._char_id(alvo)
            if alvo_id is None:
                return
            self.db.remove_shot_character(int(row["id"]), cid)
            self.db.record_manual(ep_id, int(row["idx"]), cid, "block")
            self.db.assign_character_manual(int(row["id"]), alvo_id, 1.0)
            self.db.record_manual(ep_id, int(row["idx"]), alvo_id, "add", 1.0)

        # pastas reais acompanham na hora
        nomes = [
            a["name"]
            for a in self.db.assignments_for_episode(ep_id).get(int(row["id"]), [])
        ]
        try:
            refresh_shot_links(
                self._root, self._root / row["file"], nomes,
                by_character=self.config.organize_by_character_enabled,
                by_pair=self.config.organize_by_pair_enabled,
            )
        except Exception:
            pass
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
