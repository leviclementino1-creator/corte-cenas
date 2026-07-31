"""A ponte entre o Chromium e o Python.

Duas peças, e as duas existem por um motivo medido:

1. `Ponte` — objeto exposto via QWebChannel. O JavaScript chama métodos
   Python de verdade (pedir as cenas do episódio, avisar que clicaram num
   cartão). Toda a lógica continua em Python; o JS só pede e desenha.

2. `ServidorMiniatura` — um esquema de URL próprio (`cena://`). Sem ele o
   HTML apontaria `file://` direto pros keyframes, e cada cartão mandaria o
   Chromium decodificar um JPEG 1080p de 246 KB pra desenhar 292x164. São
   331 cartões por episódio. O handler entrega a miniatura JÁ reduzida,
   reaproveitando o mesmo cache de disco do app.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal, Slot
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtWebEngineCore import (
    QWebEngineUrlRequestJob,
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
)

ESQUEMA = b"cena"
LARG_MINI = 292
ALT_MINI = 164
# O preset fala em nome curto ("threshold"); o Config guarda o nome longo
# ("default_threshold"). O diálogo Qt faz essa tradução na mão, campo a
# campo; aqui ela fica numa tabela só, pra não existirem dois lugares
# decidindo o que é "Auto".
_CAMPO_DO_PRESET = {
    "threshold": "default_threshold",
    "margin": "argmax_margin",
    "min_shots": "min_shots_per_character",
    "padding": "face_crop_padding",
    "credit": "credit_edge_threshold",
}
# Quantos quadros a prévia de hover mostra. Oito é o que o app usa hoje: dá
# pra ver a cena andar sem que a tira fique pesada (292x8 = 2336 px de largura).
QUADROS_TIRA = 8


def registra_esquema() -> None:
    """Precisa rodar ANTES de existir um QApplication — o Chromium tranca a
    lista de esquemas quando inicializa.

    `LocalAccessAllowed` deixa a página alcançar `file://` (os clipes .mp4 do
    <video> continuam vindo do disco direto, sem passar por aqui: o QBuffer
    não sabe responder pedido com Range, e sem Range o vídeo não busca)."""
    esq = QWebEngineUrlScheme(ESQUEMA)
    esq.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
    )
    esq.setSyntax(QWebEngineUrlScheme.Syntax.Path)
    QWebEngineUrlScheme.registerScheme(esq)


class ServidorMiniatura(QWebEngineUrlSchemeHandler):
    """O servidor do app. Responde três coisas em `cena:/`:

        /pagina           o HTML da interface
        /qwebchannel.js   a biblioteca da ponte (vem embutida no Qt)
        /mini/<caminho>   o keyframe JÁ reduzido pra 292x164

    A página é servida por AQUI, e não por `file://`, de propósito: assim ela
    e as miniaturas têm a mesma origem. Com a página em `file://` o Chromium
    barrava todo pedido `cena:` antes de chegar no Python — as 331 imagens
    "carregavam" em 200 ms porque todas falhavam de uma vez.
    """

    def __init__(self, pagina: Path, parent=None) -> None:
        super().__init__(parent)
        self.pagina = pagina
        # Os crops do batismo chegam como BYTES na memória, não arquivos — o
        # clustering monta os grupos e nada é gravado até o usuário nomear.
        # Escrever num temporário só pra poder mostrar seria trabalho e lixo
        # à toa: a ponte deixa os bytes aqui e o servidor entrega direto.
        self.grupos: dict[str, bytes] = {}
        self.cache: dict[str, QByteArray] = {}
        self.servidas = 0
        self.decodificadas = 0
        self.falhas = 0
        self.gasto = 0.0

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
        caminho = job.requestUrl().path()
        self.servidas += 1

        if caminho in ("/pagina", "/", ""):
            self._responde(job, b"text/html", self.pagina.read_bytes())
            return

        if caminho == "/qwebchannel.js":
            from PySide6.QtCore import QFile

            f = QFile(":/qtwebchannel/qwebchannel.js")
            f.open(QIODevice.OpenModeFlag.ReadOnly)
            self._responde(job, b"application/javascript", bytes(f.readAll()))
            f.close()
            return

        if caminho.startswith("/grupo/"):
            dados = self.grupos.get(caminho[len("/grupo/") :])
            if dados is None:
                self.falhas += 1
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            self._responde(job, b"image/jpeg", dados)
            return

        if caminho.startswith("/tira/"):
            alvo = caminho[len("/tira/") :]
            dados = self.cache.get("t:" + alvo)
            if dados is None:
                t0 = time.perf_counter()
                dados = self._tira(alvo)
                self.gasto += time.perf_counter() - t0
                self.decodificadas += 1
                if dados is None:
                    self.falhas += 1
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                self.cache["t:" + alvo] = dados
            self._responde(job, b"image/jpeg", dados)
            return

        if caminho.startswith("/mini/"):
            alvo = caminho[len("/mini/") :]
            dados = self.cache.get(alvo)
            if dados is None:
                t0 = time.perf_counter()
                dados = self._reduz(alvo)
                self.gasto += time.perf_counter() - t0
                self.decodificadas += 1
                if dados is None:
                    self.falhas += 1
                    job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                    return
                self.cache[alvo] = dados
            self._responde(job, b"image/jpeg", dados)
            return

        self.falhas += 1
        job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)

    def _responde(self, job: QWebEngineUrlRequestJob, tipo: bytes, dados) -> None:
        buf = QBuffer(job)
        buf.setData(QByteArray(dados) if not isinstance(dados, QByteArray) else dados)
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(tipo, buf)

    def _tira(self, clipe: str) -> QByteArray | None:
        """Uma tira horizontal com N quadros do clipe, num JPEG só.

        É o que faz a prévia estilo YouTube: o mouse anda pelo cartão e a
        imagem anda junto. O app de hoje faz o mesmo com o `_StripJob`,
        cíclando QImages; aqui vira UMA imagem e o CSS escolhe o quadro com
        `background-position` — sem timer, sem thread, sem redesenhar nada.
        """
        import cv2  # local: o import do cv2 é caro e nem todo hover precisa

        p = Path(clipe)
        if not p.exists():
            return None
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            cap.release()
            return None

        quadros = []
        for k in range(QUADROS_TIRA):
            # espalha os quadros pelo clipe, sem pegar o primeiro nem o último
            pos = int(total * (k + 0.5) / QUADROS_TIRA)
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(pos, total - 1))
            ok, quadro = cap.read()
            if not ok:
                continue
            quadro = cv2.resize(quadro, (LARG_MINI, ALT_MINI), interpolation=cv2.INTER_AREA)
            quadros.append(quadro)
        cap.release()
        if not quadros:
            return None

        import numpy as np

        tira = np.hstack(quadros)
        ok, buf = cv2.imencode(".jpg", tira, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
        if not ok:
            return None
        return QByteArray(buf.tobytes())

    def _reduz(self, caminho: str) -> QByteArray | None:
        p = Path(caminho)
        if not p.exists():
            return None
        # mesma técnica do app: o JPEG é decodificado JÁ pequeno
        leitor = QImageReader(str(p))
        tam = leitor.size()
        if tam.isValid():
            from PySide6.QtCore import QSize, Qt

            leitor.setScaledSize(
                tam.scaled(QSize(LARG_MINI, ALT_MINI), Qt.AspectRatioMode.KeepAspectRatio)
            )
        img: QImage = leitor.read()
        if img.isNull():
            return None
        saida = QByteArray()
        buf = QBuffer(saida)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPEG", 82)
        buf.close()
        return saida

    def resumo(self) -> str:
        kb = sum(len(v) for v in self.cache.values()) / 1024
        med = (self.gasto / self.decodificadas * 1000) if self.decodificadas else 0
        return (
            f"{self.servidas} pedidos · {self.decodificadas} decodificados "
            f"({med:.1f} ms cada) · cache {kb:,.0f} KB · {self.falhas} falhas"
        )


class Ponte(QObject):
    """O que o JavaScript enxerga do Python."""

    # Python -> JS. `progresso` tem a MESMA assinatura do
    # PipelineWorker.stage do app — (stage_id, fração, mensagem) — pra ligar
    # o worker de verdade ser um `connect`, não uma tradução.
    progresso = Signal(str, float, str)
    terminou = Signal(str)
    arquivoSolto = Signal(str)   # o episódio arrastado pra janela
    descobriu = Signal(str)      # DiscoveryResult -> tela de batismo
    dispositivo = Signal(str)    # GPU/CPU real, achado numa thread de fundo

    def __init__(self, banco: Path, raiz_saida: Path, ffmpeg: str = "ffmpeg",
                 parent=None, cfg=None) -> None:
        super().__init__(parent)
        self.banco = banco
        # A CONFIG VEM DE FORA. Antes `self.cfg` era uma property que fazia
        # `Config.load()` a cada acesso, e isso tem duas consequências ruins:
        #
        # 1. a Ponte IGNORAVA o Config que a janela recebeu — dois objetos
        #    dizendo o que é a pasta de cache, e ganhava o do arquivo;
        # 2. era impossível isolar a Ponte pra testar. `cache_dir` não está
        #    em _PERSISTED_FIELDS, então nem escrever um config.json de
        #    mentira adiantava: o load voltava com o cache DE VERDADE.
        #
        # O (2) me custou caro: um teste dos botões novos rodou
        # `limpar_fotos_baixadas` e `apagar_cache` no cache real do usuário
        # achando que estava numa fixture. Deu pra recuperar só porque o
        # `wipe_cache` move pra `cache_lixeira` em vez de destruir — a regra
        # de soft-delete da casa pagou o próprio preço naquele minuto.
        self._cfg = cfg
        # o servidor publica os crops do batismo, que só existem em memória
        self.servidor = None
        self._descoberta = None
        # `raiz` é a pasta do episódio ABERTO — muda quando o usuário escolhe
        # outro na árvore. Começa no que veio pronto pra não abrir vazio.
        self.raiz = raiz_saida
        self.ffmpeg = ffmpeg
        self.cliques: list[str] = []
        self.marcas: dict[str, float] = {}
        # Estado do episódio aberto, pra ação não ter que reconsultar o banco
        # só pra descobrir em qual cena ela bate.
        self.ep_id: int | None = None
        self.anime_id: int | None = None
        # último progresso, pra página puxar (ver `progresso_atual`)
        self._ultimo_progresso: tuple[str, float, str] = ("", 0.0, "")
        self._n_progresso = 0
        self._fim_pendente: dict | None = None
        self._batismo_pendente: str | None = None
        self._shots: dict[int, dict] = {}     # idx -> linha do shot
        self._donos: dict[int, list] = {}     # shot_id -> personagens

    @property
    def db(self):
        from app.storage.db import Database

        return Database(self.banco)

    @property
    def cfg(self):
        from app.config import Config

        if self._cfg is None:
            self._cfg = Config.load()
        return self._cfg

    @property
    def previas(self) -> Path:
        """Pasta das prévias WebM do episódio aberto. É propriedade e não
        atributo porque a raiz muda quando se troca de episódio — guardar o
        caminho no __init__ fazia a prévia do segundo episódio ir parar na
        pasta do primeiro."""
        return self.raiz / "metadata" / "previas_web"

    def _saida(self) -> Path:
        return Path(self.cfg.output_path)

    def _raiz_do_episodio(self, titulo: str, temporada: int, episodio: int) -> Path:
        """Mesma regra do app: o nome da pasta vem do que o usuário DIGITOU,
        não do título oficial, então além do palpite direto procura qualquer
        pasta que tenha a temporada/episódio certos.

        SEMPRE devolve um caminho, mesmo que a pasta não exista. Devolver None
        pra pasta sumida fazia a raiz FICAR NO EPISÓDIO ANTERIOR: abrir um
        episódio apagado continuava servindo as miniaturas do último aberto,
        sem nenhum aviso. Melhor apontar pra pasta certa e a imagem faltar."""
        from app.storage.organizer import sanitize

        slug = f"S{temporada:02d}E{episodio:02d}"
        saida = self._saida()
        direto = saida / sanitize(titulo) / slug
        if direto.exists():
            return direto
        try:
            for pasta in saida.iterdir():
                cand = pasta / slug
                if pasta.is_dir() and cand.exists():
                    return cand
        except OSError:
            pass
        return direto

    @Slot(result=str)
    def acervo(self) -> str:
        """A árvore anime → temporada → episódio, do banco."""
        con = sqlite3.connect(self.banco)
        con.row_factory = sqlite3.Row
        linhas = con.execute(
            """SELECT e.id, e.season, e.episode, a.title
                 FROM episode e JOIN anime a ON a.id = e.anime_id
                ORDER BY a.title, e.season, e.episode"""
        ).fetchall()
        # numa consulta só: uma por episódio deixaria a árvore lenta assim
        # que o acervo crescesse
        n_cenas = {
            int(r[0]): int(r[1])
            for r in con.execute("SELECT episode_id, COUNT(*) FROM shot GROUP BY episode_id")
        }
        con.close()

        # AGRUPA PELA PASTA, não pelo título do banco.
        #
        # No AniList/MAL cada temporada é um registro com título próprio
        # ("Mushoku Tensei III: Isekai Ittara Honki Dasu" é a T3, não a
        # série). Agrupar por ali punha o nome de UMA temporada como nome da
        # série inteira, e ainda partia o mesmo show em vários galhos.
        #
        # A pasta é melhor chave: é o nome que o usuário DIGITOU, é curto, é
        # o que ele vê no Explorer, e junta as temporadas que ele arquivou
        # junto. Quando a pasta sumiu do disco sobra o palpite (o nome que
        # ela teria), que é o melhor que dá pra fazer.
        # A BIBLIOTECA MOSTRA O QUE EXISTE. Episódio cuja pasta sumiu do disco
        # não aparece — biblioteca que lista o que não está lá mente sobre o
        # acervo. (A versão Qt mantinha em itálico "pra quem apagou ver que
        # apagou"; na prática vira lixo permanente na árvore.) O registro não
        # é apagado escondido: fica contado em `orfaos` e as AÇÕES oferecem a
        # limpeza.
        self.orfaos = []
        saida = self._saida()
        animes: dict[str, dict] = {}
        for r in linhas:
            raiz = self._raiz_do_episodio(r["title"], r["season"], r["episode"])
            existe = (raiz / "shots").exists()
            if not existe:
                self.orfaos.append(int(r["id"]))
                continue
            pasta = raiz.parent.name
            a = animes.setdefault(pasta, {
                "titulo": pasta,
                # o título oficial não se perde: vira a dica do nó
                "oficiais": set(),
                "temporadas": {},
            })
            a["oficiais"].add(r["title"])
            t = a["temporadas"].setdefault(r["season"], [])
            t.append({
                "id": int(r["id"]),
                "episodio": int(r["episode"]),
                "temporada": int(r["season"]),
                "titulo": r["title"],
                "cenas": n_cenas.get(int(r["id"]), 0),
                "ok": bool(existe),
            })

        fora = []
        for a in animes.values():
            temps = [
                {"temporada": t, "eps": sorted(eps, key=lambda e: e["episodio"]),
                 "cenas": sum(e["cenas"] for e in eps)}
                for t, eps in sorted(a["temporadas"].items())
            ]
            fora.append({
                "titulo": a["titulo"],
                "oficial": " · ".join(sorted(a["oficiais"])),
                "temporadas": temps,
                "cenas": sum(t["cenas"] for t in temps),
            })
        fora.sort(key=lambda x: x["titulo"].lower())
        # A pasta vai junto porque a Biblioteca É a pasta de saída: quando ela
        # aparece vazia, a primeira pergunta é "vazia ONDE?". E é o mesmo dado
        # que deixa o aviso de órfãos distinguir "apaguei a pasta" de "troquei
        # a pasta de saída" — que são coisas opostas com a mesma aparência.
        return json.dumps({
            "animes": fora,
            "orfaos": len(self.orfaos),
            "total": len(linhas),
            "saida": str(saida),
        })

    @Slot(result=str)
    def escolher_arquivo(self) -> str:
        """Diálogo de arquivo NATIVO, aberto pelo Qt.

        O `<input type=file>` do Chromium também abriria um seletor, mas o
        que ele devolve pro JavaScript é um objeto File sem caminho — e o
        ffmpeg precisa do caminho. Então quem abre é o Qt, e o caminho nunca
        sai do Python."""
        from PySide6.QtWidgets import QFileDialog

        caminho, _ = QFileDialog.getOpenFileName(
            None, "Escolher episódio", "", "Vídeo (*.mkv *.mp4 *.avi);;Tudo (*)"
        )
        return caminho or ""

    @Slot(str, result=str)
    def ler_arquivo(self, caminho: str) -> str:
        """Anime, temporada e episódio tirados do NOME do arquivo.

        Isto faltava, e era destruição de dado silenciosa. Os giros de T/E
        nasciam com 3 e 2 escritos no HTML — números da maquete — e nada na
        interface web chamava o `parse_filename`. Soltar
        "Mushoku Tensei S03E05.mkv" e clicar em Analisar gravava o episódio
        como S03E02: o `upsert_episode` casa por (anime, temporada, ep), acha
        o E02 que já existia, e o `clear_episode_shots` seguinte apagava os
        cortes DELE. O episódio certo era analisado por cima do errado.

        O app Qt sempre fez isto (analyze_tab.py:477). O parser é o mesmo,
        pra os dois lados errarem igual quando errarem.
        """
        from app.video_ingest import parse_filename

        p = Path(caminho)
        if not p.exists():
            return json.dumps({"ok": False})
        try:
            info = parse_filename(p)
        except Exception as e:  # noqa: BLE001
            print(f"    [python] não deu pra ler o nome de {p.name}: {e}")
            return json.dumps({"ok": False})
        print(f"    [python] {p.name} -> {info.anime!r} "
              f"S{info.season:02d}E{info.episode:02d}")
        return json.dumps({
            "ok": True,
            "anime": info.anime or "",
            "temporada": int(info.season),
            "episodio": int(info.episode),
        })

    @Slot(str, result=str)
    def estado_do_episodio(self, pedido: str) -> str:
        """Este episódio já foi analisado? Quantas cenas tem?

        A página pergunta ANTES de disparar qualquer coisa que apague
        identificação — reanálise, "só cortar" e Modo Descoberta passam os
        três por `clear_episode_shots`. O app Qt já perguntava na reanálise;
        a web disparava calada nos três casos.
        """
        d = json.loads(pedido)
        anime = (d.get("anime") or "").strip()
        try:
            temporada = int(d.get("temporada") or 1)
            episodio = int(d.get("episodio") or 1)
        except (TypeError, ValueError):
            return json.dumps({"tem_analise": False, "cenas": 0})
        if not anime:
            return json.dumps({"tem_analise": False, "cenas": 0})

        db = self.db
        try:
            tem = db.has_analysis(
                str(d.get("arquivo") or ""), anime, temporada, episodio)
        except Exception:  # noqa: BLE001
            tem = False
        cenas = 0
        try:
            with db.connect() as c:
                r = c.execute(
                    """SELECT COUNT(*) FROM shot s
                         JOIN episode e ON e.id = s.episode_id
                         JOIN anime a ON a.id = e.anime_id
                        WHERE a.title = ? COLLATE NOCASE
                          AND e.season = ? AND e.episode = ?""",
                    (anime, temporada, episodio),
                ).fetchone()
                cenas = int(r[0]) if r else 0
        except Exception:  # noqa: BLE001
            pass

        # ESPAÇO EM DISCO. Não havia checagem nenhuma no projeto inteiro
        # (zero ocorrências de disk_usage/ENOSPC): o disco enchia no meio do
        # corte, o ffmpeg falhava cena a cena, `cut_all_shots` filtrava os
        # None e a análise emitia `finished` — "pronto", com metade das cenas.
        #
        # A estimativa é grosseira de propósito: os clipes juntos dão mais ou
        # menos o tamanho do episódio (mesmo codec, mesmo bitrate), e os
        # keyframes somam pouco. Uma folga de 1,5x cobre o erro.
        aviso_disco = ""
        try:
            import shutil as _sh

            arq = Path(d.get("arquivo") or "")
            if arq.exists():
                preciso = int(arq.stat().st_size * 1.5)
                livre = _sh.disk_usage(self._saida()).free
                if livre < preciso:
                    aviso_disco = (
                        f"Sobram {livre / 2**30:.1f} GB em "
                        f"{self._saida().drive or self._saida()} e esta análise "
                        f"deve escrever perto de {preciso / 2**30:.1f} GB. "
                        f"Se o espaço acabar no meio, as cenas que faltarem "
                        f"não são cortadas e os clipes já feitos ficam onde "
                        f"estão.")
        except Exception:  # noqa: BLE001 — estimar não pode derrubar a análise
            pass

        # ARRANJO FEITO NO EXPLORER E AINDA NÃO SINCRONIZADO.
        #
        # A reanálise chama `clear_grouping`, que faz rmtree em
        # `by_character/` e `by_pair/` e reconstrói tudo pelo banco. Os
        # clipes não se perdem (são hardlinks, o mestre fica em shots/), mas
        # o arranjo que ainda não virou linha no banco some — e ninguém
        # avisava nem antes nem depois. Quem passou a tarde arrumando pastas
        # à mão e clicou em Analisar perdia a tarde.
        arranjo_solto = 0
        try:
            from app.storage.organizer import sanitize

            raiz = self._raiz_do_episodio(anime, temporada, episodio)
            by_char = raiz / "by_character"
            if by_char.exists():
                with db.connect() as c:
                    linha = c.execute(
                        """SELECT e.id FROM episode e JOIN anime a ON a.id = e.anime_id
                            WHERE a.title = ? COLLATE NOCASE
                              AND e.season = ? AND e.episode = ?""",
                        (anime, temporada, episodio)).fetchone()
                if linha:
                    eid = int(linha[0])
                    atribs = db.assignments_for_episode(eid)
                    # (pasta, arquivo) que o BANCO conhece
                    conhecidos = {
                        (sanitize(dono["name"]), Path(s["file"]).name)
                        for s in db.shots_for_episode(eid)
                        for dono in atribs.get(int(s["id"]), [])
                    }
                    for pasta in by_char.iterdir():
                        if not pasta.is_dir():
                            continue
                        for f in pasta.iterdir():
                            if f.is_file() and (pasta.name, f.name) not in conhecidos:
                                arranjo_solto += 1
        except Exception:  # noqa: BLE001 — contar não pode derrubar a análise
            arranjo_solto = 0

        return json.dumps({"tem_analise": bool(tem), "cenas": cenas,
                           "aviso_disco": aviso_disco,
                           "arranjo_solto": arranjo_solto})

    # ---- curadoria --------------------------------------------------------
    @Slot(str, result=str)
    def cenas_do_personagem(self, nome: str) -> str:
        """Quantas cenas este personagem tem NESTE episódio, e se a pasta
        dele existe no disco. As duas coisas divergem mais do que parece."""
        from app.storage.organizer import sanitize

        cid = self._id_do_personagem(nome)
        if cid is None or self.ep_id is None:
            return json.dumps({"ok": False, "msg": "personagem não encontrado"})
        with self.db.connect() as c:
            n = int(c.execute(
                """SELECT COUNT(*) FROM shot_character sc JOIN shot s
                     ON s.id = sc.shot_id
                    WHERE s.episode_id = ? AND sc.character_id = ?""",
                (self.ep_id, cid)).fetchone()[0])
        pasta = self.raiz / "by_character" / sanitize(nome)
        return json.dumps({
            "ok": True, "cenas": n,
            "pasta": str(pasta), "pasta_existe": pasta.exists(),
            "arquivos": len(list(pasta.glob("*.mp4"))) if pasta.exists() else 0,
        })

    @Slot(str, result=str)
    def remover_personagem(self, nome: str) -> str:
        """Tira o personagem do episódio INTEIRO.

        `curation.remove_character_from_episode` existe desde a v0.4.4 e a
        interface web nunca a chamou — então um personagem que o app inventou
        no episódio (72 cenas de alguém que nem aparece) não tinha como sair:
        remover cena a cena, 72 vezes, era o caminho.

        Cada cena vira bloqueio lembrado, os hardlinks são sincronizados e a
        pasta dele esvazia e some. Os clipes em shots/ e os outros
        personagens não são tocados.
        """
        from app.curation import remove_character_from_episode

        cid = self._id_do_personagem(nome)
        if cid is None or self.ep_id is None:
            return json.dumps({"ok": False, "msg": "personagem não encontrado"})
        try:
            n = remove_character_from_episode(
                self.db, self.ep_id, cid, self.raiz,
                by_character=self.cfg.organize_by_character_enabled,
                by_pair=self.cfg.organize_by_pair_enabled,
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"não deu: {e}"})
        msg = (f"{nome} saiu deste episódio ({n} cena(s)). A reanálise não "
               f"devolve — pra voltar atrás, mova alguma cena pra ele.")
        print(f"    [python] {msg}")
        return json.dumps({"ok": True, "msg": msg})

    @Slot(result=str)
    def elenco_do_anime(self) -> str:
        """Todo o elenco do anime, não só quem apareceu neste episódio — pra
        mover uma cena pra alguém que ainda não tem cena aqui."""
        if self.anime_id is None:
            return "[]"
        with self.db.connect() as c:
            nomes = [
                r["name"]
                for r in c.execute(
                    "SELECT name FROM character WHERE anime_id = ? ORDER BY name",
                    (self.anime_id,),
                )
            ]
        return json.dumps(nomes)

    def _id_do_personagem(self, nome: str) -> int | None:
        """Escopado no anime: dois animes podem ter uma Nina cada."""
        if self.anime_id is None:
            return None
        with self.db.connect() as c:
            r = c.execute(
                "SELECT id FROM character WHERE anime_id = ? AND name = ?",
                (self.anime_id, nome),
            ).fetchone()
        return int(r["id"]) if r else None

    @Slot(str, result=str)
    def acao_cena(self, pedido: str) -> str:
        """O portão ÚNICO das ações de curadoria.

        Mesma regra do app: o botão do painel e o menu do botão direito
        entram por aqui. Duas portas pra mesma ação é como uma delas fica
        quebrada sem ninguém perceber — foi exatamente o que aconteceu na
        versão Qt, onde o menu emitia um sinal que ninguém ouvia.

        Nada de lógica nova: chama o mesmo `Database` e o mesmo
        `refresh_shot_links` que a Biblioteca de hoje chama.
        """
        d = json.loads(pedido)
        acao = d.get("acao")
        idxs = [int(i) for i in d.get("idxs", [])]
        if self.ep_id is None or not idxs:
            return json.dumps({"ok": False, "msg": "nenhuma cena escolhida"})

        linhas = [self._shots[i] for i in idxs if i in self._shots]
        if not linhas:
            return json.dumps({"ok": False, "msg": "cena não encontrada"})

        if acao in ("juntar", "desjuntar"):
            return self._juntar(acao, linhas)

        de = d.get("de") or ""
        cid = self._id_do_personagem(de) if de else None

        alvo_id = None
        if acao == "mover":
            alvo_id = self._id_do_personagem(d.get("para") or "")
            if alvo_id is None:
                return json.dumps({"ok": False, "msg": "destino inválido"})

        # MOVER SEM ORIGEM é válido: é o caso da vista "sem identificação",
        # onde a cena não está na pasta de ninguém e o que se quer é DAR um
        # dono. Isto exigia `de` sempre e devolvia "não há de onde remover
        # nem de onde mover" — barrando justamente a curadoria mais útil
        # daquela tela. Remover sem origem continua sem sentido.
        if cid is None and acao != "mover":
            return json.dumps({
                "ok": False,
                "msg": "Esta cena não está na pasta de ninguém — não há de "
                       "onde remover.",
            })

        db = self.db
        from app.storage.organizer import refresh_shot_links

        for r in linhas:
            # sem origem (cena sem dono) não há o que tirar — só o que dar
            if cid is not None:
                db.remove_shot_character(int(r["id"]), cid)
                db.record_manual(self.ep_id, int(r["idx"]), cid, "block")
            if alvo_id is not None:
                db.assign_character_manual(int(r["id"]), alvo_id, 1.0)
                db.record_manual(self.ep_id, int(r["idx"]), alvo_id, "add", 1.0)
            # as pastas reais acompanham na hora — o clipe mestre continua em
            # shots/, quem vai e vem são os hardlinks
            nomes = [a["name"] for a in db.characters_in_shot(int(r["id"]))]
            try:
                refresh_shot_links(
                    self.raiz, self.raiz / r["file"], nomes,
                    by_character=self.cfg.organize_by_character_enabled,
                    by_pair=self.cfg.organize_by_pair_enabled,
                )
            except Exception as e:  # noqa: BLE001
                print(f"    [python] hardlinks de {r['idx']:04d}: {e}")

        quantas = len(linhas)
        # O recado ENSINA a volta. `record_manual(..., 'block')` é permanente:
        # a reanálise nunca mais devolve aquela cena pra aquele personagem, e
        # o único jeito de desfazer é mover a cena de volta na mão. Remover
        # uma cena cabe numa tecla (Del) e não passa por caixinha nenhuma —
        # então o que a pessoa lê depois é a única chance de ela descobrir
        # que a decisão foi lembrada, e como desdizê-la.
        alvos = ", ".join(f"#{int(r['idx']):04d}" for r in linhas[:3])
        if quantas > 3:
            alvos += f" +{quantas - 3}"
        if acao == "mover" and not de:
            msg = (f"{alvos}: agora é de {d.get('para')} · lembrado nas "
                   f"próximas análises")
        elif acao == "mover":
            msg = (f"{alvos}: {de} -> {d.get('para')} · lembrado nas próximas "
                   f"análises")
        else:
            msg = (f"{alvos} fora da pasta de {de} · o clipe fica em shots/ e "
                   f"a reanálise não devolve ele. Pra desfazer, mova de volta "
                   f"pro {de}.")
        print(f"    [python] {msg}")
        return json.dumps({"ok": True, "msg": msg})

    def _juntar(self, acao: str, linhas: list) -> str:
        """Juntar cenas vizinhas num clipe só (ou desfazer). Mexe nas CENAS,
        não em quem aparece nelas — vale em qualquer vista. Guardado em
        SEGUNDOS: número de cena anda quando a detecção muda, tempo não."""
        db = self.db
        r = linhas[0]
        if acao == "desjuntar":
            meio = (float(r["start"]) + float(r["end"])) / 2.0
            if db.remove_shot_merge(self.ep_id, meio):
                return json.dumps({"ok": True, "msg":
                    "Junção desfeita. As cenas voltam separadas na próxima "
                    "análise deste episódio."})
            return json.dumps({"ok": False, "msg": "Esta cena não faz parte de uma junção."})

        if len(linhas) >= 2:
            ini = min(float(x["start"]) for x in linhas)
            fim = max(float(x["end"]) for x in linhas)
        else:
            proxima = self._shots.get(int(r["idx"]) + 1)
            if proxima is None:
                return json.dumps({"ok": False, "msg": "Esta é a última cena do episódio."})
            ini, fim = float(r["start"]), float(proxima["end"])
        db.add_shot_merge(self.ep_id, ini, fim)
        return json.dumps({"ok": True, "msg":
            f"Junção marcada de {ini:.1f}s a {fim:.1f}s. Vale na próxima "
            "análise deste episódio."})

    @Slot(str, result=str)
    def apagar_do_acervo(self, pedido: str) -> str:
        """Tira episódios do acervo — a pasta vai pra LIXEIRA, não pro nada.

        Mesma lição do apagão de cache: um clique errado não pode custar
        horas de corte. `curation.enviar_para_lixeira` move pra
        `Output/_lixeira/<data>`; quem quiser o espaço de volta apaga na mão.
        O personagem, as fotos de referência e o que o app aprendeu não são
        tocados — isso é caro de refazer e não pertence a um episódio só.
        """
        from app.curation import enviar_para_lixeira

        ids = [int(i) for i in json.loads(pedido).get("ids", [])]
        if not ids:
            return json.dumps({"ok": False, "msg": "nada escolhido"})

        db = self.db
        saida = self._saida()
        movidas, apagados = 0, 0
        falhas: list[str] = []
        sobrou_copia = False
        with db.connect() as c:
            linhas = c.execute(
                """SELECT e.id, e.season, e.episode, a.title
                     FROM episode e JOIN anime a ON a.id = e.anime_id
                    WHERE e.id IN (%s)""" % ",".join("?" * len(ids)),
                ids,
            ).fetchall()

        for r in linhas:
            raiz = self._raiz_do_episodio(r["title"], r["season"], r["episode"])
            # O `delete_episode` estava FORA deste try, e isso desmentia a
            # caixinha: ela promete "a pasta vai pra _lixeira, não é apagada
            # de verdade", e quando o move falhava (arquivo aberto no
            # Explorer, no VLC, ou a própria prévia .webm em uso) o registro
            # sumia do mesmo jeito. Sobrava pasta órfã no disco e nenhuma
            # cena, nenhuma atribuição, nenhuma curadoria no banco.
            #
            # Agora falha é falha: nada é apagado e o usuário sabe o motivo.
            # É o que o app Qt já fazia (library_tab.py:948-961).
            try:
                if enviar_para_lixeira(raiz, saida) is not None:
                    movidas += 1
            except Exception as e:  # noqa: BLE001
                print(f"    [python] lixeira de {raiz}: {e}")
                falhas.append(f"{raiz.parent.name}/{raiz.name}")
                # o move de pasta não é atômico: a tentativa falha pode ter
                # deixado uma cópia na lixeira, e o usuário precisa saber
                # disso pra não confundir a cópia com um backup de algo que
                # ele conseguiu apagar
                if "cópia" in str(e):
                    sobrou_copia = True
                continue
            db.delete_episode(int(r["id"]))
            apagados += 1

        if self.ep_id in ids:
            self.ep_id = None
        aviso_copia = (
            " Uma cópia parcial ficou em Output/_lixeira — a original é a que "
            "vale, confira antes de apagar a cópia." if sobrou_copia else "")
        if falhas and not apagados:
            return json.dumps({"ok": False, "msg":
                "Não deu pra mover a pasta de " + ", ".join(falhas)
                + " (algum arquivo está aberto em outro programa). "
                "NADA foi apagado do acervo." + aviso_copia})
        msg = (f"{apagados} episódio(s) fora do acervo · "
               f"{movidas} pasta(s) na lixeira (Output/_lixeira)")
        if falhas:
            msg += (f" · {len(falhas)} ficaram como estavam "
                    f"({', '.join(falhas)}): a pasta está em uso" + aviso_copia)
        print(f"    [python] {msg}")
        return json.dumps({"ok": not falhas, "msg": msg})

    # ---- progresso --------------------------------------------------------
    @Slot(result=str)
    def etapas(self) -> str:
        """As etapas vêm de `app/pipeline_types.py`, não de uma lista escrita
        de novo no HTML — uma etapa nova no pipeline tem que aparecer na tela
        sozinha, sem ninguém lembrar de editar dois lugares."""
        from app.pipeline_types import STAGES

        return json.dumps([{"id": i, "rotulo": r} for i, r in STAGES])

    @Slot(str, result=str)
    def analisar(self, pedido: str) -> str:
        """Roda a análise DE VERDADE, com o mesmo PipelineWorker do app.

        O sinal do worker tem a mesma assinatura de `self.progresso`, então
        ligar é um `connect` — nenhuma tradução no meio, nenhum risco de a
        interface e o pipeline discordarem sobre o que é "60%".

        Os casos especiais (refs faltando, anime não encontrado) chegam como
        `terminou` com o motivo: a tela de batismo e o Modo Descoberta ainda
        moram no app Qt, e fingir que existem aqui seria pior que dizer que
        não existem.
        """
        from PySide6.QtCore import QThread

        from app.pipeline_types import AIMode
        from app.ui.worker import PipelineWorker
        from app.video_ingest import EpisodeInfo, parse_mmss, parse_mmss_estrito

        if getattr(self, "_thread", None) is not None:
            return json.dumps({"ok": False, "msg": "já tem uma análise rodando"})

        d = json.loads(pedido)
        arquivo = Path(d.get("arquivo") or "")
        if not arquivo.exists():
            return json.dumps({"ok": False, "msg": "escolha um episódio primeiro"})

        # EM QUAL PASTA ESTE ANIME MORA?
        #
        # `app/storage/pastas.py` existe pra isso desde a v0.4.10, e ninguém
        # no app inteiro definia `perguntar_pasta` — então o `resolver` caía
        # no último passo, criava a pasta com o nome digitado E GRAVAVA a
        # decisão na memória. Depois disso nem o Qt pergunta mais, porque ele
        # desiste cedo quando a chave já está lá. Digitar "Mushoku" numa
        # análise e "Mushoku Tensei" na outra dava duas pastas do mesmo show,
        # com o acervo partido no meio e sem volta fácil.
        #
        # A pergunta é feita AQUI, na thread da interface: o pipeline roda em
        # thread de fundo e não pode abrir diálogo.
        from app.storage import pastas as _pastas

        anime_digitado = (d.get("anime") or "").strip()
        if anime_digitado and not d.get("pasta_escolhida"):
            try:
                candidatas = _pastas.parecidas(anime_digitado, self._saida())
            except Exception:  # noqa: BLE001
                candidatas = []
            if candidatas:
                return json.dumps({
                    "ok": False, "precisa_pasta": True,
                    "digitado": anime_digitado, "parecidas": candidatas,
                })
        if d.get("pasta_escolhida"):
            try:
                _pastas.apontar(anime_digitado, str(d["pasta_escolhida"]),
                                self.cfg.cache_path)
            except Exception as e:  # noqa: BLE001
                print(f"    [python] não deu pra gravar a pasta: {e}")

        # OP/ED escritos errado eram INDISTINGUÍVEIS de campo vazio: o
        # `parse_mmss` devolve 0.0 tanto pra "" quanto pra "1;30" ou "90s", e
        # a abertura inteira entrava na análise como ~40 cenas de créditos
        # sem ninguém avisar. Aqui a análise nem começa.
        for chave, rotulo in (("op", "OP"), ("ed", "ED")):
            bruto = (d.get(chave) or "").strip()
            v = parse_mmss_estrito(bruto)
            if v is None:
                return json.dumps({"ok": False, "msg":
                    f"'{bruto}' não é um tempo válido no campo {rotulo}. "
                    f"Use MM:SS (ex.: 1:30) ou segundos (ex.: 90). "
                    f"Deixe vazio se o episódio não tiver."})
            # `1.30` é o caso traiçoeiro: não é erro de sintaxe, é ambiguidade.
            # Vira 1,3 SEGUNDO, um OP que "funcionou" e não pulou nada. Não dá
            # pra adivinhar a intenção, então o app não adivinha — recusa e
            # pede pra escrever de um jeito que só tenha uma leitura.
            if "." in bruto and 0 < v < 5:
                return json.dumps({"ok": False, "msg":
                    f"'{bruto}' no campo {rotulo} vale {v:.1f} SEGUNDOS, não "
                    f"{bruto.replace('.', ':')} minutos. Use dois pontos "
                    f"({bruto.replace('.', ':')}) ou os segundos direto."})

        try:
            info = EpisodeInfo(
                anime=(d.get("anime") or "").strip(),
                season=int(d.get("temporada") or 1),
                episode=int(d.get("episodio") or 1),
                source=arquivo,
                # o mesmo parser de "MM:SS" do app, pra OP e ED
                skip_head_seconds=parse_mmss(d.get("op") or ""),
                skip_tail_seconds=parse_mmss(d.get("ed") or ""),
            )
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"dados do episódio: {e}"})
        if not info.anime:
            return json.dumps({"ok": False, "msg": "falta o nome do anime"})

        # "Só cortar" NÃO pode valer quando o clique foi num botão de IA ou
        # no Modo Descoberta. O `pipeline.run` testa `cut_only` primeiro e
        # sai antes de identificar: com a caixa marcada, clicar em
        # "Analisar + IA" ignorava a IA inteira em silêncio — o usuário
        # esperava o dobro do tempo e recebia cenas sem dono.
        usa_ia = bool(d.get("ia"))
        descoberta = bool(d.get("descoberta"))
        so_cortar = bool(d.get("so_cortar")) and not usa_ia and not descoberta

        self._thread = QThread()
        self._worker = PipelineWorker(
            self.cfg, info,
            use_ai_recognition=usa_ia,
            ai_mode=AIMode.FULL,
            ai_review_ambiguous=bool(d.get("revisar")),
            discovery=descoberta,
            cut_only=so_cortar,
            # "adicionar" preserva o que a análise anterior já identificou.
            # A página pergunta antes; sem resposta o padrão é substituir,
            # que é o mesmo padrão do app Qt.
            merge_previous=(d.get("modo") == "adicionar"),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._etapa_atual = ""
        # UM slot so: o `_anota_etapa` anota a etapa E reemite o progresso.
        # Ligar o sinal do worker direto no sinal da ponte nao relaia
        # atravessando thread — ver o docstring dele.
        self._worker.stage.connect(self._anota_etapa)
        # MÉTODOS, nunca lambdas — ver `_fim_falhou`. Conexão com lambda não
        # tem objeto receptor, o Qt escolhe DIRETA, e o fim da análise passa a
        # rodar na thread do worker: ele destrói a própria QThread de dentro
        # dela e o Qt aborta o processo.
        self._worker.finished.connect(self._fim_com_resultado)
        self._worker.failed.connect(self._fim_falhou)
        self._worker.cancelled.connect(self._fim_cancelado)
        self._worker.refs_missing.connect(self._fim_sem_refs)
        self._worker.anime_not_found.connect(self._fim_sem_anime)
        self._worker.discovery_ready.connect(self._descoberta_pronta)
        # Análise nova: o que sobrou da anterior não pode chegar agora. Um
        # batismo reaberto e não puxado abriria a tela de nomes no meio da
        # próxima análise.
        self._fim_pendente = None
        self._batismo_pendente = None
        self._thread.start()
        print(f"    [python] análise começou: {info.anime} S{info.season:02d}E{info.episode:02d}")
        return json.dumps({"ok": True, "msg": f"Analisando {info.anime}…"})

    # ---- Modo Descoberta e batismo ---------------------------------------
    @Slot(str, result=str)
    def descobrir(self, pedido: str) -> str:
        """Modo Descoberta: corta o episódio e agrupa os rostos por
        semelhança, sem saber quem é ninguém. O resultado vai pra tela de
        batismo, onde o usuário dá nome aos grupos."""
        d = json.loads(pedido)
        d["descoberta"] = True
        return self.analisar(json.dumps(d))

    # ---- os fins que vinham de lambda ------------------------------------
    #
    # Estes quatro eram lambdas ligados direto no sinal do worker, e é por
    # isso que o app do Levi fechou sozinho no segundo em que o batismo
    # terminou de gravar. O Visualizador de Eventos do Windows:
    #
    #     módulo com falha: Qt6Core.dll
    #     código de exceção: 0xc0000409   (qFatal — o Qt abortando de
    #                                      propósito)
    #
    # Conexão com lambda NÃO TEM objeto receptor, então o Qt não tem thread
    # de destino e escolhe DIRETA: o corpo roda na thread de quem emitiu, o
    # worker. Aí dentro do `_fim_da_analise` vem `t.wait(4000)` — a thread
    # esperando por si mesma — e logo depois `self._thread = None`, que solta
    # a última referência e DESTRÓI a QThread de dentro dela. Qt aborta.
    #
    # Um método ligado (bound method de um QObject) carrega a afinidade de
    # thread do objeto e ganha conexão ENFILEIRADA: roda na interface, que é
    # onde encerrar a thread é seguro. É a mesma pegadinha que me custou meia
    # hora na caça do progresso, agora do outro lado do fio.
    def _fim_falhou(self, msg: str) -> None:
        self._fim_da_analise("falhou", detalhe=str(msg))

    def _fim_cancelado(self) -> None:
        self._fim_da_analise("cancelado")

    def _fim_sem_refs(self, msg: str, pasta: str) -> None:
        # A pasta das refs vinha sendo jogada fora (`lambda m, _p:`). Sem ela
        # some o "Abrir pasta de refs", que é a ação que resolve o problema.
        self._refs_dir = str(pasta or "")
        self._fim_da_analise("refs_faltando", detalhe=str(msg),
                             refs_dir=str(pasta or ""))

    def _fim_sem_anime(self, msg: str) -> None:
        self._fim_da_analise("anime_nao_achado", detalhe=str(msg))

    def _fim_batizado(self, r=None) -> None:
        # O `lambda _r:` daqui jogava fora o PipelineResult do commit — a
        # mesma doença do `_fim_com_resultado`. O batismo salvava 12
        # personagens em 332 cenas e o fim ia pra página sem `episodio_id`,
        # sem `cenas` e sem `rotulo`: a aba Resultados abria em branco depois
        # de um batismo que tinha dado certo.
        extra = self._le_o_resultado(r, sobras_reabrem=False) if r is not None else {}
        self._fim_da_analise("batizado", **extra)

    def _descoberta_pronta(self, disc) -> None:
        """Fim do Modo Descoberta — e ele é um FIM, coisa que ninguém dizia.

        O worker emitia `discovery_ready` e dava `return`, sem passar por
        `_fim_da_analise`. Três consequências, todas medidas numa descoberta
        de verdade que chegou a 100% e parou ali:

        · a QThread nunca era encerrada, então `self._thread` ficava presa
          pra sempre — e o `batizar` recusa trabalhar com uma thread viva
          ("já tem trabalho rodando"). O batismo estava condenado antes
          mesmo de a tela poder abrir;
        · o laço que puxa o progresso lê `rodando` desse mesmo campo: ele
          ficava girando de 200 em 200 ms pra sempre, com o Cancelar aceso;
        · a página nunca era avisada de nada, porque o único aviso era o
          sinal `descobriu` — Python→JS, o sentido que morre enquanto uma
          análise existe.
        """
        n = len(getattr(disc, "groups", None) or [])
        self._guarda_descoberta(disc)
        self._fim_da_analise("descoberta", grupos=n)

    def _guarda_descoberta(self, disc) -> None:
        """Recebe o DiscoveryResult e o abre pra página.

        O objeto inteiro fica AQUI, no Python: ele carrega embeddings e
        centroides que o JavaScript não tem o que fazer com. Pro navegador
        vai só o que a tela precisa desenhar, e as fotos viram URLs no
        servidor `cena:/grupo/`.
        """
        self._descoberta = disc
        self.servidor.grupos.clear()

        grupos = []
        for g in disc.groups:
            fotos = []
            # `ref_crops_jpg` (8), NÃO `thumbs_jpg` (6). O commit do batismo
            # aplica os índices que o usuário clicou sobre ref_crops_jpg — se
            # a tela mostra só as 6 primeiras, DUAS fotos que ele nunca viu
            # viram referência, e a dica na tela ("clique numa foto pra tirar
            # ela das referências") vira mentira pra elas. O diálogo do Qt
            # publica as 8 de propósito: o que você vê é o que vai pro banco.
            for i, jpg in enumerate(g.ref_crops_jpg):
                chave = f"{g.key}/{i}"
                self.servidor.grupos[chave] = jpg
                fotos.append(f"cena:/grupo/{chave}")
            grupos.append({
                "key": int(g.key),
                "cenas": int(g.n_shots),
                "rostos": int(g.n_faces),
                "fotos": fotos,
                "sugerido": g.suggested_name or "",
                "confianca": round(float(g.suggested_sim or 0), 2),
            })
        # o maior grupo primeiro: quem aparece mais é quem o usuário
        # reconhece mais rápido, e nomear ele já resolve a maior parte
        grupos.sort(key=lambda x: -x["cenas"])

        payload = {
            "titulo": disc.anime_title,
            "temporada": int(disc.season),
            "episodio": int(disc.episode),
            "cenas": len(disc.shots),
            "rostos": int(disc.total_faces),
            "elenco": list(disc.roster or []),
            "grupos": grupos,
        }
        print(f"    [python] descoberta: {len(grupos)} grupos, "
              f"{disc.total_faces} rostos, elenco com {len(disc.roster or [])} nomes")
        carga = json.dumps(payload)
        # GUARDA pra página PUXAR, pelo mesmo motivo do `_fim_pendente`: o
        # `descobriu` é um sinal Python→JS e a descoberta termina com o
        # worker ainda vivo, que é exatamente o estado em que esse sentido
        # não entrega nada. A análise ia até 100%, o log dizia "18 grupos
        # descobertos" e a tela de batismo nunca abria.
        self._batismo_pendente = carga
        self.descobriu.emit(carga)

    @Slot(str, result=str)
    def batizar(self, pedido: str) -> str:
        """Fecha o Modo Descoberta com os nomes dados.

        `nomes`: {key do grupo: nome}. Grupo sem nome é ignorado; dois grupos
        com o MESMO nome fundem — é assim que o usuário conserta o clustering
        que partiu uma pessoa em dois.
        `tiradas`: {key: [índices]} das fotos que ele marcou como intrusas —
        rosto alheio no grupo não pode virar referência, senão contamina o
        banco do personagem.
        """
        from PySide6.QtCore import QThread

        from app.ui.worker import DiscoveryCommitWorker

        disc = getattr(self, "_descoberta", None)
        if disc is None:
            return json.dumps({"ok": False, "msg": "nenhuma descoberta aberta"})
        if getattr(self, "_thread", None) is not None:
            return json.dumps({"ok": False, "msg": "já tem trabalho rodando"})

        d = json.loads(pedido)
        nomes = {int(k): v.strip() for k, v in (d.get("nomes") or {}).items()
                 if (v or "").strip()}
        tiradas = {int(k): [int(i) for i in v]
                   for k, v in (d.get("tiradas") or {}).items()}
        if not nomes:
            return json.dumps({"ok": False, "msg": "nenhum grupo foi nomeado"})

        self._thread = QThread()
        self._worker = DiscoveryCommitWorker(self.cfg, disc, nomes, tiradas)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._etapa_atual = ""
        self._worker.stage.connect(self._anota_etapa)
        # métodos, nunca lambdas — foi ESTE `finished` que fechou o app do
        # Levi no segundo em que o batismo acabou de gravar (ver `_fim_falhou`)
        self._worker.finished.connect(self._fim_batizado)
        self._worker.failed.connect(self._fim_falhou)
        self._fim_pendente = None
        self._batismo_pendente = None
        self._thread.start()
        print(f"    [python] batismo de {len(nomes)} grupo(s): "
              f"{', '.join(sorted(nomes.values()))}")
        return json.dumps({"ok": True, "msg": f"Salvando {len(nomes)} personagem(ns)…"})

    # Etapas ANTES desta não escrevem no banco: o `clear_episode_shots` roda
    # depois do elenco (pipeline.py:213). Cancelar até aqui não custa nada;
    # daí pra frente o resultado anterior já foi embora.
    _ETAPAS_SEM_ESTRAGO = ("parse", "detect_shots", "cut_shots")

    def _anota_etapa(self, sid: str, fracao: float, msg: str) -> None:
        """Ponte entre o worker e a página — e é ELA que repassa o progresso.

        Antes o worker era ligado direto no sinal da ponte
        (`worker.stage.connect(self.progresso)`), e isso NUNCA funcionou numa
        análise de verdade: o worker vive noutra thread (`moveToThread`), e
        ligar sinal-em-sinal atravessando thread não relaia — a página ficava
        em "Aguardando · 0%" do começo ao fim enquanto o log enchia de
        `detect_shots ... 58%`.

        Ninguém percebeu porque a tela de progresso só tinha sido exercitada
        pelo `ensaiar()`, que emite `self.progresso` DIRETO, da thread dele.
        O ensaio provava o caminho ponte→página e pulava justamente o elo
        que estava quebrado, worker→ponte.

        Um slot de verdade recebe a chamada com afinidade de thread correta e
        reemite aqui, na thread da interface.
        """
        self._etapa_atual = sid
        # GUARDA o último estado pra página PUXAR — ver `progresso_atual`.
        self._ultimo_progresso = (sid, float(fracao), msg)
        self._n_progresso += 1
        self.progresso.emit(sid, fracao, msg)

    @Slot(result=str)
    def progresso_atual(self) -> str:
        """O último progresso, pra página BUSCAR em vez de esperar o sinal.

        MEDIDO, e é o motivo desta função existir: durante uma análise o
        QWebChannel para de entregar sinal Python→JS. Fica vivo no sentido
        contrário (o JS continua chamando slots e recebendo resposta na
        mesma hora), mas nenhum `progresso.emit` chega — nem os do worker,
        nem um emitido na mão pela thread principal. Antes da análise
        começar, o mesmo sinal chega normalmente.

        A tela ficava em "Aguardando · 0%" do começo ao fim enquanto o log
        enchia de `detect_shots ... 58%`. Passou despercebido porque a tela
        de progresso só tinha sido exercitada pelo `ensaiar()`, que roda sem
        análise nenhuma — o ensaio provava o caminho num estado em que ele
        funciona.

        Puxar é feio e é o que funciona. `n` deixa a página saber se algo
        novo chegou sem comparar strings.
        """
        sid, fracao, msg = getattr(self, "_ultimo_progresso", ("", 0.0, ""))
        # o fim é entregue UMA vez: quem puxou, levou
        fim = getattr(self, "_fim_pendente", None)
        self._fim_pendente = None
        # e o batismo vem pelo mesmo caminho, pelo mesmo motivo
        batismo = getattr(self, "_batismo_pendente", None)
        self._batismo_pendente = None
        return json.dumps({
            "n": getattr(self, "_n_progresso", 0),
            "sid": sid, "fracao": fracao, "msg": msg,
            "rodando": getattr(self, "_thread", None) is not None,
            "fim": fim,
            "batismo": batismo,
        })

    def _fim_da_analise(self, como: str, **extra) -> None:
        """Fim da análise — e a página precisa saber QUAL fim.

        Isto emitia uma string só, e o JS testava `=== 'cancelado'`: todo o
        resto — falha, refs faltando, anime não encontrado — virava tarja
        verde "Análise terminada." com a barra em 100% e ✓ em cada etapa. O
        usuário ia procurar as cenas na pasta, não achava nada, e não tinha
        como saber por quê. As mensagens boas que o Python produz (a pasta
        das refs, a oferta do Modo Descoberta) morriam aqui.

        Agora vai um objeto: `como` diz o tipo de fim, e o resto é o que
        aquela tela precisa pra ser útil em vez de só bonita.
        """
        from PySide6.QtCore import QThread, QTimer

        t = getattr(self, "_thread", None)
        # REDE: chegou aqui de dentro da própria thread de trabalho (conexão
        # direta). Encerrar e destruir a QThread daqui é o `qFatal` que fechou
        # o app sem deixar traceback. Reagenda com a ponte como contexto, que
        # é o que joga a chamada pra thread da interface, e sai.
        if t is not None and t is QThread.currentThread():
            print("    [python] fim da análise veio da thread de trabalho — "
                  "reagendando pra interface")
            QTimer.singleShot(0, self, lambda: self._fim_da_analise(como, **extra))
            return
        if t is not None:
            t.quit()
            t.wait(4000)
        self._thread = None
        self._worker = None
        etapa = getattr(self, "_etapa_atual", "")
        self._etapa_atual = ""
        # NÚMERO do fim, pra página saber se já tratou ESTE.
        #
        # Ele chega por dois caminhos (o sinal `terminou` e o laço que puxa) e
        # tratar duas vezes abre caixinha em duplicata. Deduplicar pelo
        # CONTEÚDO parecia bastar e não basta: duas análises seguidas que dão
        # certo produzem exatamente o mesmo `pronto|organize`, e a segunda
        # seria engolida como repetição da primeira.
        self._n_fim = getattr(self, "_n_fim", 0) + 1
        carga = {"como": como, "etapa": etapa, "n_fim": self._n_fim}
        carga.update(extra)
        if como == "cancelado":
            # Só o cancelamento durante o corte é inofensivo. Depois do
            # elenco o banco já foi limpo, e o que sobrou é um resultado
            # PARCIAL misturado com as pastas da análise antiga.
            carga["perdeu_resultado"] = etapa not in self._ETAPAS_SEM_ESTRAGO
        print(f"    [python] análise terminou: {como}"
              + (f" ({extra.get('detalhe', '')[:90]})" if extra.get("detalhe") else ""))
        # O FIM também fica guardado pra página puxar. O `terminou` é um
        # sinal Python→JS, e é justamente esse sentido que morre enquanto a
        # análise existe — sem isto a tela ficaria com o Cancelar aceso pra
        # sempre depois de uma análise que já acabou.
        self._fim_pendente = carga
        self.terminou.emit(json.dumps(carga))

    def _fim_com_resultado(self, r) -> None:
        """Fim BEM-SUCEDIDO — que nem sempre é bom.

        O `finished` carrega um `PipelineResult` e a ponte jogava ele fora
        com um `lambda _r:`. Dentro dele vão os três avisos que o app Qt
        mostra e a web nunca mostrou: protagonista sem refs utilizáveis
        (as cenas dele contaminam quem se parece com ele), elenco com
        suspeitos, e grupos de cena que ninguém conseguiu nomear.

        Análise que termina com quase tudo sem dono é um fracasso que se
        parece com sucesso — e era exatamente assim que ela aparecia.
        """
        extra = self._le_o_resultado(r, sobras_reabrem=True)
        self._fim_da_analise("pronto", **extra)

    def _le_o_resultado(self, r, sobras_reabrem: bool) -> dict:
        """Tira do `PipelineResult` tudo que a tela precisa.

        Vale pros DOIS fins bons — a análise normal e o commit do batismo. O
        batismo passava um `lambda _r:` e jogava o resultado fora inteiro:
        salvava 12 personagens em 332 cenas e mandava pra página um fim sem
        `episodio_id`, sem `cenas` e sem `rotulo`. A aba Resultados abria
        vazia depois de um batismo que tinha dado certo.
        """
        extra: dict = {}
        try:
            extra["cenas"] = int(getattr(r, "total_shots", 0) or 0)
            extra["personagens"] = list(getattr(r, "identified_characters", []) or [])
            # O episódio que ACABOU de sair. Sem isto a aba Resultados abria
            # em branco: ela copiava o título da Biblioteca (que vale "—",
            # ou "Biblioteca vazia" pra quem analisou o primeiro episódio) e
            # quem preenche a grade é o `carregaCenas`, que só rodava quando
            # se clicava na árvore.
            extra["episodio_id"] = int(getattr(r, "episode_id", 0) or 0)
            extra["rotulo"] = (
                f"{getattr(r, 'anime_title', '')} — "
                f"S{int(getattr(r, 'season', 0)):02d}"
                f"E{int(getattr(r, 'episode', 0)):02d}")
            if getattr(r, "low_refs_warning", None):
                extra["refs_fracas"] = str(r.low_refs_warning)
                extra["refs_dir"] = str(getattr(r, "refs_dir", "") or "")
                self._refs_dir = extra["refs_dir"]
            suspeitos = [c["name"] for c in (getattr(r, "cast_review", None) or [])
                         if c.get("suspicious")]
            if suspeitos:
                extra["suspeitos"] = suspeitos
            sobras = getattr(r, "leftover_groups", None)
            if sobras is not None and getattr(sobras, "groups", None):
                extra["grupos_sem_nome"] = len(sobras.groups)
                # Só a análise normal reabre o batismo com as sobras. Depois
                # de um batismo, reabrir a tela de nomes por causa do que
                # sobrou seria mandar a pessoa de volta pro começo do que ela
                # acabou de terminar.
                if sobras_reabrem:
                    # o payload do batismo é o MESMO que a Descoberta usa
                    self._guarda_descoberta(sobras)
        except Exception as e:  # noqa: BLE001 — nenhum aviso vale derrubar o fim
            print(f"    [python] não deu pra ler o resultado: {e}")
        return extra

    @Slot()
    def ensaiar(self) -> None:
        """Ensaio do progresso: percorre as etapas REAIS numa thread de fundo
        emitindo o mesmo sinal que o `PipelineWorker` emite.

        Não analisa nada — não toca em vídeo, banco nem pasta. Serve pra
        provar a única coisa que faltava saber da arquitetura: que sinal de
        thread de fundo chega na página e que ela continua respondendo
        enquanto isso. Ligar o worker de verdade é trocar este ensaio por um
        `worker.stage.connect(self.progresso)`.
        """
        from PySide6.QtCore import QThread

        from app.pipeline_types import STAGES

        ponte = self

        class Ensaio(QThread):
            def run(self) -> None:  # noqa: D102
                for k, (sid, rotulo) in enumerate(STAGES):
                    passos = 8
                    for p in range(passos + 1):
                        if self.isInterruptionRequested():
                            ponte.terminou.emit(json.dumps(
                                {"como": "cancelado", "etapa": sid}))
                            return
                        fracao = (k + p / passos) / len(STAGES)
                        ponte.progresso.emit(
                            sid, fracao,
                            f"{rotulo} {p * 40}/{passos * 40}" if p else rotulo,
                        )
                        self.msleep(45)
                ponte.terminou.emit(json.dumps({"como": "pronto", "cenas": 331}))

        self._ensaio = Ensaio()
        self._ensaio.start()
        print("    [python] ensaio do progresso começou (thread de fundo)")

    @Slot(result=str)
    def cancelar(self) -> str:
        """Pede o cancelamento — e DEVOLVE algo, que é o que faltava.

        Este slot era mudo. O botão continuava ativo, a barra continuava
        andando e nenhum recado aparecia: o cancelamento é cooperativo e o
        pipeline só olha o pedido no próximo ponto de checagem, o que pode
        levar minutos se um modelo estiver baixando. Do lado de fora isso é
        indistinguível de um botão quebrado, e a pessoa clica de novo.
        """
        # o worker de verdade tem o pedido dele; o ensaio usa interrupção
        w = getattr(self, "_worker", None)
        if w is not None:
            # O `DiscoveryCommitWorker` não tem `request_cancel` — e não é
            # esquecimento, é que gravar metade dos personagens, das refs e
            # dos hardlinks é pior que esperar. Chamar assim mesmo levantava
            # AttributeError dentro do slot: a página recebia `undefined` e
            # morria no JSON.parse.
            if not hasattr(w, "request_cancel"):
                return json.dumps({"ok": False, "msg":
                    "Isto não dá pra cancelar — está gravando os personagens "
                    "e as referências. Falta pouco."})
            w.request_cancel()
            print("    [python] cancelamento pedido à análise")
            return json.dumps({"ok": True, "msg":
                "Cancelando — espera a operação atual terminar (um download "
                "de modelo pode levar minutos)."})
        t = getattr(self, "_ensaio", None)
        if t is not None and t.isRunning():
            t.requestInterruption()
            print("    [python] cancelamento pedido ao ensaio")
            return json.dumps({"ok": True, "msg": "Cancelando o ensaio…"})
        return json.dumps({"ok": False, "msg": "Não há nada rodando."})

    @Slot(result=str)
    def reabrir_batismo(self) -> str:
        """Reabre o batismo da última descoberta.

        Fechar a tela no Cancelar só escondia a caixa. O `DiscoveryResult`
        continuava vivo aqui dentro, mas nenhum slot o devolvia — não havia
        caminho de volta, e refazer a descoberta é a análise inteira de novo.
        """
        disc = getattr(self, "_descoberta", None)
        if disc is None or not getattr(disc, "groups", None):
            return json.dumps({"ok": False, "msg": "nenhuma descoberta aberta"})
        self._guarda_descoberta(disc)
        # O payload VOLTA na resposta, não pelo sinal. Aqui não há análise
        # rodando e o `descobriu` até chegaria — mas também não há laço
        # puxando, então o `_batismo_pendente` ficaria parado esperando um
        # puxão que só viria na próxima análise. Quem pediu, recebe agora.
        carga = getattr(self, "_batismo_pendente", None)
        self._batismo_pendente = None
        return json.dumps({"ok": True, "msg": "", "batismo": carga})

    @Slot(result=str)
    def readotar(self) -> str:
        """Traz pro banco as pastas de episódio que ele não conhece.

        É a inversa do órfão: lá o registro sobrevive à pasta, aqui a pasta
        sobrevive ao registro. Uma biblioteca que esconde 393 clipes que
        estão no disco mente tanto quanto uma que lista o que não existe.
        Nada é reanalisado — o mapa das cenas sai do `metadata/shots.json`
        que toda análise deixa."""
        from app.storage import readocao

        db = self.db
        achadas = readocao.orfas(self._saida(), db)
        if not achadas:
            return json.dumps({"ok": True, "msg": "nenhuma pasta esquecida"})
        cenas = 0
        for o in achadas:
            r = readocao.readotar(db, Path(o["pasta"]))
            if r["ok"]:
                cenas += r["cenas"]
            print(f"    [python] {o['pasta']}: {r['msg']}")
        msg = f"{len(achadas)} episódio(s) readotados · {cenas} cenas de volta"
        return json.dumps({"ok": True, "msg": msg})

    @Slot(result=str)
    def esquecidas(self) -> str:
        """Quantas pastas o banco não conhece — pra árvore poder avisar."""
        from app.storage import readocao

        try:
            achadas = readocao.orfas(self._saida(), self.db)
        except Exception:  # noqa: BLE001
            return json.dumps({"n": 0, "cenas": 0})
        return json.dumps({
            "n": len(achadas),
            "cenas": sum(o["clipes"] for o in achadas),
        })

    @Slot(result=str)
    def limpar_orfaos(self) -> str:
        """Apaga do banco os episódios cuja pasta não existe mais.

        Não há pasta pra mandar pra lixeira — ela já sumiu. O que some aqui é
        só o registro morto: cenas que apontam pra arquivos inexistentes. O
        personagem, as fotos de referência e o que o app aprendeu ficam, que
        é a parte cara e não pertence a um episódio só.

        MESMO ASSIM o banco vai pra lixeira antes. "A pasta sumiu" e "a pasta
        de saída mudou" são indistinguíveis daqui: nos dois casos o disco não
        tem o que o banco diz. No segundo, os episódios estão vivos na pasta
        antiga e este botão apagaria o registro deles — recuperável, mas só
        pra quem souber apontar a saída de volta. Uma cópia do .db custa
        alguns MB e transforma um erro caro num arquivo que dá pra restaurar.
        """
        if not self.orfaos:
            return json.dumps({"ok": True, "msg": "nada a limpar"})
        import shutil
        from datetime import datetime

        try:
            destino = (self._saida() / "_lixeira"
                       / datetime.now().strftime("%Y%m%d_%H%M%S"))
            destino.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.banco, destino / "index.db")
            print(f"    [python] cópia do banco em {destino}")
        except OSError as e:
            print(f"    [python] não deu pra copiar o banco: {e}")
        db = self.db
        for ep in self.orfaos:
            db.delete_episode(ep)
        n = len(self.orfaos)
        self.orfaos = []
        msg = f"{n} episódio(s) sem pasta apagados do banco"
        print(f"    [python] {msg}")
        return json.dumps({"ok": True, "msg": msg})

    # ---- ações da aba Resultados -----------------------------------------
    @Slot(str, result=str)
    def sincronizar(self, pedido: str = "") -> str:
        """Explorer → app, nos dois sentidos. EM DUAS ETAPAS.

        Clipe arrastado pra pasta de outro personagem vira atribuição
        lembrada; clipe apagado da pasta vira remoção com memória. É a mesma
        `curation.apply_folder_moves` que a aba Resultados usa hoje.

        Sem `aplicar`, só CONTA e devolve o plano. O botão aplicava direto e
        cada arquivo faltando virava `record_manual('block')` — permanente —
        sem o usuário ver o tamanho do que ia acontecer. E pasta com nome
        desconhecido cria personagem novo: renomear "Rimuru" pra "Rimuru
        Tempest" no Explorer deixava DOIS personagens no acervo, e o único
        aviso era um recado de 4 s com dois números.

        A parte conservadora continua valendo: pasta INTEIRA sumida não vira
        remoção silenciosa em massa. Mas agora ela é CONTADA — dizer "Pastas
        já estavam em dia" depois de ver a pasta da Nina inteira sumida e
        decidir ignorar é a definição de aviso mentiroso.
        """
        from app.curation import apply_folder_moves
        from app.storage.organizer import refresh_shot_links, sanitize

        if self.ep_id is None or self.anime_id is None:
            return json.dumps({"ok": False, "msg": "nenhum episódio aberto"})
        aplicar = bool(json.loads(pedido or "{}").get("aplicar"))
        db = self.db
        raiz = self.raiz

        if not aplicar:
            return self._plano_do_sync(db, raiz, sanitize)

        movidas = apply_folder_moves(db, self.ep_id, self.anime_id, raiz)
        n_movidas = sum(movidas.values()) if movidas else 0

        # clipe que sumiu da pasta de um personagem = remoção com memória
        by_char = raiz / "by_character"
        removidas = 0
        if by_char.exists():
            atribs = db.assignments_for_episode(self.ep_id)
            cenas = {int(s["id"]): s for s in db.shots_for_episode(self.ep_id)}
            mexidas = set()
            for sid, donos in atribs.items():
                s = cenas.get(int(sid))
                if not s:
                    continue
                nome_arq = Path(s["file"]).name
                for d in donos:
                    pasta = by_char / sanitize(d["name"])
                    if not pasta.exists():
                        continue      # pasta inteira sumida: NÃO conta aqui
                    if not (pasta / nome_arq).exists():
                        db.remove_shot_character(int(sid), int(d["id"]))
                        db.record_manual(self.ep_id, int(s["idx"]), int(d["id"]), "block")
                        removidas += 1
                        mexidas.add(int(sid))
            if mexidas:
                agora = db.assignments_for_episode(self.ep_id)
                for sid in mexidas:
                    nomes = [x["name"] for x in agora.get(sid, [])]
                    try:
                        refresh_shot_links(
                            raiz, raiz / cenas[sid]["file"], nomes,
                            by_character=self.cfg.organize_by_character_enabled,
                            by_pair=self.cfg.organize_by_pair_enabled,
                        )
                    except Exception:  # noqa: BLE001
                        pass

        # "Já estavam em dia" só quando NÃO havia nada a fazer. Pasta inteira
        # sumida era vista, ignorada por segurança, e mesmo assim a mensagem
        # dizia que estava tudo certo.
        ignoradas = sorted({
            d["name"]
            for donos in db.assignments_for_episode(self.ep_id).values()
            for d in donos
            if by_char.exists() and not (by_char / sanitize(d["name"])).exists()
        })
        if not n_movidas and not removidas:
            msg = ("Pastas já estavam em dia." if not ignoradas else
                   "Nada a sincronizar — mas a pasta de "
                   + ", ".join(ignoradas[:3])
                   + (f" e mais {len(ignoradas) - 3}" if len(ignoradas) > 3 else "")
                   + " sumiu por inteiro e foi ignorada por segurança "
                     "(apagar uma pasta inteira não vira remoção em massa).")
        else:
            msg = f"{n_movidas} cena(s) movida(s) à mão · {removidas} removida(s)"
            if ignoradas:
                msg += (f" · {len(ignoradas)} pasta(s) inteiras sumidas foram "
                        f"ignoradas: {', '.join(ignoradas[:3])}")
        print(f"    [python] sync: {msg}")
        return json.dumps({"ok": True, "msg": msg, "mudou": bool(n_movidas or removidas)})

    def _plano_do_sync(self, db, raiz: Path, sanitize) -> str:
        """O que o sync FARIA — contado, sem tocar em nada."""
        atribs = db.assignments_for_episode(self.ep_id)
        cenas = {int(s["id"]): s for s in db.shots_for_episode(self.ep_id)}
        by_char = raiz / "by_character"

        sairiam = 0
        pastas_sumidas: set[str] = set()
        if by_char.exists():
            for sid, donos in atribs.items():
                s = cenas.get(int(sid))
                if not s:
                    continue
                nome_arq = Path(s["file"]).name
                for d in donos:
                    pasta = by_char / sanitize(d["name"])
                    if not pasta.exists():
                        pastas_sumidas.add(d["name"])
                    elif not (pasta / nome_arq).exists():
                        sairiam += 1

        # pasta com nome que o banco não conhece = personagem NOVO
        conhecidos = set()
        try:
            with db.connect() as c:
                conhecidos = {sanitize(r["name"]) for r in c.execute(
                    "SELECT name FROM character WHERE anime_id = ?", (self.anime_id,))}
        except Exception:  # noqa: BLE001
            pass
        novos = []
        if by_char.exists():
            for p in sorted(by_char.iterdir()):
                if p.is_dir() and p.name not in conhecidos and any(p.iterdir()):
                    novos.append(p.name)

        return json.dumps({
            "ok": True, "plano": True,
            "sairiam": sairiam,
            "novos": novos[:12],
            "novos_total": len(novos),
            "pastas_sumidas": sorted(pastas_sumidas)[:12],
        })

    @Slot(result=str)
    def exportar_refs(self) -> str:
        """Zip do banco de referências deste anime."""
        import zipfile

        from PySide6.QtWidgets import QFileDialog

        if self.anime_id is None:
            return json.dumps({"ok": False, "msg": "nenhum episódio aberto"})
        with self.db.connect() as c:
            r = c.execute(
                "SELECT title, anilist_id FROM anime WHERE id = ?", (self.anime_id,)
            ).fetchone()
        if r is None:
            return json.dumps({"ok": False, "msg": "anime não encontrado"})

        # o MESMO cache que a análise usa — refs exportadas têm que ser as
        # que o app realmente consultou
        from app.references.reference_store import ReferenceStore

        try:
            loja = ReferenceStore(self.cfg.cache_path)
            cache_id = (f"al{r['anilist_id']}" if r["anilist_id"]
                        else f"local_{r['title']}")
            src = loja.anime_dir(cache_id) / "characters"
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"cache de refs: {e}"})
        if not src.exists():
            return json.dumps({
                "ok": False,
                "msg": "Esse anime ainda não tem banco de referências no cache.",
            })

        destino, _ = QFileDialog.getSaveFileName(
            None, "Exportar refs",
            str(Path.home() / "Documents" / f"CorteCenas-refs-{src.parent.name}.zip"),
            "Zip (*.zip)",
        )
        if not destino:
            return json.dumps({"ok": True, "msg": "cancelado"})

        n_arq = n_pers = 0
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
            for pasta in sorted(src.iterdir()):
                # _filtered guarda o que o filtro REJEITOU — exportar isso
                # contaminaria o banco de quem receber
                if not pasta.is_dir() or pasta.name == "_filtered":
                    continue
                achou = False
                for f in sorted(pasta.iterdir()):
                    if f.is_file():
                        zf.write(f, f"{pasta.name}/{f.name}")
                        n_arq += 1
                        achou = True
                n_pers += 1 if achou else 0
        return json.dumps({
            "ok": True,
            "msg": f"{n_arq} imagens de {n_pers} personagens em {Path(destino).name}",
        })

    @Slot(str, result=str)
    def abrir_pasta(self, qual: str) -> str:
        """Abre no Explorer. `qual` é 'episodio', 'ep:<id>' ou o nome de um
        personagem.

        O menu do acervo calculava o episódio clicado e mandava 'episodio'
        assim mesmo — e este slot abria `self.raiz`, que é o episódio ABERTO.
        Clicar com o direito no S03E05 abria o S03E01. Antes de abrir
        qualquer episódio, `raiz` ainda é a raiz de saída, então o botão
        despejava o Output inteiro. E quando a pasta não existia o slot não
        fazia nada e não devolvia nada: clique sem efeito e sem explicação.
        """
        import subprocess

        if qual.startswith("ep:"):
            try:
                eid = int(qual[3:])
            except ValueError:
                return json.dumps({"ok": False, "msg": "episódio inválido"})
            with self.db.connect() as c:
                r = c.execute(
                    """SELECT e.season, e.episode, a.title
                         FROM episode e JOIN anime a ON a.id = e.anime_id
                        WHERE e.id = ?""", (eid,)).fetchone()
            if r is None:
                return json.dumps({"ok": False, "msg": "episódio não está no banco"})
            alvo = self._raiz_do_episodio(r["title"], r["season"], r["episode"])
        elif qual == "episodio":
            if self.ep_id is None:
                return json.dumps({"ok": False, "msg":
                    "Abra um episódio na árvore primeiro."})
            alvo = self.raiz
        else:
            alvo = self.raiz / "by_character" / qual

        if not alvo.exists():
            return json.dumps({"ok": False, "msg":
                f"A pasta não existe mais em {self._saida()}."})
        subprocess.Popen(["explorer", str(alvo)])
        print(f"    [python] abri {alvo}")
        return json.dumps({"ok": True, "msg": f"Abri {alvo}"})

    @Slot(str, result=str)
    def detalhes_do_apagar(self, pedido: str) -> str:
        """Quantas cenas e quanta curadoria somem — o TAMANHO do estrago.

        A caixinha dizia "o que o app aprendeu fica", e isso é meia verdade:
        o `delete_episode` apaga o `manual_override` DAQUELE episódio, que é
        exatamente a memória de curadoria dele. Arrastar a pasta de volta da
        lixeira não traz isso — as fotos de referência e os personagens é que
        ficam.
        """
        ids = [int(i) for i in json.loads(pedido).get("ids", [])]
        if not ids:
            return json.dumps({"cenas": 0, "curadoria": 0})
        marcas = ",".join("?" * len(ids))
        cenas = curadoria = 0
        try:
            with self.db.connect() as c:
                cenas = int(c.execute(
                    f"SELECT COUNT(*) FROM shot WHERE episode_id IN ({marcas})",
                    ids).fetchone()[0])
                curadoria = int(c.execute(
                    f"SELECT COUNT(*) FROM manual_override "
                    f"WHERE episode_id IN ({marcas})", ids).fetchone()[0])
        except Exception:  # noqa: BLE001
            pass
        return json.dumps({"cenas": cenas, "curadoria": curadoria})

    @Slot()
    def descobrir_dispositivo(self) -> None:
        """Quem está fazendo o trabalho: GPU ou CPU. Numa thread de fundo.

        A cápsula do topo dizia **"RTX 4070"** — texto fixo no HTML, herdado
        da maquete — com a bolinha verde do CSS, em qualquer máquina. Ela
        afirmava uma placa que o usuário podia não ter (não tem: é uma 5080)
        e afirmava saúde que podia não existir, contradizendo o próprio
        diálogo de "sem GPU" que o main.py abre por cima da janela.

        Em thread porque o primeiro `import torch` leva segundos: perguntar
        isso na thread da interface congelaria a janela no arranque.
        """
        from PySide6.QtCore import QThread

        if getattr(self, "_sonda_gpu", None) is not None:
            return
        ponte = self

        class Sonda(QThread):
            def run(self) -> None:  # noqa: D102
                from app.deps_check import cuda_available, cuda_error, gpu_name
                from app.ffmpeg_locate import nvenc_available, nvenc_reason

                tem = cuda_available()
                erro = cuda_error()
                if tem:
                    estado, nome = "gpu", (gpu_name() or "GPU NVIDIA")
                elif erro:
                    # Três estados, não dois: "não deu pra saber" não pode
                    # virar a afirmação "não tem GPU".
                    estado, nome = "desconhecido", "GPU: não deu pra checar"
                else:
                    estado, nome = "cpu", "CPU (lento)"
                carga = {
                    "estado": estado, "nome": nome, "erro": erro,
                    "nvenc": bool(nvenc_available()),
                    "nvenc_motivo": nvenc_reason(),
                }
                print(f"    [python] dispositivo: {carga}")
                ponte.dispositivo.emit(json.dumps(carga))

        self._sonda_gpu = Sonda()
        self._sonda_gpu.start()

    @Slot(result=str)
    def abrir_refs(self) -> str:
        """Abre a pasta de referências do último "refs faltando".

        Um slot próprio em vez de um `abrir_caminho(qualquer_coisa)`: o
        caminho vem do Python e nunca da página, então não há como o JS
        mandar o Explorer abrir o que quiser.
        """
        import subprocess

        alvo = Path(getattr(self, "_refs_dir", "") or "")
        if not alvo or not alvo.exists():
            return json.dumps({"ok": False, "msg": "a pasta de refs não existe (ainda)"})
        subprocess.Popen(["explorer", str(alvo)])
        print(f"    [python] abri as refs em {alvo}")
        return json.dumps({"ok": True, "msg": f"Abri {alvo}"})

    # ---- Configurações: os botões que não faziam nada ---------------------
    #
    # Os sete botões da seção "Referências e cache" não tinham UM listener: os
    # únicos handlers de `.btn-campo` filtravam por texto começando com
    # "Escolher"/"Selecionar", e nenhum deles começa assim. Com `:hover` no
    # CSS, eles se anunciavam como vivos. Quem tinha ref suja clicava em
    # "Limpar fotos baixadas", acreditava que limpou, reanalisava e recebia o
    # MESMO resultado errado — uma análise inteira de GPU jogada fora atrás de
    # um botão morto.
    #
    # A lógica toda já existia em `cache_tools`; faltava só o fio.

    @Slot(str, result=str)
    def abrir_especial(self, qual: str) -> str:
        """Abre uma das pastas do app. Lista fechada de propósito: o JS
        escolhe QUAL, nunca o caminho."""
        import subprocess

        from app.cache_tools import refs_root

        c = self.cfg
        alvos = {
            "refs": refs_root(c.cache_path),
            "cache": c.cache_path,
            "saida": Path(c.output_path),
            "logs": Path(c.cache_path) / "logs",
        }
        p = alvos.get(qual)
        if p is None:
            return json.dumps({"ok": False, "msg": f"pasta desconhecida: {qual}"})
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return json.dumps({"ok": False, "msg": f"não deu pra abrir: {e}"})
        subprocess.Popen(["explorer", str(p)])
        return json.dumps({"ok": True, "msg": f"Abri {p}"})

    @Slot(result=str)
    def resumo_refs(self) -> str:
        """Quantas refs existem e quantas vieram da internet — o número que
        faz a pergunta de limpar valer a pena ou não."""
        from app.cache_tools import refs_summary

        try:
            animes, personagens, fotos = refs_summary(self.cfg.cache_path)
            return json.dumps({"ok": True, "animes": animes,
                               "personagens": personagens, "fotos": fotos})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": str(e)})

    @Slot(result=str)
    def limpar_fotos_baixadas(self) -> str:
        from app.cache_tools import clean_catalog_refs

        try:
            # A ORDEM É (arquivos, animes). Eu tinha desempacotado invertido e
            # a mensagem trocava os dois números — "2 fotos de 195 pastas"
            # quando eram 195 fotos de 2 animes. Mensagem que erra a escala
            # do estrago é pior que mensagem nenhuma: ela dá permissão.
            fotos, animes = clean_catalog_refs(self.cfg.cache_path)
            msg = (f"{fotos} foto(s) do catálogo removidas em {animes} "
                   f"anime(s). O que você pôs na mão e os batismos "
                   f"(auto_disc) continuam; a próxima análise baixa as do "
                   f"catálogo de novo.")
            print(f"    [python] {msg}")
            return json.dumps({"ok": True, "msg": msg})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"não deu: {e}"})

    @Slot(result=str)
    def apagar_cache(self) -> str:
        from app.cache_tools import wipe_cache

        try:
            # `wipe_cache` devolve o que NÃO conseguiu mover, não o que moveu.
            # Eu li como "itens movidos" e a mensagem dizia "0 item(ns) foram
            # pra lixeira" depois de mover o cache inteiro — o número certo
            # com o significado ao contrário, que é o jeito mais fácil de
            # mentir sem perceber.
            sobraram = wipe_cache(self.cfg.cache_path)
            if sobraram:
                msg = ("O cache foi pra cache_lixeira (ao lado do cache), MENOS "
                       + f"{len(sobraram)} item(ns) que estão em uso: "
                       + ", ".join(sobraram[:4])
                       + ". Feche o que estiver aberto e tente de novo.")
            else:
                msg = ("Cache inteiro movido pra cache_lixeira (ao lado do "
                       "cache) — dá pra recuperar de lá enquanto você não "
                       "apagar essa pasta na mão.")
            print(f"    [python] {msg}")
            return json.dumps({"ok": not sobraram, "msg": msg})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"não deu: {e}"})

    @Slot(str, result=str)
    def fundir_duplicados(self, pedido: str) -> str:
        """Duas etapas: sem `aplicar` só devolve o PLANO, pra tela mostrar o
        que vai acontecer antes de acontecer."""
        from app.cache_tools import merge_duplicates

        aplicar = bool(json.loads(pedido or "{}").get("aplicar"))
        try:
            r = merge_duplicates(self.cfg.cache_path, apply=aplicar)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"não deu: {e}"})
        if aplicar:
            msg = (f"{len(r['chars'])} personagem(ns) e {len(r['anime'])} "
                   f"anime(s) fundidos, {r['moved']} arquivo(s) reorganizados.")
            print(f"    [python] {msg}")
            return json.dumps({"ok": True, "aplicado": True, "msg": msg})
        linhas = [f"🎬 {' + '.join(s)} → {c}" for s, c in r["anime"]]
        linhas += [f"👤 [{a.split(' [')[0]}] {' + '.join(s)} → {c}"
                   for a, s, c in r["chars"]]
        return json.dumps({"ok": True, "aplicado": False,
                           "animes": len(r["anime"]), "chars": len(r["chars"]),
                           "linhas": linhas[:20], "resto": max(0, len(linhas) - 20)})

    @Slot(result=str)
    def restaurar_padroes(self) -> str:
        """Volta os números da análise ao padrão de fábrica. Só os da
        análise — pasta de saída e chave de API não são 'ajuste fino'."""
        from app.config import Config as _C

        padrao = _C()
        c = self.cfg
        for campo in _CAMPO_DO_PRESET.values():
            setattr(c, campo, getattr(padrao, campo))
        c.save()
        print("    [python] números da análise restaurados ao padrão")
        return json.dumps({"ok": True, "msg": "Números da análise no padrão de fábrica."})

    @Slot(result=str)
    def sobre(self) -> str:
        """Versão e interface em uso. A tela web não dizia nem em que versão
        o app estava — e é justamente ela que precisa dizer, porque cair na
        interface antiga é invisível de qualquer outro jeito."""
        from app import __version__

        return json.dumps({
            "versao": __version__,
            "interface": "nova (web)",
            "logs": str(Path(self.cfg.cache_path) / "logs"),
        })

    @Slot(str)
    def atalho(self, tecla: str) -> None:
        self.cliques.append(f"tecla:{tecla}")
        print(f"    [python] atalho -> {tecla!r}")

    def _preset_atual(self, c) -> str:
        """Qual preset bate com os números gravados — 'manual' se nenhum."""
        from app.ui.presets import PRESETS

        for nome, vals in PRESETS.items():
            if all(
                abs((getattr(c, campo, None) or 0) - vals[curto]) < 1e-9
                for curto, campo in _CAMPO_DO_PRESET.items()
            ):
                return nome
        return "manual"

    @Slot(result=str)
    def config(self) -> str:
        """O estado real das Configurações — o mesmo `Config` que o app usa,
        e os mesmos presets de `ui/presets.py`, pra não existirem dois lugares
        dizendo o que é 'Auto'."""
        try:
            c = self.cfg
            atual = self._preset_atual(c)
            return json.dumps({
                "saida": str(c.output_path),
                # a saída de verdade, quando a desta sessão é provisória
                "saida_indisponivel": getattr(c, "saida_indisponivel", ""),
                "preset": atual,
                "por_personagem": bool(c.organize_by_character_enabled),
                "por_dupla": bool(c.organize_by_pair_enabled),
                "ccip": bool(c.ccip_enabled),
                "deteccao_rapida": bool(c.fast_scene_detect),
                "danbooru": bool(getattr(c, "use_danbooru", False)),
                "tem_chave": bool(getattr(c, "navyai_api_key", "")),
                "modelo": getattr(c, "gemini_model", "") or getattr(c, "navyai_model", ""),
            })
        except Exception as e:  # noqa: BLE001 — o PoC não pode morrer por isso
            return json.dumps({"saida": str(self.raiz.parent.parent), "erro": str(e)})

    @Slot(str, result=str)
    def salvar_config(self, pedido: str) -> str:
        """Grava o que mudou. Só os campos que a tela mostra — o resto do
        Config fica como está, senão salvar a partir de uma tela incompleta
        apagaria ajuste que ela nem exibe."""
        d = json.loads(pedido)
        antes = str(self._saida())
        try:
            c = self.cfg
            from app.ui.presets import PRESETS

            preset = d.get("preset")
            if preset in PRESETS:
                for curto, campo in _CAMPO_DO_PRESET.items():
                    setattr(c, campo, PRESETS[preset][curto])
            for chave, campo in (
                ("por_personagem", "organize_by_character_enabled"),
                ("por_dupla", "organize_by_pair_enabled"),
                ("ccip", "ccip_enabled"),
                ("deteccao_rapida", "fast_scene_detect"),
                ("danbooru", "use_danbooru"),
            ):
                if chave in d:
                    setattr(c, campo, bool(d[chave]))
            if d.get("saida"):
                nova = str(d["saida"])
                # Saída inacessível nesta sessão (HD desconectado): o app está
                # usando um caminho provisório e a tela MOSTRA esse provisório.
                # Salvar sem mexer nele gravaria o provisório por cima do
                # caminho real — o mesmo apagão que o `ensure_dirs` deixou de
                # fazer sozinho, só que pela mão do usuário.
                if (getattr(c, "saida_indisponivel", "")
                        and nova == str(c.output_path)):
                    print(f"    [python] mantive a saída configurada "
                          f"({c.saida_indisponivel}) — a desta sessão é "
                          f"provisória")
                    c.output_dir = c.saida_indisponivel
                else:
                    c.output_dir = nova
            c.save()
            depois = str(self._saida())
            mudou = depois != antes
        except Exception as e:  # noqa: BLE001
            return json.dumps({"ok": False, "msg": f"não deu pra salvar: {e}"})

        # O LOG FICA FORA DO TRY. Este print estava lá dentro e tinha um "→":
        # num console cp1252 ele levanta UnicodeEncodeError, o `except` amplo
        # pegava, e um Salvar que FUNCIONOU voltava pra tela como
        # "não deu pra salvar". A operação já tinha acontecido — só o relato
        # é que mentia. Registrar não pode ter voto no resultado.
        try:
            print(f"    [python] configuracoes gravadas (preset={preset})"
                  + (f" - saida {antes} -> {depois}" if mudou else ""))
        except Exception:  # noqa: BLE001
            pass
        # Quem chamou precisa saber que a SAÍDA mudou, não só que salvou:
        # a Biblioteca inteira é lida de lá e acabou de ficar velha.
        return json.dumps({
            "ok": True, "msg": "Configurações salvas.",
            "saida_mudou": mudou, "saida": depois,
        })

    @Slot(result=str)
    def escolher_pasta_saida(self) -> str:
        from PySide6.QtWidgets import QFileDialog

        d = QFileDialog.getExistingDirectory(None, "Pasta de saída", str(self._saida()))
        return d or ""

    # ---- prévia -----------------------------------------------------------
    @Slot(int, result=str)
    def previa(self, idx: int) -> str:
        """O Chromium do PySide6 não traz H.264 — só codecs livres. Como TODO
        clipe do app é H.264, o <video> apontado direto pro .mp4 morre com
        SRC_NOT_SUPPORTED.

        Saída: uma prévia VP8/WebM a 640px, feita na hora e guardada. Medido:
        0,14 s por clipe de ~4 s (o VP9 em 1080p levava 1,9 s — caro demais
        pra quem só quer ver a cena). O .mp4 original não é tocado.
        """
        clipe = self.raiz / "shots" / f"{idx:04d}.mp4"
        if not clipe.exists():
            return ""
        destino = self.previas / f"{idx:04d}.webm"
        if not destino.exists():
            import subprocess

            destino.parent.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            r = subprocess.run(
                [self.ffmpeg, "-v", "error", "-y", "-i", str(clipe),
                 "-vf", "scale=640:-2", "-c:v", "libvpx", "-crf", "30", "-b:v", "0",
                 "-cpu-used", "8", "-deadline", "realtime", "-an", str(destino)],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            gasto = (time.perf_counter() - t0) * 1000
            if r.returncode != 0:
                print(f"    [python] ffmpeg falhou: {r.stderr[:150]}")
                return ""
            print(f"    [python] prévia {idx:04d} em {gasto:.0f} ms "
                  f"({destino.stat().st_size // 1024} KB)")
        return "file:///" + str(destino).replace("\\", "/")

    # ---- o "UM botão" da prova de conceito -------------------------------
    @Slot(str)
    def clicou(self, o_que: str) -> None:
        self.cliques.append(o_que)
        print(f"    [python] o JS avisou: clicou em {o_que!r}")

    @Slot(str, float)
    def marca(self, nome: str, ms: float) -> None:
        """O JS devolve os tempos que só ele consegue medir (primeiro
        desenho, grade montada)."""
        self.marcas[nome] = ms
        print(f"    [js] {nome}: {ms:.0f} ms")

    # ---- dados de verdade, vindos do banco -------------------------------
    @Slot(str, result=str)
    def cenas_do_episodio(self, episodio: str) -> str:
        t0 = time.perf_counter()
        con = sqlite3.connect(self.banco)
        con.row_factory = sqlite3.Row

        # A raiz TEM que acompanhar o episódio pedido: sem isto, escolher
        # outro na árvore continuava servindo miniaturas e prévias da pasta
        # do episódio anterior — e o erro é silencioso, as imagens só somem.
        ep = con.execute(
            """SELECT e.season, e.episode, e.anime_id, a.title
                 FROM episode e JOIN anime a ON a.id = e.anime_id
                WHERE e.id = ?""",
            (episodio,),
        ).fetchone()
        if ep is not None:
            self.raiz = self._raiz_do_episodio(ep["title"], ep["season"], ep["episode"])
            self.ep_id = int(episodio)
            self.anime_id = int(ep["anime_id"])

        # as junções deste episódio, uma vez só — são guardadas por TEMPO
        juncoes = [
            dict(m) for m in con.execute(
                "SELECT start, end FROM shot_merge WHERE episode_id = ?",
                (episodio,))
        ]

        linhas = con.execute(
            """SELECT s.id, s.idx, s.file, s.keyframe, s.start, s.end
                 FROM shot s JOIN episode e ON e.id = s.episode_id
                WHERE e.id = ? ORDER BY s.idx""",
            (episodio,),
        ).fetchall()
        nomes: dict[int, list[str]] = {}
        confs: dict[int, list[float]] = {}
        for r in con.execute(
            """SELECT sc.shot_id, c.name, sc.confidence FROM shot_character sc
                 JOIN character c ON c.id = sc.character_id"""
        ):
            nomes.setdefault(r[0], []).append(r[1])
            confs.setdefault(r[0], []).append(round(float(r[2] or 0), 2))
        con.close()

        # guardado pra ação não ter que reconsultar o banco só pra descobrir
        # em qual cena ela bate
        self._shots = {int(r["idx"]): dict(r) for r in linhas}
        self._donos = nomes

        cenas = []
        for r in linhas:
            # O banco guarda os dois caminhos RELATIVOS à pasta do episódio
            # ("shots\0002.mp4"). Mandar isso cru pro navegador fazia a prévia
            # de hover pedir um arquivo que não existe — e falhar em silêncio.
            kf = r["keyframe"] or ""
            if kf and not Path(kf).is_absolute():
                kf = str(self.raiz / kf)
            clipe = r["file"] or ""
            if clipe and not Path(clipe).is_absolute():
                clipe = str(self.raiz / clipe)
            cenas.append(
                {
                    "idx": r["idx"],
                    "ini": round(r["start"] or 0, 1),
                    "fim": round(r["end"] or 0, 1),
                    "dur": round((r["end"] or 0) - (r["start"] or 0), 1),
                    "quem": nomes.get(r["id"], []),
                    # a maquete mostra a confiança na cena escolhida ("0.91 / 0.78")
                    "conf": confs.get(r["id"], []),
                    # BARRA PRA FRENTE, sempre. URL não é caminho do Windows.
                    #
                    # A miniatura sobrevivia com "\" porque vai num
                    # `<img src>`, que é atributo HTML. A TIRA vai num
                    # `background-image: url(...)`, e ali a barra invertida é
                    # ESCAPE de CSS: `\A` virou quebra de linha e `\O`, `\M`,
                    # `\S` sumiram. Medido: a imagem carregava
                    # (`onload em 433ms`), e o valor guardado era
                    # `url("cena:/tira/G:\a pp Corte CenasOutputMush…` — um
                    # caminho destruído, com o servidor respondendo direito o
                    # tempo todo. A prévia nunca aparecia e nada acusava erro.
                    # esta cena faz parte de uma junção? O menu precisa saber
                    # pra não oferecer "Desfazer junção" em cena que nunca foi
                    # juntada — o item aparecia em TODAS, e clicar respondia
                    # "Esta cena não faz parte de uma junção", o que é dizer
                    # depois o que dava pra saber antes
                    "juntada": any(
                        m["start"] - 0.05 <= (r["start"] + r["end"]) / 2.0 <= m["end"] + 0.05
                        for m in juncoes),
                    "mini": "cena:/mini/" + kf.replace("\\", "/") if kf else "",
                    "tira": "cena:/tira/" + clipe.replace("\\", "/") if clipe else "",
                    "clipe": "file:///" + clipe.replace("\\", "/") if clipe else "",
                }
            )
        print(f"    [python] {len(cenas)} cenas do banco em {(time.perf_counter()-t0)*1000:.0f} ms")
        return json.dumps(cenas)
