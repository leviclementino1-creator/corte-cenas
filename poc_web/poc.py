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

# Congelado, `__file__` aponta pro descompactado temporário: os dados (banco,
# Output) continuam na pasta do projeto, mas a PÁGINA viaja dentro do exe.
CONGELADO = getattr(sys, "frozen", False)
if CONGELADO:
    RAIZ = Path(sys.executable).resolve().parent
    RECURSOS = Path(getattr(sys, "_MEIPASS", RAIZ))
    # o PoC lê o banco e os clipes do projeto, não da pasta do exe
    PROJETO = Path(r"G:\App Corte Cenas")
else:
    RAIZ = Path(__file__).resolve().parent.parent
    RECURSOS = RAIZ
    PROJETO = RAIZ
sys.path.insert(0, str(RAIZ))

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget  # noqa: E402
from PySide6.QtWebChannel import QWebChannel  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PySide6.QtWebEngineCore import (  # noqa: E402
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)

from poc_web.ponte import Ponte, ServidorMiniatura, registra_esquema  # noqa: E402

EPISODIO = "3"
SAIDA_EP = PROJETO / "Output" / "Mushoku" / "S03E02"
PAGINA = RECURSOS / "poc_web" / "app_poc.html"


def acha_ffmpeg() -> str:
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for c in (PROJETO / "ffmpeg" / "bin" / "ffmpeg.exe", PROJETO / "ffmpeg.exe"):
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
        # O Chromium tem menu de contexto próprio (Recarregar, Ver código…).
        # Desligado: quem desenha o menu é a página.
        self.vista.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # O arquivo arrastado é recebido pelo QT, não pelo HTML — ver soltou().
        self.setAcceptDrops(True)
        s = self.vista.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        # Sem isso o <video> em file:// não toca sem clique do usuário.
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        self.ponte = Ponte(PROJETO / "cache" / "index.db", SAIDA_EP, ffmpeg=FFMPEG)
        self.canal = QWebChannel(self)
        self.canal.registerObject("ponte", self.ponte)
        self.vista.page().setWebChannel(self.canal)
        self.setCentralWidget(self.vista)
        self.servidor = servidor

        self._vigiados: set[int] = set()

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        # O widget de render só nasce depois que a janela aparece: filtrar no
        # __init__ pegava focusProxy() == None e o drop sumia.
        QTimer.singleShot(0, self._vigiar_drop)

    def _vigiar_drop(self) -> None:
        """ARMADILHA: dentro do QWebEngineView quem recebe o arrastar é um
        widget-filho de render, não a vista — e o HTML5 até recebe o drop, mas
        o objeto File do Chromium NÃO tem caminho, e o ffmpeg precisa do
        caminho. Então quem trata é o Qt, e o caminho nunca sai do Python."""
        alvos = [self.vista, self.vista.focusProxy(), *self.vista.findChildren(QWidget)]
        novos = 0
        for a in alvos:
            if a is None or id(a) in self._vigiados:
                continue
            self._vigiados.add(id(a))
            a.setAcceptDrops(True)
            a.installEventFilter(self)
            novos += 1
        if novos:
            print(f"    [python] vigiando o arrastar em {novos} widget(s)")

    def eventFilter(self, obj, ev):  # noqa: N802
        tipo = ev.type()
        if tipo in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                return True
        elif tipo == QEvent.Type.Drop:
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                self.soltou(ev.mimeData().urls())
                return True
        return super().eventFilter(obj, ev)

    def soltou(self, urls) -> None:
        for u in urls:
            caminho = u.toLocalFile()
            if caminho.lower().endswith((".mkv", ".mp4", ".avi")):
                print(f"    [python] episódio solto na janela: {caminho}")
                self.ponte.arquivoSolto.emit(caminho)
                return
        print("    [python] soltaram algo que não é vídeo")

    # o mesmo caminho quando o drop cai fora da vista (na moldura da janela)
    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self.soltou(ev.mimeData().urls())


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
            jan.grab().save(str(PROJETO / "poc_web" / f"{nome}.png")),
            print(f"  {nome}.png"),
            passo(i + 1),
        ))

    passo(0)


