"""A janela que hospeda a interface.

Fina de propósito: tudo que ela faz é hospedar a página, desligar o menu de
contexto do Chromium e tratar o arrastar-soltar. O resto mora na `Ponte`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QSystemTrayIcon, QWidget

from ...config import Config
from .ponte import Ponte, ServidorMiniatura

_log = logging.getLogger("cortecenas")
PAGINA = Path(__file__).with_name("interface.html")


class _Pagina(QWebEnginePage):
    """Manda o console do JavaScript pro log do app.

    Sem isso, erro de script e recurso bloqueado somem em silêncio — foi
    assim que 331 miniaturas "carregaram" em 183 ms sem nenhuma existir.
    """

    def javaScriptConsoleMessage(self, nivel, msg, linha, fonte):  # noqa: N802
        _log.warning("[js] %s (linha %s)", msg, linha)


class JanelaWeb(QMainWindow):
    def __init__(self, cfg: Config, ffmpeg: str = "ffmpeg") -> None:
        super().__init__()
        self.setWindowTitle("Corte Cenas")
        self.resize(1600, 940)
        self.setMinimumSize(980, 640)

        self.vista = QWebEngineView(self)
        self.vista.setPage(_Pagina(self.vista))
        # O Chromium tem menu de contexto próprio (Recarregar, Ver código…).
        # Desligado: quem desenha o menu é a página.
        self.vista.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setAcceptDrops(True)

        s = self.vista.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, True)
        # Sem isso o <video> da prévia não toca sem clique do usuário.
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        self.servidor = ServidorMiniatura(PAGINA)
        QWebEngineProfile.defaultProfile().installUrlSchemeHandler(b"cena", self.servidor)

        # O MESMO objeto de config que a janela recebeu — não um `Config.load()`
        # paralelo lá dentro. Dois donos da mesma verdade é como a Ponte
        # acabava lendo um cache diferente do que a janela abriu.
        self.ponte = Ponte(cfg.cache_path / "index.db", Path(cfg.output_path),
                           ffmpeg=ffmpeg, cfg=cfg)
        self.ponte.servidor = self.servidor
        self.canal = QWebChannel(self)
        self.canal.registerObject("ponte", self.ponte)
        self.vista.page().setWebChannel(self.canal)

        self.setCentralWidget(self.vista)
        # A página vem pelo MESMO esquema das imagens: em `file://` o
        # Chromium barra todo pedido `cena:` antes de chegar no Python.
        self.vista.load(QUrl("cena:/pagina"))

        self._vigiados: set[int] = set()
        self._bandeja: QSystemTrayIcon | None = None
        self.ponte.terminou.connect(self._avisar_que_acabou)

    # ---- aviso do Windows quando a análise termina -----------------------
    def _avisar_que_acabou(self, carga: str) -> None:
        """Análise é longa; o usuário sai da frente. O recado na própria
        página só serve pra quem está olhando pra ela.

        A regra é a mesma do app Qt (`analyze_tab._notify`): SÓ com a janela
        fora de foco. Quem está olhando não precisa de um balão dizendo o que
        já está na tela.
        """
        if self.isActiveWindow():
            return
        try:
            d = json.loads(carga)
        except (ValueError, TypeError):
            d = {"como": str(carga)}
        como = d.get("como", "")
        titulos = {
            "pronto": ("Corte Cenas — Análise pronta", "O episódio terminou de ser cortado."),
            "batizado": ("Corte Cenas — Personagens salvos", "O batismo terminou."),
            "cancelado": ("Corte Cenas — Análise cancelada", "Você parou a análise."),
            "refs_faltando": ("Corte Cenas — Faltam referências",
                              "A análise parou: nenhum personagem tem fotos suficientes."),
            "anime_nao_achado": ("Corte Cenas — Anime não encontrado",
                                 "A análise parou. Abra o app pra escolher o que fazer."),
        }
        if como == "pronto" and d.get("cenas"):
            titulos["pronto"] = (
                "Corte Cenas — Análise pronta",
                f"{d['cenas']} cena(s) cortadas"
                + (f", {len(d.get('personagens') or [])} personagem(ns) identificados."
                   if d.get("personagens") else "."))
        # Um traceback cortado no caractere 180 não é aviso, é ruído: o
        # `motivo` de uma falha vem com a exceção inteira. Vai só a primeira
        # linha, que é o que o app Qt já fazia.
        primeira = (d.get("detalhe") or "").strip().splitlines()
        titulo, corpo = titulos.get(
            como, ("Corte Cenas — Análise parou",
                   (primeira[0][:160] if primeira else "Abra o app pra ver o motivo.")))
        QApplication.alert(self)  # pisca na barra de tarefas
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self._bandeja is None:
            self._bandeja = QSystemTrayIcon(self.windowIcon(), self)
            self._bandeja.setToolTip("Corte Cenas")
            self._bandeja.activated.connect(lambda *_: self._voltar_pra_frente())
            self._bandeja.messageClicked.connect(self._voltar_pra_frente)
        self._bandeja.show()
        self._bandeja.showMessage(
            titulo, corpo, QSystemTrayIcon.MessageIcon.Information, 8000)
        QTimer.singleShot(15000, self._esconder_bandeja)

    def _voltar_pra_frente(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._esconder_bandeja()

    def _esconder_bandeja(self) -> None:
        if self._bandeja is not None:
            self._bandeja.hide()

    # ---- fechar no meio de uma análise -----------------------------------
    def closeEvent(self, ev):  # noqa: N802
        """Trinta minutos de análise não morrem num clique no X.

        `JanelaWeb` não tinha closeEvent: o QThread ia embora com o processo,
        sem pergunta e sem encerramento limpo. O app Qt sempre perguntou
        (main_window.py:215) — a interface nova perdeu isso na migração.
        """
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QMessageBox

        t = getattr(self.ponte, "_thread", None)
        if isinstance(t, QThread) and t.isRunning():
            r = QMessageBox.question(
                self, "Análise em andamento",
                "Tem uma análise rodando. Fechar mesmo assim?\n\n"
                "• O processamento é interrompido agora\n"
                "• Os clipes já cortados ficam salvos em shots/\n"
                "• Rodar de novo aproveita o que já foi feito",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            w = getattr(self.ponte, "_worker", None)
            if w is not None:
                try:
                    w.request_cancel()
                except Exception:  # noqa: BLE001
                    pass
            t.quit()
            t.wait(3000)
            if t.isRunning():
                t.terminate()
                t.wait(1000)
        ev.accept()

    # ---- arrastar o episódio pra janela ----------------------------------
    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        # O widget de render só nasce depois que a janela aparece: filtrar no
        # __init__ pegava focusProxy() == None e o arquivo solto sumia.
        QTimer.singleShot(0, self._vigiar_drop)

    def _vigiar_drop(self) -> None:
        """ARMADILHA: dentro do QWebEngineView quem recebe o arrastar é um
        widget-filho de render, não a vista. E o HTML5 até recebe o drop, mas
        o objeto File do Chromium NÃO tem caminho — e o ffmpeg precisa do
        caminho. Então quem trata é o Qt, e o caminho nunca sai do Python."""
        alvos = [self.vista, self.vista.focusProxy(), *self.vista.findChildren(QWidget)]
        for a in alvos:
            if a is None or id(a) in self._vigiados:
                continue
            self._vigiados.add(id(a))
            a.setAcceptDrops(True)
            a.installEventFilter(self)

    def eventFilter(self, obj, ev):  # noqa: N802
        tipo = ev.type()
        if tipo in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                return True
        elif tipo == QEvent.Type.Drop:
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()
                self._soltou(ev.mimeData().urls())
                return True
        return super().eventFilter(obj, ev)

    def _soltou(self, urls) -> None:
        for u in urls:
            caminho = u.toLocalFile()
            if caminho.lower().endswith((".mkv", ".mp4", ".avi", ".m4v", ".mov")):
                _log.info("Episódio solto na janela: %s", caminho)
                self.ponte.arquivoSolto.emit(caminho)
                return

    def dragEnterEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._soltou(ev.mimeData().urls())
