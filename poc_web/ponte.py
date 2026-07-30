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

    def __init__(self, banco: Path, raiz_saida: Path, ffmpeg: str = "ffmpeg", parent=None) -> None:
        super().__init__(parent)
        self.banco = banco
        self.raiz = raiz_saida
        self.ffmpeg = ffmpeg
        self.previas = raiz_saida / "metadata" / "previas_web"
        self.cliques: list[str] = []
        self.marcas: dict[str, float] = {}

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

        cenas = []
        for r in linhas:
            kf = r["keyframe"] or ""
            if kf and not Path(kf).is_absolute():
                kf = str(self.raiz / kf)
            cenas.append(
                {
                    "idx": r["idx"],
                    "dur": round((r["end"] or 0) - (r["start"] or 0), 1),
                    "quem": nomes.get(r["id"], []),
                    "mini": f"cena:/mini/{kf}" if kf else "",
                    "clipe": "file:///" + str(r["file"]).replace("\\", "/") if r["file"] else "",
                }
            )
        print(f"    [python] {len(cenas)} cenas do banco em {(time.perf_counter()-t0)*1000:.0f} ms")
        return json.dumps(cenas)
