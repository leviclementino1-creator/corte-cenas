"""Prova de conceito: a Biblioteca desenhada pelo Chromium, alimentada pelo Python.

Não substitui nada. Abre uma janela própria, lê o banco de verdade (331 cenas
do Mushoku T3 E2), serve as miniaturas dos keyframes reais e mede o que
importa pra decidir se a migração compensa:

    tempo até a janela aparecer · tempo até as 331 miniaturas na tela ·
    memória (somando os processos do Chromium) · a ponte JS→Python funciona ·
    o vídeo em loop toca

    python poc_web/poc.py            janela normal
    python poc_web/poc.py --foto     tira o retrato e fecha sozinho
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

T_IMPORT = time.perf_counter()

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWebEngineCore import (  # noqa: E402
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)

from poc_web.ponte import Ponte, ServidorMiniatura, registra_esquema  # noqa: E402

EPISODIO = "3"
SAIDA_EP = RAIZ / "Output" / "Mushoku" / "S03E02"
PAGINA = RAIZ / "poc_web" / "app_poc.html"


def acha_ffmpeg() -> str:
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for c in (RAIZ / "ffmpeg" / "bin" / "ffmpeg.exe", RAIZ / "ffmpeg.exe"):
        if c.exists():
            return str(c)
    return "ffmpeg"


FFMPEG = acha_ffmpeg()


def memoria() -> tuple[float, int]:
    """RSS de tudo: o processo Python + os QtWebEngineProcess.exe filhos."""
    import psutil

    eu = psutil.Process(os.getpid())
    total = eu.memory_info().rss
    n = 0
    for f in eu.children(recursive=True):
        try:
            total += f.memory_info().rss
            n += 1
        except psutil.Error:
            pass
    return total / 1024 / 1024, n


class PaginaFalante(QWebEnginePage):
    """Sem isso, um erro de JS ou um recurso bloqueado somem em silêncio —
    foi assim que as 331 miniaturas 'carregaram' em 183 ms sem existir."""

    def javaScriptConsoleMessage(self, nivel, msg, linha, fonte):  # noqa: N802
        print(f"    [console:{nivel.name if hasattr(nivel,'name') else nivel}] {msg} ({linha})")


class Janela(QMainWindow):
    def __init__(self, servidor: ServidorMiniatura) -> None:
        super().__init__()
        self.setWindowTitle("Corte Cenas — prova de conceito (WebEngine)")
        self.resize(1600, 940)

        self.vista = QWebEngineView(self)
        self.vista.setPage(PaginaFalante(self.vista))
        s = self.vista.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        # Sem isso o <video> em file:// não toca sem clique do usuário.
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        self.ponte = Ponte(RAIZ / "cache" / "index.db", SAIDA_EP, ffmpeg=FFMPEG)
        self.canal = QWebChannel(self)
        self.canal.registerObject("ponte", self.ponte)
        self.vista.page().setWebChannel(self.canal)
        self.setCentralWidget(self.vista)
        self.servidor = servidor


def retratos(jan, app) -> None:
    """Uma foto de cada tela do PoC, pra comparar com as do app Qt."""
    fila = [
        ("web_analisar", "document.querySelector('[data-tela=analisar]').click()"),
        ("web_biblioteca", "document.querySelector('[data-tela=biblioteca]').click()"),
        ("web_resultados", "document.querySelector('[data-tela=resultados]').click()"),
        ("web_configuracoes", "document.getElementById('abrir_config').click()"),
    ]

    def passo(i: int) -> None:
        if i >= len(fila):
            print("\nretratos em poc_web/web_*.png")
            QTimer.singleShot(300, app.quit)
            return
        nome, js = fila[i]
        jan.vista.page().runJavaScript(js)
        # dá tempo do layout assentar e das miniaturas chegarem
        QTimer.singleShot(2200, lambda: (
            jan.grab().save(str(RAIZ / "poc_web" / f"{nome}.png")),
            print(f"  {nome}.png"),
            passo(i + 1),
        ))

    passo(0)


def main() -> int:
    foto = "--foto" in sys.argv
    galeria = "--telas" in sys.argv

    registra_esquema()  # ANTES do QApplication
    t_app = time.perf_counter()
    app = QApplication(sys.argv)
    print(f"[1] import + QApplication ....... {(t_app - T_IMPORT)*1000:>7.0f} ms")

    servidor = ServidorMiniatura(PAGINA)
    QWebEngineProfile.defaultProfile().installUrlSchemeHandler(b"cena", servidor)

    t_jan = time.perf_counter()
    jan = Janela(servidor)
    jan.show()
    print(f"[2] janela na tela .............. {(time.perf_counter() - t_jan)*1000:>7.0f} ms")

    t_load = time.perf_counter()
    marcos: dict[str, float] = {}

    def carregou(ok: bool) -> None:
        marcos["load"] = (time.perf_counter() - t_load) * 1000
        print(f"[3] HTML carregado {'':<12} {marcos['load']:>7.0f} ms  ({'ok' if ok else 'FALHOU'})")

    jan.vista.loadFinished.connect(carregou)
    jan.vista.load(QUrl("cena:/pagina"))

    SONDA = r"""
    (() => {
      const c = document.querySelector('.cartao');
      if (!c) return 'nenhum cartão no DOM';
      const q = c.querySelector('.quadro'), i = c.querySelector('img'), p = c.querySelector('.pe');
      const r = n => n ? `${n.getBoundingClientRect().width.toFixed(0)}x${n.getBoundingClientRect().height.toFixed(0)}` : '-';
      const cs = getComputedStyle(c);
      const filhos = [...c.children].map(x =>
        `${x.className}[${getComputedStyle(x).position},${getComputedStyle(x).display},h=${getComputedStyle(x).height}]`
      ).join(' ');
      return [
        `cartao ${r(c)}  quadro ${r(q)}  pe ${r(p)}`,
        `cartao: display=${cs.display} pos=${cs.position} h=${cs.height} off=${c.offsetHeight} scroll=${c.scrollHeight} contain=${cs.contain}`,
        `filhos: ${filhos}`,
        `img ${r(i)} natural=${i.naturalWidth}x${i.naturalHeight} complete=${i.complete}`,
        `src ${i.getAttribute('src').slice(0,60)}`,
        `linhas da grade: ${getComputedStyle(document.querySelector('.pista')).gridTemplateRows.slice(0,60)}`,
        `colunas: ${getComputedStyle(document.querySelector('.pista')).gridTemplateColumns}`,
      ].join('\n     ');
    })()
    """

    # O ponto fraco do caminho Qt: contar colunas e manter o respiro igual dos
    # quatro lados. Aqui é medido, não confiado.
    COLUNAS = r"""
    (() => {
      const pista = document.querySelector('.pista');
      const caixa = document.querySelector('.grade');
      const cols = getComputedStyle(pista).gridTemplateColumns.split(' ');
      const cs = getComputedStyle(caixa);
      const c0 = document.querySelector('.cartao').getBoundingClientRect();
      const cN = [...document.querySelectorAll('.cartao')].slice(0, cols.length).pop().getBoundingClientRect();
      const r = caixa.getBoundingClientRect();
      return `${cols.length} colunas de ${parseFloat(cols[0]).toFixed(0)}px `
           + `· esquerda ${(c0.left - r.left).toFixed(0)} `
           + `· direita ${(r.right - cN.right - 10).toFixed(0)} (fora a barra) `
           + `· topo ${cs.paddingTop}`;
    })()
    """

    # Quatro suspeitos pra linha de 2px. Cada um é aplicado ao vivo e a
    # altura do cartão é medida de novo — em vez de trocar o CSS no escuro.
    AB = r"""
    (() => {
      const grade = document.querySelector('.grade');
      const alvo  = document.querySelector('.cartao');
      const h = () => alvo.getBoundingClientRect().height.toFixed(0);
      const linha = () => getComputedStyle(grade).gridTemplateRows.split(' ')[0];
      const out = [`antes: cartao=${h()} linha=${linha()}`];
      const testes = [
        ['cartao sem overflow:hidden', () => document.querySelectorAll('.cartao').forEach(c => c.style.overflow='visible')],
        ['grid-auto-rows:max-content', () => grade.style.gridAutoRows='max-content'],
        ['align-content padrão',       () => grade.style.alignContent='normal'],
        ['quadro com altura fixa',     () => document.querySelectorAll('.quadro').forEach(q => {q.style.aspectRatio='auto'; q.style.height='110px';})],
      ];
      for (const [nome, aplica] of testes) {
        aplica(); grade.offsetHeight;
        out.push(`${nome.padEnd(30)} -> cartao=${h()} linha=${linha()}`);
      }
      return out.join('\n     ');
    })()
    """

    # O passo 4 do plano: um botão do HTML chamando Python de verdade. Clica
    # sozinho pra prova valer sem depender de alguém estar olhando.
    CLIQUE = r"""
    (() => {
      document.getElementById('btn_ponte').click();
      const c = document.querySelectorAll('.cartao')[7];
      c.dispatchEvent(new MouseEvent('click', {bubbles:true}));
      return 'cliquei no botão e no cartão 7';
    })()
    """
    VIDEO = r"""
    (() => {
      const v = document.getElementById('tv');
      const p = document.createElement('video');
      const ERROS = {1:'ABORTED', 2:'NETWORK', 3:'DECODE', 4:'SRC_NOT_SUPPORTED'};
      return [
        `src=${v.currentSrc ? v.currentSrc.split('/').pop() : '(vazio)'} `
        + `pronto=${v.readyState}/4 tocando=${!v.paused} loop=${v.loop} `
        + `pos=${v.currentTime.toFixed(2)}s de ${isNaN(v.duration)?'?':v.duration.toFixed(2)}s `
        + `${v.videoWidth}x${v.videoHeight}`,
        `erro=${v.error ? ERROS[v.error.code] + ' — ' + v.error.message : 'nenhum'}`,
        `codecs: h264+aac="${p.canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"')||'NÃO'}" `
        + `h264="${p.canPlayType('video/mp4; codecs="avc1.42E01E"')||'NÃO'}" `
        + `webm-vp9="${p.canPlayType('video/webm; codecs="vp9"')||'NÃO'}"`,
      ].join('\n     ');
    })()
    """

    def clica() -> None:
        jan.vista.page().runJavaScript(CLIQUE, lambda s: print(f"    [teste] {s}"))
        # dá tempo do <video> abrir o mp4 antes de perguntar se está tocando
        QTimer.singleShot(2500, lambda: jan.vista.page().runJavaScript(
            VIDEO, lambda s: print(f"[V] vídeo: {s}")))

    def relatorio() -> None:
        jan.vista.page().runJavaScript(SONDA, lambda s: print(f"[S] sonda: {s}"))
        jan.vista.page().runJavaScript(AB, lambda s: print(f"[AB] {s}"))
        mb, filhos = memoria()
        m = jan.ponte.marcas
        print()
        print("=" * 62)
        print(f"[4] canal QWebChannel pronto .... {m.get('canal_pronto', -1):>7.0f} ms")
        cartoes = next((v for k, v in m.items() if k.startswith("montou_")), -1)
        quantos = next((k for k in m if k.startswith("montou_")), "montou_?")
        print(f"[5] {quantos.replace('montou_','').replace('_cartoes',' cartões no DOM'):<28}"
              f"{cartoes:>7.0f} ms")
        print(f"[6] todas as miniaturas na tela . {m.get('todas_miniaturas', -1):>7.0f} ms")
        print(f"[7] memória total ............... {mb:>7.0f} MB  ({filhos} processos filhos)")
        print(f"[8] servidor de miniaturas ...... {servidor.resumo()}")
        print(f"[9] ponte JS->Python ............ {len(jan.ponte.cliques)} chamadas recebidas")
        print("=" * 62)

        # o retrato sai depois do teste de redimensionamento (ver acabou())

    def mede_larguras(larguras: list[int], fim) -> None:
        """Redimensiona a janela e pergunta ao layout quantas colunas deu."""
        if not larguras:
            fim()
            return
        w = larguras[0]
        jan.resize(w, 940)

        def leu(s: str) -> None:
            print(f"    {w:>5}px -> {s}")
            QTimer.singleShot(150, lambda: mede_larguras(larguras[1:], fim))

        # dois quadros de folga pro layout assentar antes de medir
        QTimer.singleShot(320, lambda: jan.vista.page().runJavaScript(COLUNAS, leu))

    if galeria:
        # só as fotos: sem A/B, sem teste de redimensionamento
        QTimer.singleShot(4000, lambda: retratos(jan, app))
        return app.exec()

    QTimer.singleShot(4000, clica)
    QTimer.singleShot(9000 if foto else 12000, relatorio)

    def resize_e_encerra() -> None:
        print("\n[R] dynamic resize (colunas e respiro em cada largura):")

        def acabou() -> None:
            jan.resize(1600, 940)
            if foto:
                QTimer.singleShot(600, lambda: (
                    jan.grab().save(str(RAIZ / "poc_web" / "retrato.png")),
                    print(f"retrato: {RAIZ / 'poc_web' / 'retrato.png'}"),
                    QTimer.singleShot(300, app.quit),
                ))

        mede_larguras([980, 1180, 1280, 1440, 1600, 1920], acabou)

    QTimer.singleShot(10000 if foto else 13000, resize_e_encerra)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