def testa_interacoes(jan, app) -> None:
    """As quatro coisas que só dá pra provar DENTRO do WebEngine:
    menu de contexto, atalhos de teclado, arrastar-soltar e diálogo nativo."""
    from PySide6.QtCore import QMimeData, QPoint, QPointF
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QDialog

    print("\n" + "=" * 62)
    print("TESTE DAS INTERAÇÕES")
    print("=" * 62)

    MENU = r"""
    (() => {
      const c = document.querySelectorAll('.cartao')[3];
      c.dispatchEvent(new MouseEvent('contextmenu',
        {bubbles:true, clientX:400, clientY:300}));
      const m = document.getElementById('menu');
      const visivel = m.classList.contains('viva');
      const r = m.getBoundingClientRect();
      if (visivel) m.querySelector('[data-acao=remover]').click();
      return `menu ${visivel ? 'apareceu' : 'NÃO apareceu'} em ${r.left.toFixed(0)},${r.top.toFixed(0)} `
           + `(${r.width.toFixed(0)}x${r.height.toFixed(0)}) · cliquei em "remover"`;
    })()
    """

    TECLAS = r"""
    (() => {
      const bate = (k, ctrl) => document.dispatchEvent(
        new KeyboardEvent('keydown', {key:k, ctrlKey:!!ctrl, bubbles:true}));
      const antes = document.querySelector('#grade .cartao.viva');
      bate('ArrowRight'); bate('ArrowRight');
      const depois = document.querySelector('#grade .cartao.viva');
      bate('j'); bate('m'); bate('Delete'); bate('p', true);
      return `setas: ${antes ? antes.dataset.idx : '—'} -> ${depois ? depois.dataset.idx : '—'}`
           + ` · mandei J, M, Del e Ctrl+P`;
    })()
    """

    # A prévia estilo YouTube: passar o mouse tem que fazer a CENA ANDAR,
    # não só mexer a barrinha.
    # `runJavaScript` NÃO espera Promise — devolve nulo na hora. Então o
    # hover é disparado num passo e lido no seguinte.
    HOVER = r"""
    (() => {
      const c = document.querySelectorAll('.cartao')[2];
      const r = c.getBoundingClientRect();
      c.dispatchEvent(new MouseEvent('mousemove',
        {bubbles:true, clientX:r.left + r.width*0.1, clientY:r.top + r.height/2}));
      return 'passei o mouse no cartão 2';
    })()
    """
    LE_TIRA = r"""
    (() => {
      const c = document.querySelectorAll('.cartao')[2];
      const t = c.querySelector('.tira');
      const r = c.getBoundingClientRect();
      const lidos = [];
      for (const f of [0.05, 0.3, 0.6, 0.95]) {
        c.dispatchEvent(new MouseEvent('mousemove',
          {bubbles:true, clientX:r.left + r.width*f, clientY:r.top + r.height/2}));
        lidos.push(t.style.backgroundPositionX || '0%');
      }
      const fundo = getComputedStyle(t).backgroundImage;
      return `tira ${t.dataset.carregada ? 'CARREGOU' : 'não carregou'}`
           + ` (${fundo === 'none' ? 'sem imagem' : fundo.length + ' chars de url'})`
           + ` · quadro em 5/30/60/95% da largura: ${lidos.join('  ')}`;
    })()
    """

    def passo0() -> None:
        jan.vista.page().runJavaScript(HOVER, lambda s: print(f"[0] {s}"))
        QTimer.singleShot(1800, passo0b)

    def passo0b() -> None:
        jan.vista.page().runJavaScript(LE_TIRA, lambda s: print(f"[0] {s}"))
        QTimer.singleShot(700, passo1)

    def passo1() -> None:
        jan.vista.page().runJavaScript(MENU, lambda s: print(f"[1] {s}"))
        QTimer.singleShot(600, passo2)

    def passo2() -> None:
        jan.vista.page().runJavaScript(TECLAS, lambda s: print(f"[2] {s}"))
        QTimer.singleShot(600, passo3)

    def passo3() -> None:
        # Um drop de verdade: mesmo evento que o Explorer manda.
        origem = SAIDA_EP / "shots" / "0000.mp4"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(origem))])
        alvo = jan.vista.focusProxy() or jan.vista
        from PySide6.QtGui import QDragEnterEvent

        entrada = QDragEnterEvent(
            QPoint(700, 400), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        app.sendEvent(alvo, entrada)
        ev = QDropEvent(
            QPointF(QPoint(700, 400)), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        app.sendEvent(alvo, ev)
        print(f"    [teste] mandei DragEnter+Drop pra {type(alvo).__name__}")
        QTimer.singleShot(500, lambda: jan.vista.page().runJavaScript(
            "document.getElementById('c_arquivo').textContent",
            lambda s: (print(f"[3] campo Arquivo agora diz: {s[-46:]!r}"), passo4())))

    def passo4() -> None:
        # Abre o diálogo nativo e fecha sozinho — só provando que ele sobe
        # com o WebEngine na tela sem travar a página.
        def fecha() -> None:
            for w in app.topLevelWidgets():
                if isinstance(w, QDialog) and w.isVisible():
                    print(f"[4] diálogo nativo abriu: {w.windowTitle()!r} — fechando")
                    w.reject()
                    return
            print("[4] diálogo nativo NÃO apareceu")

        QTimer.singleShot(900, fecha)
        caminho = jan.ponte.escolher_arquivo()
        print(f"    devolveu {caminho!r} (vazio = cancelado, esperado)")
        QTimer.singleShot(400, fim)

    def fim() -> None:
        print("\nchamadas que chegaram no Python:")
        for c in jan.ponte.cliques:
            print(f"    · {c}")
        print("=" * 62)
        QTimer.singleShot(300, app.quit)

    passo0()


def testa_acervo(jan, app) -> None:
    """Árvore do acervo, troca de episódio, filtro por personagem e
    ordenação — os controles que até agora eram só desenho."""
    print("\n" + "=" * 62)
    print("TESTE DO ACERVO, FILTRO E ORDEM")
    print("=" * 62)

    ARVORE = r"""
    (() => {
      const eps = [...document.querySelectorAll('#arvore .no[data-ep]')];
      const viva = document.querySelector('#arvore .no.viva');
      return `${document.querySelectorAll('#arvore .no').length} nós, `
           + `${eps.length} episódios (${eps.filter(e => e.classList.contains('sumido')).length} sumidos)`
           + ` · aberto: ${viva ? viva.dataset.rot : 'NENHUM'}`
           + ` · grade com ${document.querySelectorAll('#grade .cartao').length} cartões`;
    })()
    """

    FILTRA = r"""
    (() => {
      const pilulas = [...document.querySelectorAll('#filtros .pilula')];
      const alvo = pilulas.find(p => p.dataset.nome && p.dataset.nome !== '__sem__');
      if (!alvo) return 'sem pílulas de personagem';
      const antes = document.querySelectorAll('#grade .cartao').length;
      alvo.click();
      const depois = document.querySelectorAll('#grade .cartao').length;
      const nome = alvo.dataset.nome;
      const so = [...document.querySelectorAll('#grade .cartao .nome')]
        .every(n => n.textContent.includes(nome.split(',')[0]));
      return `filtrei por "${nome}": ${antes} -> ${depois} cartões · `
           + `todos são dele? ${so ? 'sim' : 'NÃO'}`;
    })()
    """

    ORDENA = r"""
    (() => {
      const fora = [];
      for (const modo of ['duvidosas', 'longas', 'crono']) {
        document.querySelector(`.ordem .opcao[data-ordem=${modo}]`).click();
        const tres = [...document.querySelectorAll('#grade .cartao')].slice(0,3)
          .map(c => c.querySelector('.dur').textContent);
        fora.push(`${modo}: ${tres.join(' ')}`);
      }
      return fora.join(' · ');
    })()
    """

    TROCA = r"""
    (() => {
      const eps = [...document.querySelectorAll('#arvore .no[data-ep]:not(.sumido)')];
      if (eps.length < 2) return `só ${eps.length} episódio com pasta — não dá pra testar a troca`;
      const outro = eps[1];
      outro.click();
      return `cliquei em ${outro.dataset.rot}`;
    })()
    """

    def p1() -> None:
        jan.vista.page().runJavaScript(ARVORE, lambda s: print(f"[1] árvore: {s}"))
        QTimer.singleShot(700, p2)

    def p2() -> None:
        jan.vista.page().runJavaScript(FILTRA, lambda s: print(f"[2] {s}"))
        QTimer.singleShot(700, p3)

    def p3() -> None:
        jan.vista.page().runJavaScript(ORDENA, lambda s: print(f"[3] ordem — {s}"))
        QTimer.singleShot(700, p4)

    def p4() -> None:
        jan.vista.page().runJavaScript(TROCA, lambda s: print(f"[4] {s}"))
        QTimer.singleShot(2500, p5)

    def p5() -> None:
        jan.vista.page().runJavaScript(ARVORE, lambda s: print(f"[5] depois da troca: {s}"))
        QTimer.singleShot(600, fim)

    def fim() -> None:
        print(f"[6] servidor: {jan.servidor.resumo()}")
        print("=" * 62)
        QTimer.singleShot(300, app.quit)

    p1()


def testa_acoes(jan, app) -> None:
    """As ações de curadoria PELA INTERFACE.

    Cuidado: este modo fala com o banco de verdade. Então só faz o que dá pra
    desfazer — junta e desfaz (líquido zero) — e nas destrutivas confere que a
    pergunta aparece, cancelando. Quem testa remover/mover de verdade é o
    fixture isolado em scratchpad/t_acoes.py."""
    print("\n" + "=" * 62)
    print("TESTE DAS AÇÕES (sem mexer na curadoria)")
    print("=" * 62)

    JUNTA = r"""
    (() => {
      const c = document.querySelectorAll('#grade .cartao')[10];
      c.click();
      pedeAcao('juntar', [+c.dataset.idx]);
      return 'mandei juntar a cena ' + c.dataset.idx;
    })()
    """
    RECADO = "document.getElementById('recado').textContent"
    DESJUNTA = r"""
    (() => {
      const c = document.querySelector('#grade .cartao.viva');
      pedeAcao('desjuntar', [+c.dataset.idx]);
      return 'mandei desfazer';
    })()
    """
    PERGUNTA_MOVER = r"""
    (() => {
      const c = [...document.querySelectorAll('#grade .cartao')]
        .find(x => !x.querySelector('.nome').classList.contains('vazio'));
      c.click();
      pedeAcao('mover', [+c.dataset.idx]);
      return 'pedi pra mover a cena ' + c.dataset.idx;
    })()
    """
    LE_CAIXINHA = r"""
    (() => {
      const cx = document.getElementById('caixinha');
      const aberta = cx.classList.contains('viva');
      const ops = [...document.querySelectorAll('#cx_lista .op')].length;
      const t = document.getElementById('cx_titulo').textContent;
      if (aberta) document.getElementById('cx_nao').click();   // CANCELA
      return `caixinha ${aberta ? 'abriu' : 'NÃO abriu'}: "${t}" com ${ops} opções · cancelei`;
    })()
    """

    def js(codigo, rotulo, proximo, espera=1200):
        jan.vista.page().runJavaScript(codigo, lambda s: print(f"    {rotulo} {s}"))
        QTimer.singleShot(espera, proximo)

    def p1():
        js(JUNTA, "[1]", p2)

    def p2():
        jan.vista.page().runJavaScript(RECADO, lambda s: print(f"[1] resposta: {s}"))
        QTimer.singleShot(2500, p3)

    def p3():
        js(DESJUNTA, "[2]", p4)

    def p4():
        jan.vista.page().runJavaScript(RECADO, lambda s: print(f"[2] resposta: {s}"))
        QTimer.singleShot(2500, p5)

    def p5():
        js(PERGUNTA_MOVER, "[3]", p6)

    def p6():
        jan.vista.page().runJavaScript(LE_CAIXINHA, lambda s: print(f"[3] {s}"))
        QTimer.singleShot(800, fim)

    def fim():
        print("=" * 62)
        QTimer.singleShot(300, app.quit)

    p1()


def testa_progresso(jan, app) -> None:
    """A última incógnita da arquitetura: sinal de thread de fundo chega na
    página, e ela continua respondendo enquanto isso."""
    print("\n" + "=" * 62)
    print("TESTE DO PROGRESSO (thread de fundo -> página)")
    print("=" * 62)

    # O que fazia a tela tremer era a fileira mudando de altura quando o
    # rótulo longo quebrava em duas linhas. Isso é medível e estável: se só
    # aparecer UMA altura durante o processo inteiro, nada pulou.
    ESPIA = r"""
    (() => {
      window.__alturas = new Set();
      window.__timer = setInterval(() => {
        document.querySelectorAll('.etapa').forEach(e =>
          window.__alturas.add(Math.round(e.getBoundingClientRect().height)));
      }, 60);
      return 'medindo a altura das fileiras';
    })()
    """
    VEREDITO = r"""
    (() => {
      clearInterval(window.__timer);
      const a = [...window.__alturas].sort((x,y) => x-y);
      return `alturas de fileira durante todo o processo: [${a.join(', ')}]`
           + (a.length === 1 ? ' — nada pulou' : ' — AINDA PULA');
    })()
    """

    LE = r"""
    (() => {
      const e = [...document.querySelectorAll('.etapa')];
      return `${document.getElementById('p_pct').textContent} · `
           + `${e.filter(x => x.classList.contains('feita')).length} feitas, `
           + `${e.filter(x => x.classList.contains('rodando')).length} rodando de ${e.length} · `
           + `"${document.getElementById('p_titulo').textContent}" `
           + `decorrido ${document.querySelector('.tempos .v').textContent}`;
    })()
    """
    # se a página travasse, este clique não daria resposta nenhuma
    RESPONDE = r"""
    (() => {
      const t0 = performance.now();
      document.querySelector('[data-tela=biblioteca]').click();
      document.querySelector('[data-tela=analisar]').click();
      return `troquei de aba duas vezes em ${(performance.now()-t0).toFixed(1)} ms`;
    })()
    """

    jan.vista.page().runJavaScript(ESPIA, lambda s: print(f"    {s}"))
    jan.vista.page().runJavaScript(
        "document.querySelector('[data-tela=analisar]').click(); "
        "document.getElementById('btn_analisar').click(); 'comecei'",
        lambda s: print(f"    {s}"))
    QTimer.singleShot(6400, lambda: jan.vista.page().runJavaScript(
        VEREDITO, lambda s: print(f"[T] {s}")))

    def olha(n):
        def _():
            jan.vista.page().runJavaScript(LE, lambda s: print(f"[{n}] {s}"))
        return _

    for i, quando in enumerate([1200, 2600, 4200, 6000], start=1):
        QTimer.singleShot(quando, olha(i))
    QTimer.singleShot(3200, lambda: jan.vista.page().runJavaScript(
        RESPONDE, lambda s: print(f"[R] página viva? {s}")))
    QTimer.singleShot(2400, lambda: (
        jan.grab().save(str(PROJETO / "poc_web" / "web_analisando.png")),
        print("    retrato: web_analisando.png")))
    QTimer.singleShot(7200, lambda: jan.vista.page().runJavaScript(
        LE, lambda s: (print(f"[fim] {s}"), print("=" * 62),
                       QTimer.singleShot(400, app.quit))))


def main() -> int:
    foto = "--foto" in sys.argv
    galeria = "--telas" in sys.argv
    interacoes = "--interacoes" in sys.argv
    acervo = "--acervo" in sys.argv
    acoes = "--acoes" in sys.argv
    progresso = "--progresso" in sys.argv

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

    if interacoes:
        QTimer.singleShot(4000, lambda: testa_interacoes(jan, app))
        return app.exec()

    if acervo:
        QTimer.singleShot(4500, lambda: testa_acervo(jan, app))
        return app.exec()

    if acoes:
        QTimer.singleShot(4500, lambda: testa_acoes(jan, app))
        return app.exec()

    if progresso:
        QTimer.singleShot(4000, lambda: testa_progresso(jan, app))
        return app.exec()

    QTimer.singleShot(4000, clica)
    QTimer.singleShot(9000 if foto else 12000, relatorio)

    def resize_e_encerra() -> None:
        print("\n[R] dynamic resize (colunas e respiro em cada largura):")

        def acabou() -> None:
            jan.resize(1600, 940)
            if foto:
                QTimer.singleShot(600, lambda: (
                    jan.grab().save(str(PROJETO / "poc_web" / "retrato.png")),
                    print(f"retrato: {PROJETO / 'poc_web' / 'retrato.png'}"),
                    QTimer.singleShot(300, app.quit),
                ))

        mede_larguras([980, 1180, 1280, 1440, 1600, 1920], acabou)

    QTimer.singleShot(10000 if foto else 13000, resize_e_encerra)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
