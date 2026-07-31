from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from . import __version__
from .applog import get_logger, setup as setup_logging
from .config import Config
from .deps_check import cuda_available, ffmpeg_available, missing_optional_deps

_log = __import__('logging').getLogger('cortecenas')
from .ui.deps_dialog import FFmpegMissingDialog, MissingDepsDialog, NoGpuDialog
from .ui.main_window import MainWindow
from .updater import avisar_se_update_falhou, check_and_offer_update, fetch_release


def _load_app_icon() -> QIcon:
    """Load the multi-resolution app icon from app/assets/. Works both when
    running from source and when PyInstaller-bundled (sys._MEIPASS)."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "app" / "assets" / "icon.ico")
    candidates.append(Path(__file__).resolve().parent / "assets" / "icon.ico")
    for p in candidates:
        if p.exists():
            return QIcon(str(p))
    return QIcon()


def _load_splash_pixmap() -> QPixmap | None:
    """Splash de verdade: a LOGO ORIGINAL flutuando (transparência per-pixel
    da janela, sem cartão nem máscara serrilhada), com um balão escuro
    discreto só atrás da faixa de texto pra dar leitura em qualquer fundo."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "app" / "assets" / "icon_256.png")
    candidates.append(Path(__file__).resolve().parent / "assets" / "icon_256.png")
    logo = None
    for p in candidates:
        if p.exists():
            pm = QPixmap(str(p))
            if not pm.isNull():
                logo = pm
                break
    if logo is None:
        return None

    from PySide6.QtGui import QColor, QPainter, QPainterPath

    band_h = 46                      # balão do texto (2 linhas compactas)
    gap = 8
    W, H = logo.width(), logo.height() + gap + band_h
    canvas = QPixmap(W, H)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.drawPixmap(0, 0, logo)   # a logo, intocada
    # Balão-legenda: mesmo tom do fundo da logo (contínuo visualmente),
    # bem arredondado — legível em desktop claro ou escuro.
    pill = QPainterPath()
    pill.addRoundedRect(W * 0.14, H - band_h, W * 0.72, band_h, band_h / 2.2, band_h / 2.2)
    painter.fillPath(pill, QColor(30, 31, 34, 235))
    painter.end()
    return canvas


class _FloatingSplash(QLabel):
    """Splash com transparência REAL. O QSplashScreen do Qt quebra o
    WA_TranslucentBackground há anos (pinta o pixmap com fundo opaco por
    dentro) — e janela opaca sem moldura ganha do Windows 11 canto
    arredondado + borda cinza de 1px desenhados pelo DWM: a "borda feia".
    Um QLabel frameless translúcido é a solução consagrada nos fóruns do Qt;
    janela de verdade translúcida nem recebe a decoração do DWM."""

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._base = pixmap
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - pixmap.width() // 2,
                geo.center().y() - pixmap.height() // 2,
            )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # A LINHA CINZA de 1px em volta é decoração do próprio Windows 11
        # (DWM) em janelas sem moldura. Interruptor oficial: border color
        # NONE + canto sem arredondar, direto na API do DWM.
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                dwm = ctypes.windll.dwmapi
                # DWMWA_WINDOW_CORNER_PREFERENCE(33) = DWMWCP_DONOTROUND(1)
                pref = ctypes.c_int(1)
                dwm.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
                )
                # DWMWA_BORDER_COLOR(34) = DWMWA_COLOR_NONE(0xFFFFFFFE)
                color = ctypes.c_uint(0xFFFFFFFE)
                dwm.DwmSetWindowAttribute(
                    hwnd, 34, ctypes.byref(color), ctypes.sizeof(color)
                )
            except Exception:
                pass

    def showMessage(self, text: str) -> None:
        """Redesenha o texto na faixa inferior do pixmap (o balão-legenda)."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFont, QPainter

        pm = QPixmap(self._base)
        painter = QPainter(pm)
        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            QRectF(0, 0, pm.width(), pm.height() - 10),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            text,
        )
        painter.end()
        self.setPixmap(pm)

    def finish(self, _win: QWidget) -> None:
        self.close()


def _force_foreground(win: QWidget) -> None:
    """Traz a janela pro primeiro plano DE VERDADE quando o load termina.

    raise_/activateWindow não bastam: o Windows bloqueia troca de
    foreground vinda de processo que não está em foco — o app só piscava
    na barra de tarefas enquanto o usuário esperava noutra janela. O
    destravamento clássico é simular um toque de ALT (o foreground-lock é
    suspenso com ALT pressionado) e aí pedir SetForegroundWindow. O toque
    é sintético e imediato — não digita nada em lugar nenhum."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(win.winId())
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass  # pior caso: continua o comportamento antigo (piscar na barra)


