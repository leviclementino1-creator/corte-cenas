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

    # Python -> JS: o mesmo modelo de sinal que a UI Qt já usa hoje
    progresso = Signal(int, str)
    cenasProntas = Signal(str)
    arquivoSolto = Signal(str)   # o episódio arrastado pra janela

    def __init__(self, banco: Path, raiz_saida: Path, ffmpeg: str = "ffmpeg", parent=None) -> None:
        super().__init__(parent)
        self.banco = banco
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
        self._shots: dict[int, dict] = {}     # idx -> linha do shot
        self._donos: dict[int, list] = {}     # shot_id -> personagens

    @property
    def db(self):
        from app.storage.db import Database

        return Database(self.banco)

    @property
    def cfg(self):
        from app.config import Config

        return Config.load()

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

        animes: dict[str, dict] = {}
        for r in linhas:
            raiz = self._raiz_do_episodio(r["title"], r["season"], r["episode"])
            existe = (raiz / "shots").exists()
            a = animes.setdefault(r["title"], {"titulo": r["title"], "temporadas": {}})
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
                "temporadas": temps,
                "cenas": sum(t["cenas"] for t in temps),
            })
        return json.dumps(fora)

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

    # ---- curadoria --------------------------------------------------------
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
        if cid is None:
            return json.dumps({
                "ok": False,
                "msg": "Esta cena não está na pasta de ninguém — não há de "
                       "onde remover nem de onde mover.",
            })

        alvo_id = None
        if acao == "mover":
            alvo_id = self._id_do_personagem(d.get("para") or "")
            if alvo_id is None:
                return json.dumps({"ok": False, "msg": "destino inválido"})

        db = self.db
        from app.storage.organizer import refresh_shot_links

        for r in linhas:
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
        if acao == "mover":
            msg = f"{quantas} cena(s) de {de} → {d.get('para')}"
        else:
            msg = f"{quantas} cena(s) fora da pasta de {de}"
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
        with db.connect() as c:
            linhas = c.execute(
                """SELECT e.id, e.season, e.episode, a.title
                     FROM episode e JOIN anime a ON a.id = e.anime_id
                    WHERE e.id IN (%s)""" % ",".join("?" * len(ids)),
                ids,
            ).fetchall()

        for r in linhas:
            raiz = self._raiz_do_episodio(r["title"], r["season"], r["episode"])
            try:
                if enviar_para_lixeira(raiz, saida) is not None:
                    movidas += 1
            except Exception as e:  # noqa: BLE001
                print(f"    [python] lixeira de {raiz}: {e}")
            db.delete_episode(int(r["id"]))
            apagados += 1

        if self.ep_id in ids:
            self.ep_id = None
        msg = (f"{apagados} episódio(s) fora do acervo · "
               f"{movidas} pasta(s) na lixeira (Output/_lixeira)")
        print(f"    [python] {msg}")
        return json.dumps({"ok": True, "msg": msg})

    @Slot(str)
    def abrir_pasta(self, qual: str) -> None:
        """Abre no Explorer. `qual` é 'episodio' ou o nome de um personagem."""
        import subprocess

        alvo = self.raiz if qual == "episodio" else self.raiz / "by_character" / qual
        if not alvo.exists():
            alvo = self.raiz
        if alvo.exists():
            subprocess.Popen(["explorer", str(alvo)])
            print(f"    [python] abri {alvo}")

    @Slot(str)
    def atalho(self, tecla: str) -> None:
        self.cliques.append(f"tecla:{tecla}")
        print(f"    [python] atalho -> {tecla!r}")

    @Slot(result=str)
    def config(self) -> str:
        """O que a tela de Analisar e o diálogo de Configurações mostram nos
        campos. Vem do Config real do app, não de texto fixo."""
        try:
            return json.dumps({"saida": str(self._saida())})
        except Exception as e:  # noqa: BLE001 — o PoC não pode morrer por isso
            return json.dumps({"saida": str(self.raiz.parent.parent), "erro": str(e)})

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

        linhas = con.execute(
            """SELECT s.id, s.idx, s.file, s.keyframe, s.start, s.end
                 FROM shot s JOIN episode e ON e.id = s.episode_id
                WHERE e.id = ? ORDER BY s.idx""",
            (episodio,),
        ).fetchall()
        nomes = {}
        for r in con.execute(
            """SELECT sc.shot_id, c.name FROM shot_character sc
                 JOIN character c ON c.id = sc.character_id"""
        ):
            nomes.setdefault(r[0], []).append(r[1])
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
                    "dur": round((r["end"] or 0) - (r["start"] or 0), 1),
                    "quem": nomes.get(r["id"], []),
                    "mini": f"cena:/mini/{kf}" if kf else "",
                    "tira": f"cena:/tira/{clipe}" if clipe else "",
                    "clipe": "file:///" + clipe.replace("\\", "/") if clipe else "",
                }
            )
        print(f"    [python] {len(cenas)} cenas do banco em {(time.perf_counter()-t0)*1000:.0f} ms")
        return json.dumps(cenas)