def _quer_interface_classica() -> bool:
    """A interface antiga (QWidgets) continua alcançável por uma release.

    A nova é a padrão, mas se ela quebrar na máquina de alguém o app não
    pode virar tijolo: `CORTECENAS_UI=classico` (ou `--classico`) traz a
    velha de volta sem reinstalar nada.
    """
    import os

    return (
        "--classico" in sys.argv
        or os.environ.get("CORTECENAS_UI", "").lower() in ("classico", "qt", "classic")
    )


def main() -> int:
    setup_logging()  # no-op if run.py already did it
    from .no_console import harden_subprocess
    harden_subprocess()  # no-op if run.py already did it

    # O esquema `cena:` TEM que ser registrado antes de existir QApplication —
    # o Chromium tranca a lista de esquemas quando inicializa, e depois disso
    # todo pedido de miniatura é barrado antes de chegar no Python.
    classico = _quer_interface_classica()
    caiu_pro_classico = ""      # vazio = escolha do usuário, não acidente
    if not classico:
        try:
            from .ui.web import registra_esquema

            registra_esquema()
        except Exception as e:
            _log.exception("QtWebEngine indisponível — caindo pra interface clássica")
            classico = True
            caiu_pro_classico = f"{type(e).__name__}: {e}"[:300]

    app = QApplication(sys.argv)
    app.setApplicationName("Corte Cenas")
    app.setWindowIcon(_load_app_icon())

    # DUAS INSTÂNCIAS NO MESMO BANCO.
    #
    # Não havia trava nenhuma, e `sqlite3.connect` é chamado cru — sem
    # `timeout=` e sem WAL. Dois cliques no ícone e duas análises: a segunda
    # leva "database is locked" depois de 5 s e, até a onda B, mostrava
    # "Análise terminada." com 100%. As duas ainda escrevem clipes na mesma
    # pasta.
    #
    # Aviso em vez de proibição: o Levi roda o app instalado e o modo fonte
    # lado a lado de propósito, e travar isso quebraria o fluxo dele. Mas
    # tem que ser uma escolha, não uma surpresa.
    trava = None
    try:
        import tempfile

        from PySide6.QtCore import QLockFile

        trava = QLockFile(str(Path(tempfile.gettempdir()) / "CorteCenas.lock"))
        trava.setStaleLockTime(30_000)
        if not trava.tryLock(150):
            from PySide6.QtWidgets import QMessageBox

            cx = QMessageBox()
            cx.setIcon(QMessageBox.Icon.Warning)
            cx.setWindowTitle("O Corte Cenas já está aberto")
            cx.setText("Já tem uma instância do Corte Cenas rodando.")
            cx.setInformativeText(
                "As duas usam o MESMO banco e a MESMA pasta de saída:\n\n"
                "• Analisar nas duas ao mesmo tempo dá erro de banco travado\n"
                "• Elas podem escrever clipes por cima uma da outra\n\n"
                "Abrir só pra olhar a Biblioteca é seguro."
            )
            seguir = cx.addButton("Abrir assim mesmo", QMessageBox.ButtonRole.AcceptRole)
            cx.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
            cx.setDefaultButton(seguir)
            cx.exec()
            if cx.clickedButton() is not seguir:
                return 0
            trava = None      # segue sem a trava, por escolha do usuário
    except Exception:  # noqa: BLE001 — trava é conforto, não requisito
        trava = None
    app._trava_instancia = trava   # segura a referência pela vida do processo

    # Splash "estilo After Effects": fica na tela DURANTE todo o carregamento
    # lento (rede + torch), com o status trocando embaixo do ícone. A janela
    # principal só aparece quando está pronta de verdade — e os diálogos que
    # precisam do usuário (update, deps, GPU) vêm depois, por cima dela.
    splash: _FloatingSplash | None = None
    pixmap = _load_splash_pixmap()
    if pixmap is not None:
        splash = _FloatingSplash(pixmap)
        splash.show()

    def status(text: str) -> None:
        if splash is not None:
            splash.showMessage(f"Corte Cenas v{__version__}\n{text}")
        app.processEvents()

    status("Carregando…")
    cfg = Config.load()
    cfg.ensure_dirs()
    get_logger().info(
        "Config: output=%s | cache=%s | models=%s",
        cfg.output_dir, cfg.cache_path, cfg.models_path,
    )

    # A janela não espera mais NADA que possa ser feito em paralelo. Antes,
    # a abertura era: rede do GitHub (0.5-5s) + import do torch (~5s) + UI —
    # tudo em fila, com o splash congelado. Agora rede e GPU vão pra uma
    # thread e a janela abre assim que estiver pronta; o resultado dos dois
    # só é preciso DEPOIS, na hora dos diálogos.
    from concurrent.futures import ThreadPoolExecutor

    bg = ThreadPoolExecutor(max_workers=2)
    status("Verificando atualizações…")
    f_release = bg.submit(fetch_release)     # rede, ~0.5-5s
    f_cuda = bg.submit(cuda_available)       # importa torch: ~5s no 1º uso

    status("Verificando dependências…")
    missing = missing_optional_deps()        # agora só olha o disco (µs)
    ffmpeg_ok = ffmpeg_available()

    status("Abrindo…")
    if classico:
        win = MainWindow(cfg)
    else:
        from .ui.web import JanelaWeb

        from .ffmpeg_locate import ffmpeg_binary

        win = JanelaWeb(cfg, ffmpeg=ffmpeg_binary())
    win.show()
    if splash is not None:
        splash.finish(win)
    # O usuário pode ter focado outra janela durante o load — sem isto o
    # Windows nega o primeiro plano e o app nasce atrás de tudo.
    win.raise_()
    win.activateWindow()
    _force_foreground(win)

    # Agora sim os diálogos interativos, por cima da janela visível — e é
    # aqui que o trabalho de fundo é colhido (já terminou enquanto a UI
    # montava, na maioria das vezes).
    try:
        release = f_release.result(timeout=8)
    except Exception:
        release = None
    try:
        has_cuda = f_cuda.result(timeout=30)
    except Exception:
        has_cuda = False
    bg.shutdown(wait=False)
    # Uma atualização foi tentada e o app continua na versão velha? O helper
    # já escrevia "APPLY FAILED" num log que ninguém lia.
    avisar_se_update_falhou(parent=win)
    check_and_offer_update(parent=win, release=release)

    if missing:
        MissingDepsDialog(missing).exec()

    if not ffmpeg_ok:
        FFmpegMissingDialog().exec()

    # Warn about CPU-only mode (no NVIDIA GPU with CUDA). App still runs,
    # just ~20x slower. Once dismissed with "don't ask again", we stay quiet.
    if not has_cuda and not cfg.gpu_warning_dismissed:
        dlg = NoGpuDialog()
        dlg.exec()
        if dlg.dont_ask_again:
            cfg.gpu_warning_dismissed = True
            cfg.save()

    # CAIR PRA INTERFACE ANTIGA NÃO É SILENCIOSO.
    #
    # O `except` acima só escrevia no app.log e a janela clássica subia com o
    # título de sempre. É o pior jeito de a v0.5.0 aparecer pra quem atualiza:
    # a pessoa vê a interface velha e conclui que a atualização não pegou —
    # e pode reinstalar tudo atrás de um problema que não é esse.
    #
    # `--classico` (escolha explícita) continua sem alarme nenhum.
    if caiu_pro_classico:
        from PySide6.QtWidgets import QMessageBox

        cx = QMessageBox()
        cx.setIcon(QMessageBox.Icon.Warning)
        cx.setWindowTitle("Interface nova não carregou")
        cx.setText(
            f"A interface nova da v{__version__} não carregou nesta máquina.\n"
            "Abri a antiga pra você não ficar sem app."
        )
        cx.setInformativeText(
            f"• A v{__version__} ESTÁ instalada — a atualização pegou\n"
            "• Nenhum dos seus cortes ou configurações mudou\n"
            "• Tudo continua funcionando por aqui\n\n"
            f"Motivo: {caiu_pro_classico}\n\n"
            "Se atualizou pelo delta, reinstalar com o CorteCenas-Setup "
            "completo costuma resolver (o delta não traz o QtWebEngine).\n"
            f"Log: {cfg.cache_path / 'logs' / 'app.log'}"
        )
        cx.exec()

    # Config ilegível: mesma lógica — voltar tudo pro padrão em silêncio faz
    # o usuário achar que o app "esqueceu" os ajustes dele.
    if getattr(cfg, "erro_ao_carregar", ""):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            None, "Configurações ilegíveis",
            f"Não consegui ler o config.json ({cfg.erro_ao_carregar}).\n\n"
            f"• Guardei o arquivo em {cfg.arquivo_quebrado or '(não deu)'}\n"
            "• Esta sessão está com os valores PADRÃO\n"
            "• Seus cortes e o acervo não foram tocados\n"
            "• Salvar as Configurações agora sobrescreve o arquivo antigo",
        )

    # Saída inacessível: o caminho configurado NÃO foi apagado, mas a
    # Biblioteca vai aparecer vazia e é preciso dizer por quê.
    if getattr(cfg, "saida_indisponivel", ""):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(
            None, "Pasta de saída fora do ar",
            f"A pasta {cfg.saida_indisponivel} não está acessível agora "
            "(disco externo desconectado?).\n\n"
            f"• Nesta sessão vou usar {cfg.output_dir}\n"
            "• NADA foi movido, apagado ou regravado\n"
            "• A Biblioteca vai aparecer vazia — é só o lugar errado\n"
            "• Reconecte e reabra o app que ele volta sozinho",
        )

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
