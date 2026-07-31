from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QElapsedTimer, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QSizePolicy,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


def _fmt_clock(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


import re

_RE_CONTA = re.compile(r"(\d+)\s*(?:/|de)\s*(\d+)")


def _parte_contagem(msg: str) -> tuple[str, str, int | None]:
    """Separa "Cortando cena 214 de 331" em ("Cortando cena", "214 de 331").

    O texto vira título e a CONTAGEM sai dele pra ganhar cor de número —
    é o que a pessoa acompanha de longe enquanto o app trabalha.
    """
    m = _RE_CONTA.search(msg or "")
    if not m:
        return (msg or "", "", None)
    titulo = (msg[: m.start()] + " " + msg[m.end():]).strip(" .:—-·")
    return (titulo or "Processando", f"{m.group(1)} de {m.group(2)}", int(m.group(1)))


from .presets import PRESETS   # noqa: F401 (ainda importado por outras telas)

from ..config import Config
from . import theme
from ..pipeline_types import AIMode, PipelineResult, STAGES
from ..storage.skip_ranges import SkipRangesStore
from ..video_ingest import EpisodeInfo, format_mmss, parse_filename, parse_mmss
from .discovery_dialog import DiscoveryNamingDialog
from .quiet import set_quiet_icon
from .worker import DiscoveryCommitWorker, PipelineWorker, RefsPreviewWorker


class CastReviewDialog(QDialog):
    """Conferência do elenco — a pergunta de 5 segundos que mata a classe
    inteira de fantasmas: "esses personagens estão MESMO no episódio?".
    Todos vêm marcados (desmarcar é decisão, não acidente); os suspeitos
    (refs fracas, poucas cenas com confiança morna) chegam com ⚠ na cara.
    Desmarcado = removido do episódio com pastas e memória (curation)."""

    def __init__(self, cast: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conferência do elenco")
        self.setMinimumWidth(460)
        self._boxes: list[tuple[QCheckBox, dict]] = []
        lay = QVBoxLayout(self)
        header = QLabel(
            "<b>Esses personagens estão mesmo no episódio?</b><br>"
            "Desmarque quem não está — as cenas dele saem das pastas na "
            "hora e o app lembra. Os marcados com ⚠ têm evidência fraca "
            "(poucas fotos de referência ou confiança morna)."
        )
        header.setWordWrap(True)
        lay.addWidget(header)
        for c in cast:
            warn = "⚠  " if c["suspicious"] else ""
            reason = ""
            if c["weak_refs"]:
                reason = " · refs fracas"
            cb = QCheckBox(
                f"{warn}{c['name']} — {c['n_shots']} cenas · "
                f"confiança média {c['mean_conf']:.2f}{reason}"
            )
            cb.setChecked(True)
            if c["suspicious"]:
                cb.setStyleSheet(theme.label("warn"))
            lay.addWidget(cb)
            self._boxes.append((cb, c))
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Confirmar elenco")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Deixar como está")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def removed(self) -> list[dict]:
        return [c for cb, c in self._boxes if not cb.isChecked()]


class AnalyzeTab(QWidget):
    pipeline_finished = Signal(object)  # PipelineResult

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.skip_store = SkipRangesStore(self.config.cache_path)
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        # Cronômetro + estimativa de término ("tipo Claude"): decorrido pelo
        # relógio, restante extrapolado do progresso real com suavização.
        self._clock = QElapsedTimer()
        self._overall = 0.0
        self._eta_smooth: float | None = None
        self._ritmo = ""                      # "1.3 cena/s"
        self._ritmo_etapa: str | None = None
        self._ritmo_base: tuple[int, float] | None = None
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._update_clock)
        self._tray: QSystemTrayIcon | None = None
        self._build_ui()

    @staticmethod
    def _cabecalho(numero: str, titulo: str) -> QWidget:
        """Cabeçalho de seção: número, título e um traço que vai até a borda.

        É o que substitui o cartão. O traço dá o limite que a borda dava, sem
        fechar uma caixa em volta de quatro campos — e sai de graça em
        altura, que numa janela de 640 é o recurso escasso.
        """
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        num = QLabel(numero)
        num.setStyleSheet(
            f"font-family:{theme.MONO};font-size:11px;font-weight:600;"
            f"color:{theme.ACCENT};"
        )
        lay.addWidget(num)
        tit = QLabel(titulo)
        tit.setStyleSheet(f"font-size:13px;font-weight:600;color:{theme.TXT};")
        lay.addWidget(tit)
        traco = QWidget()
        traco.setFixedHeight(1)
        traco.setStyleSheet(f"background:{theme.LINE_SOFT};")
        traco.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay.addWidget(traco, 1)
        w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return w

    def _build_ui(self) -> None:
        # A aba inteira ROLA. Numa janela baixa (ou num notebook 768p) o
        # layout antes espremia os campos até o texto não caber mais dentro
        # deles — a caixa do arquivo virava um risco de 20px com meia linha
        # de texto cortada. Rolar é o comportamento honesto: cada coisa
        # mantém o tamanho em que é legível e quem não coube desce.
        conteudo = QWidget()
        layout = QVBoxLayout(conteudo)
        layout.setContentsMargins(20, 16, 20, 16)
        # 10 entre cabeçalho e conteúdo; as seções seguintes ganham +6
        # (= 16 entre seções), porque o layout aplica o mesmo espaço
        # entre todos os pares.
        layout.setSpacing(10)
        self._conteudo = conteudo

        # --- 01 Episódio ---
        # SUPERFÍCIE PLANA, não cartões: cada seção é só um cabeçalho com um
        # traço e o conteúdo recuado embaixo. Três painéis com borda, fundo e
        # canto arredondado empilhados viravam um "dashboard" — três caixas
        # disputando a atenção numa tela cujo trabalho é preencher quatro
        # campos e apertar um botão.
        inputs = QWidget()
        form = QFormLayout(inputs)
        form.setContentsMargins(20, 0, 0, 0)
        layout.addWidget(self._cabecalho("01", "Episódio"))

        file_row = QHBoxLayout()
        self.video_edit = QLineEdit()
        self.video_edit.setPlaceholderText(
            "Arraste o episódio pra cá (.mp4/.mkv) ou clique em Selecionar..."
        )
        btn_pick = QPushButton("Selecionar...")
        btn_pick.clicked.connect(self._pick_video)
        file_row.addWidget(self.video_edit, 1)
        file_row.addWidget(btn_pick)
        form.addRow("Arquivo:", self._wrap(file_row))

        self.anime_edit = QLineEdit()
        if self.config.last_anime:
            self.anime_edit.setPlaceholderText(f"último: {self.config.last_anime}")
        form.addRow("Anime:", self.anime_edit)

        se_row = QHBoxLayout()
        self.season_spin = QSpinBox()
        self.season_spin.setRange(1, 50)
        self.season_spin.setValue(self.config.last_season)
        self.episode_spin = QSpinBox()
        self.episode_spin.setRange(1, 999)
        self.episode_spin.setValue(self.config.last_episode)
        se_row.addWidget(QLabel("T:"))
        se_row.addWidget(self.season_spin)
        se_row.addSpacing(12)
        se_row.addWidget(QLabel("E:"))
        se_row.addWidget(self.episode_spin)

        # Temporada/Ep e OP/ED dividem a linha: são quatro campos curtos que
        # sozinhos deixariam duas linhas quase vazias.
        self.skip_head_edit = QLineEdit()
        self.skip_head_edit.setPlaceholderText("1:30")
        self.skip_head_edit.setFixedWidth(70)
        self.skip_tail_edit = QLineEdit()
        self.skip_tail_edit.setPlaceholderText("1:30")
        self.skip_tail_edit.setFixedWidth(70)
        rot_op = QLabel("OP/ED:")
        rot_op.setStyleSheet(theme.label("dim"))
        se_row.addSpacing(24)
        se_row.addWidget(rot_op)
        se_row.addSpacing(10)
        se_row.addWidget(QLabel("início"))
        se_row.addWidget(self.skip_head_edit)
        se_row.addSpacing(10)
        se_row.addWidget(QLabel("fim"))
        se_row.addWidget(self.skip_tail_edit)
        se_row.addStretch(1)
        form.addRow("Temporada/Ep:", self._wrap(se_row))

        out_row = QHBoxLayout()
        self.output_edit = QLineEdit(self.config.output_dir)
        btn_out = QPushButton("Escolher pasta...")
        btn_out.clicked.connect(self._pick_output)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(btn_out)
        form.addRow("Saída:", self._wrap(out_row))

        self.anime_edit.editingFinished.connect(self._load_skip_for_anime)

        # Coluna de rótulos FIXA em 98: com largura automática, cada rótulo
        # termina numa posição e os campos começam em quatro lugares
        # diferentes — a lista deixa de ler como coluna.
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        for i in range(form.rowCount()):
            item = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            if item is not None and item.widget() is not None:
                item.widget().setFixedWidth(98)

        inputs.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(inputs)

        # --- 02 Análise ---
        # O modo de reconhecimento (Muito Fiel / Auto / Pouco Fiel e os
        # valores manuais) mudou pras Configurações: é escolha que se faz
        # UMA vez e vale por dezenas de episódios; aqui ela pedia decisão a
        # cada arquivo aberto. Esta seção agora responde só a uma pergunta —
        # o que fazer com este episódio.
        layout.addSpacing(6)
        layout.addWidget(self._cabecalho("02", "Análise"))
        action_box = QWidget()
        action_v = QVBoxLayout(action_box)
        action_v.setContentsMargins(20, 0, 0, 0)
        action_v.setSpacing(10)

        # Tesoura sem cor de estado: vermelho neste app significa destrutivo,
        # e cortar as cenas não destrói nada.
        self.cut_only_cb = QCheckBox("✂  Só cortar as cenas (sem identificar personagens)")
        self.cut_only_cb.setToolTip(
            "Pica o episódio inteiro em cenas na pasta shots/ e para aí — "
            "sem internet, sem referências, sem pastas por personagem. "
            "Pra quando você só quer os cortes."
        )

        action_row = QHBoxLayout()
        self.preview_btn = QPushButton("Testar refs (preview)")
        self.preview_btn.setToolTip(
            "Só busca+baixa as imagens de referência e abre a pasta. "
            "Não corta shots nem roda CLIP. Útil pra inspecionar o que o sistema vai usar."
        )
        # Controle secundário compacto: some no fundo até você precisar.
        self.preview_btn.setStyleSheet(theme.button("ghost"))
        self.preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_btn.clicked.connect(self._start_preview)

        self.run_btn = QPushButton("Analisar episódio")
        self.run_btn.setStyleSheet(theme.button("primary"))
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self._start)

        # A análise "premium": o mesmo pipeline do botão verde + IA revisando
        # só os shots duvidosos. Clique direto — os modos "IA em tudo" foram
        # aposentados da UI (o híbrido faz o mesmo com ~10% do custo).
        self.run_ai_btn = QPushButton("Analisar + IA nos duvidosos")
        self.run_ai_btn.setToolTip(
            "Igual ao Analisar episódio, com um extra: os shots em que o "
            "reconhecimento local ficou em dúvida vão pra IA desempatar.\n"
            "Gasta pouquíssima quota (~10-20% do modo IA antigo). "
            "Precisa de API key em ⚙ Configurações."
        )
        # Cinza: é uma variação do Analisar, não um segundo destaque. Duas
        # bordas cianas na mesma linha fazem o olho escolher no par ou ímpar.
        self.run_ai_btn.setStyleSheet(theme.button("alto"))
        self.run_ai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_ai_btn.clicked.connect(lambda: self._start(ai_review=True))

        self.discovery_btn = QPushButton("🔍 Modo Descoberta")
        self.discovery_btn.setToolTip(
            "Agrupa os rostos do episódio por semelhança e você dá os nomes "
            "— os prints viram referências.\n"
            "• Anime novo/desconhecido: cria o banco do zero, sem depender "
            "de nenhum site\n"
            "• Anime conhecido: reforça as refs, com os nomes já sugeridos"
        )
        self.discovery_btn.setStyleSheet(theme.button("accent-outline"))
        self.discovery_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.discovery_btn.clicked.connect(lambda: self._start(discovery=True))

        # Only visible while an analysis is running. Cooperative cancel: the
        # worker stops at the next shot/stage boundary, so the click can take
        # a few seconds to land (one ffmpeg cut / API call finishes first).
        self.cancel_btn = QPushButton("✕  Cancelar análise")
        self.cancel_btn.setStyleSheet(theme.button("danger"))
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_analysis)
        self.cancel_btn.setVisible(False)

        # Ordem de leitura: o que é raro fica à esquerda e discreto; a ação
        # principal termina a linha, que é onde o olho para.
        # Duas linhas: os controles secundários em cima, compactos, e a
        # linha das ações embaixo. Tudo junto numa linha só, a fileira não
        # cabia na janela mínima e o app ganhava barra de rolagem horizontal
        # — que numa tela de formulário é sempre erro de layout.
        secundarios = QHBoxLayout()
        secundarios.setSpacing(10)
        secundarios.addWidget(self.cut_only_cb)
        secundarios.addWidget(self.preview_btn)
        secundarios.addStretch(1)
        action_v.addLayout(secundarios)

        action_row.setSpacing(8)
        action_row.addStretch(1)
        action_row.addWidget(self.discovery_btn)
        action_row.addWidget(self.run_ai_btn)
        action_row.addWidget(self.run_btn)
        action_row.addWidget(self.cancel_btn)
        action_v.addLayout(action_row)
        action_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(action_box)

        # --- progress ---
        # Sem animação disponível no Qt, o que prova que o app está VIVO é
        # número que anda: o que ele está fazendo, quanto já foi, o tempo
        # decorrido e o ritmo real. Barra quicando falsa não prova nada.
        layout.addSpacing(6)
        layout.addWidget(self._cabecalho("03", "Progresso"))
        progress_box = QWidget()
        pv = QVBoxLayout(progress_box)
        pv.setContentsMargins(20, 0, 0, 0)
        pv.setSpacing(10)

        linha_topo = QHBoxLayout()
        linha_topo.setSpacing(10)
        self.status_label = QLabel("Aguardando…")
        self.status_label.setStyleSheet(
            f"font-family:{theme.DISP};font-size:15px;font-weight:600;color:{theme.TXT};"
        )
        linha_topo.addWidget(self.status_label)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(theme.label("time"))
        linha_topo.addWidget(self.count_label)
        linha_topo.addStretch(1)
        self.pct_label = QLabel("")
        self.pct_label.setStyleSheet(
            f"font-family:{theme.MONO};font-size:15px;font-weight:600;color:{theme.ACCENT};"
        )
        linha_topo.addWidget(self.pct_label)
        pv.addLayout(linha_topo)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        pv.addWidget(self.progress)

        linha_medida = QHBoxLayout()
        linha_medida.setSpacing(18)
        self.clock_label = QLabel("")
        self.clock_label.setTextFormat(Qt.TextFormat.RichText)
        self.clock_label.setStyleSheet(theme.label("mono"))
        self.clock_label.setToolTip(
            "Tempo decorrido · estimativa de término (calculada pelo ritmo "
            "real das etapas — fica mais precisa conforme avança)"
        )
        linha_medida.addWidget(self.clock_label)
        linha_medida.addStretch(1)
        self.file_label = QLabel("")
        self.file_label.setStyleSheet(theme.label("faint"))
        self.file_label.setToolTip("O arquivo que está sendo escrito agora.")
        linha_medida.addWidget(self.file_label)
        pv.addLayout(linha_medida)

        self.stage_list = QListWidget()
        self.stage_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.stage_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stage_list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;}}"
            f"QListWidget::item{{border:none;padding:5px 8px;}}"
        )
        for stage_id, label in STAGES:
            item = QListWidgetItem(f"○   {label}")
            item.setData(Qt.ItemDataRole.UserRole, stage_id)
            item.setForeground(QBrush(QColor(theme.TXT_FAINT)))
            self.stage_list.addItem(item)
        # Parada, a seção mostra só texto + barra + tempo. A lista de etapas
        # aparece quando a análise começa: um painel alto e vazio esperando
        # não informa nada e ainda empurra o resto da tela pra fora.
        self.stage_list.setVisible(False)
        self.stage_list.setFixedHeight(len(STAGES) * 30 + 6)
        self.stage_list.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        pv.addWidget(self.stage_list)

        progress_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(progress_box)
        # UM stretch, no fim: é o que segura as três seções coladas no topo
        # em vez de deixá-las esticarem pra preencher a janela.
        layout.addStretch(1)

        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QScrollArea.Shape.NoFrame)
        rolagem.setWidget(conteudo)
        fora = QVBoxLayout(self)
        fora.setContentsMargins(0, 0, 0, 0)
        fora.addWidget(rolagem)

    @staticmethod
    def _wrap(inner):
        w = QWidget()
        if hasattr(inner, "setContentsMargins"):
            inner.setContentsMargins(0, 0, 0, 0)
        w.setLayout(inner)
        return w

    def _pick_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar episódio", "", "Vídeo (*.mp4 *.mkv *.mov *.avi *.webm *.ts)"
        )
        if path:
            self.set_video(path)

    def set_video(self, path: str) -> None:
        """Set the episode file and auto-fill anime/season/episode from the
        filename. Shared by the file picker and window-wide drag-and-drop."""
        self.video_edit.setText(path)
        info = parse_filename(path)
        self.anime_edit.setText(info.anime)
        self.season_spin.setValue(info.season)
        self.episode_spin.setValue(info.episode)
        self._load_skip_for_anime()

    def _load_skip_for_anime(self) -> None:
        name = self.anime_edit.text().strip()
        if not name:
            return
        head, tail = self.skip_store.get(name)
        self.skip_head_edit.setText(format_mmss(head))
        self.skip_tail_edit.setText(format_mmss(tail))

    def _pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Pasta de saída", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _start(
        self,
        use_ai: bool = False,
        ai_mode: AIMode = AIMode.FULL,
        ai_review: bool = False,
        discovery: bool = False,
    ) -> None:
        video = self.video_edit.text().strip()
        anime = self.anime_edit.text().strip()
        out = self.output_edit.text().strip()
        if not video or not Path(video).is_file():
            self.status_label.setText("⚠ Selecione um arquivo de vídeo válido.")
            return
        if not anime:
            self.status_label.setText("⚠ Informe o nome do anime.")
            return
        if not out:
            self.status_label.setText("⚠ Escolha uma pasta de saída.")
            return
        if (use_ai or ai_review) and not (
            (self.config.navyai_api_key or "").strip()
            or (self.config.gemini_api_key or "").strip()
        ):
            self.status_label.setText(
                "⚠ Modo IA precisa de uma API key (NavyAI ou Gemini). Abre em ⚙ Configurações."
            )
            return

        self.config.output_dir = out
        self.config.last_anime = anime
        self.config.last_season = int(self.season_spin.value())
        self.config.last_episode = int(self.episode_spin.value())
        # Os valores de reconhecimento (confiança, margem, padding…) vêm da
        # config, escritos em ⚙ Configurações — esta aba não os edita mais.
        self.config.save()

        head_s = parse_mmss(self.skip_head_edit.text())
        tail_s = parse_mmss(self.skip_tail_edit.text())
        self.skip_store.set(anime, head_s, tail_s)

        info = EpisodeInfo(
            anime=anime,
            season=int(self.season_spin.value()),
            episode=int(self.episode_spin.value()),
            source=Path(video),
            skip_head_seconds=head_s,
            skip_tail_seconds=tail_s,
        )

        # Reanálise de um episódio que já tem resultado salvo: o usuário
        # escolhe se o resultado novo SUBSTITUI o antigo (padrão — chutes
        # ruins da análise anterior somem) ou SOMA por cima (nada que já foi
        # identificado se perde; erros antigos ficam até serem removidos na
        # mão). A curadoria manual sobrevive nas duas opções.
        cut_only = (
            not use_ai and not discovery and self.cut_only_cb.isChecked()
        )
        merge_previous = False
        if not discovery and not cut_only:
            try:
                from ..storage.db import Database
                _db = Database(self.config.cache_path / "index.db")
                already = _db.has_analysis(
                    str(info.source), info.anime, info.season, info.episode
                )
            except Exception:
                already = False
            if already:
                box = QMessageBox(self)
                set_quiet_icon(box, QMessageBox.Icon.Question)
                box.setWindowTitle("Reanalisar episódio")
                box.setText(
                    "Este episódio já tem uma análise salva.\n"
                    "Como aplicar o resultado novo?"
                )
                box.setInformativeText(
                    "• Substituir: as pastas refletem só a análise nova — "
                    "chutes errados da antiga somem (recomendado após "
                    "reforçar refs).\n"
                    "• Adicionar: mantém tudo que já foi identificado e soma "
                    "o novo — nada se perde, mas erros antigos ficam até "
                    "você remover.\n\n"
                    "Sua curadoria manual (remover/mover/aprovar) é "
                    "respeitada nas duas opções."
                )
                btn_replace = box.addButton(
                    "Substituir (recomendado)", QMessageBox.ButtonRole.AcceptRole
                )
                btn_merge = box.addButton(
                    "Adicionar por cima", QMessageBox.ButtonRole.ActionRole
                )
                btn_cancel = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(btn_replace)
                box.exec()
                clicked = box.clickedButton()
                if clicked is btn_cancel:
                    return
                merge_previous = clicked is btn_merge

        # Em qual pasta este anime mora? Perguntado AQUI, na thread da
        # interface, antes de a análise começar — o pipeline roda em thread
        # de fundo e não pode abrir diálogo. Só aparece quando o nome
        # digitado parece com uma pasta que já existe e ainda não houve
        # decisão; depois disso nunca mais.
        if not self._escolher_pasta(info.anime):
            return

        self.run_btn.setEnabled(False)
        self.run_ai_btn.setEnabled(False)
        self.discovery_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(True)
        self._reset_stages()
        self._reset_status_style()
        self.progress.setValue(0)
        self._clock.start()
        self._overall = 0.0
        self._eta_smooth = None
        self._ritmo = ""
        self._ritmo_etapa = None
        self._ritmo_base = None
        self.clock_label.setText(self._medida("decorrido", "0:00"))
        self._tick.start()
        if discovery:
            suffix = " (Modo Descoberta)"
        elif use_ai:
            suffix = " (IA)"
        elif ai_review:
            suffix = " (CLIP + revisão IA)"
        else:
            suffix = ""
        self.status_label.setText("Iniciando..." + suffix)

        self._thread = QThread(self)
        self._worker = PipelineWorker(
            self.config, info, use_ai_recognition=use_ai, ai_mode=ai_mode,
            ai_review_ambiguous=ai_review, discovery=discovery,
            merge_previous=merge_previous, cut_only=cut_only,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.stage.connect(self._on_stage)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.refs_missing.connect(self._on_refs_missing)
        self._worker.anime_not_found.connect(self._on_anime_not_found)
        self._worker.discovery_ready.connect(self._on_discovery_ready)
        for sig in (self._worker.finished, self._worker.failed,
                    self._worker.cancelled, self._worker.refs_missing,
                    self._worker.anime_not_found, self._worker.discovery_ready):
            sig.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_anime_not_found(self, message: str) -> None:
        """Anime sem match na AniList/MAL e sem banco local — em vez de um
        beco sem saída, oferece o Modo Descoberta na hora."""
        self._stop_clock("")
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.discovery_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.status_label.setText("Anime não encontrado nas bases online.")
        self.status_label.setStyleSheet(theme.label("warn"))

        box = QMessageBox(self)
        set_quiet_icon(box, QMessageBox.Icon.Question)
        box.setWindowTitle("Anime não encontrado")
        box.setText(message.splitlines()[0] if message else "Anime não encontrado.")
        box.setInformativeText(
            "Quer rodar o Modo Descoberta? O app identifica os personagens "
            "pelo próprio episódio (agrupando os rostos parecidos) e você dá "
            "os nomes no final. Os rostos nomeados viram referências pros "
            "próximos episódios."
        )
        disc_btn = box.addButton("🔍 Rodar Modo Descoberta", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is disc_btn:
            self._start(discovery=True)

    def _on_discovery_ready(self, disc) -> None:
        """Grupos prontos — abre a tela de batismo; confirmado, o commit
        roda em thread própria e desagua no fluxo normal de conclusão."""
        secs = self._stop_clock()
        self._notify(
            "Corte Cenas — Descoberta pronta",
            f"{len(disc.groups)} personagens encontrados em {_fmt_clock(secs)}. "
            "Hora de dar os nomes!",
        )
        self.status_label.setText(
            f"{len(disc.groups)} personagens descobertos — dê os nomes na janela."
        )
        dlg = DiscoveryNamingDialog(disc, self)
        if not dlg.exec():
            self.run_btn.setEnabled(True)
            self.run_ai_btn.setEnabled(True)
            self.discovery_btn.setEnabled(True)
            self.cancel_btn.setVisible(False)
            self.status_label.setText(
                "Descoberta cancelada — nada foi salvo (os shots cortados "
                "ficam em cache)."
            )
            return

        self.status_label.setText("Salvando personagens descobertos...")
        self._start_discovery_commit(disc, dlg.names(), dlg.removed())

    def _start_discovery_commit(
        self, disc, names: dict, removed: dict
    ) -> None:
        """Commit do batismo em thread própria — usado tanto pelo Modo
        Descoberta quanto pela ponte verde→batismo (grupos sem nome)."""
        self.run_btn.setEnabled(False)
        self.run_ai_btn.setEnabled(False)
        self.discovery_btn.setEnabled(False)
        self._thread = QThread(self)
        self._worker = DiscoveryCommitWorker(self.config, disc, names, removed)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.stage.connect(self._on_stage)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cancel_analysis(self) -> None:
        if self._worker is None or not isinstance(self._worker, PipelineWorker):
            return
        self.cancel_btn.setEnabled(False)
        self.status_label.setText(
            "Cancelando — espera a operação atual terminar "
            "(um download de modelo pode levar minutos)..."
        )
        self._worker.request_cancel()

    def _on_cancelled(self) -> None:
        self._stop_clock("")
        self.cancel_btn.setVisible(False)
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.discovery_btn.setEnabled(True)
        self.progress.setValue(0)
        self._reset_stages()
        self.status_label.setText(
            "Análise cancelada. Os shots já cortados ficam em cache — "
            "rodar de novo continua de onde parou."
        )

    # Estado da etapa → (marca, cor do texto, fundo da linha). Pendente não
    # tem fundo (economiza contraste); feita fica discreta; a que RODA é a
    # única em ciano e negrito; a que falhou fica vermelha na tela até o
    # usuário agir.
    def _pinta_etapa(self, item, estado: str, texto: str) -> None:
        marcas = {"feita": "✓", "rodando": "▸", "pendente": "○", "falhou": "✕"}
        cores = {
            "feita": theme.TXT_DIM, "rodando": theme.ACCENT,
            "pendente": theme.TXT_FAINT, "falhou": theme.DANGER,
        }
        fundos = {
            "feita": theme.WELL_OFF, "rodando": theme.ACCENT_INK,
            "pendente": "transparent", "falhou": theme.DANGER_INK,
        }
        item.setText(f"{marcas[estado]}   {texto}")
        item.setForeground(QBrush(QColor(cores[estado])))
        if fundos[estado] == "transparent":
            item.setBackground(QBrush(Qt.GlobalColor.transparent))
        else:
            item.setBackground(QBrush(QColor(fundos[estado])))
        fonte = item.font()
        fonte.setBold(estado == "rodando")
        item.setFont(fonte)

    def _escolher_pasta(self, digitado: str) -> bool:
        """Pergunta em qual pasta o anime vai morar, uma vez só.

        O app criava DUAS pastas do mesmo show quando o nome era digitado
        diferente ("Mushoku Tensei" numa análise, "Mushoku" na outra). Agora,
        quando o nome parece com uma pasta que já existe, quem decide é o
        usuário — e a resposta fica gravada.

        Devolve False só se o usuário cancelar a análise.
        """
        from ..storage import pastas
        from ..storage.organizer import sanitize

        if not digitado:
            return True
        # já decidido antes? então nem pergunta
        try:
            if pastas._memoria(self.config.cache_path).get(pastas._chave(digitado)):
                return True
        except Exception:  # noqa: BLE001 — memória ilegível não trava análise
            return True

        candidatas = [
            c for c in pastas.parecidas(digitado, self.config.output_path)
            if c != sanitize(digitado)
        ]
        if not candidatas:
            return True

        box = QMessageBox(self)
        box.setWindowTitle("Onde guardar este anime")
        box.setText(
            f'Já existe a pasta <b>{candidatas[0]}</b>, que parece ser o mesmo '
            f'anime que "<b>{digitado}</b>".'
        )
        box.setInformativeText(
            "Guardar este episódio lá dentro mantém as temporadas juntas.\n\n"
            "Sua resposta fica gravada — não pergunto de novo."
        )
        usar = box.addButton(
            f'Guardar em "{candidatas[0]}"', QMessageBox.ButtonRole.AcceptRole
        )
        propria = box.addButton(
            f'Criar "{sanitize(digitado)}"', QMessageBox.ButtonRole.ActionRole
        )
        cancelar = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(usar)
        box.exec()

        clicado = box.clickedButton()
        if clicado is cancelar:
            return False
        pastas.apontar(
            digitado,
            candidatas[0] if clicado is usar else sanitize(digitado),
            self.config.cache_path,
        )
        return True

    def _reset_stages(self) -> None:
        self.stage_list.setVisible(True)
        for i in range(self.stage_list.count()):
            self._pinta_etapa(self.stage_list.item(i), "pendente", STAGES[i][1])
        self.count_label.setText("")
        self.pct_label.setText("")
        self.file_label.setText("")

    def _on_stage(self, stage_id: str, fraction: float, msg: str) -> None:
        stage_labels = dict(STAGES)
        label = stage_labels.get(stage_id, stage_id)
        # global progress: each stage = 1/N
        idx = next((i for i, s in enumerate(STAGES) if s[0] == stage_id), -1)
        if idx >= 0:
            frac = max(0.0, min(1.0, fraction)) if fraction >= 0 else 0.5
            overall = (idx + frac) / len(STAGES)
            self._overall = overall
            self.progress.setValue(int(overall * 100))
            self.pct_label.setText(f"{int(overall * 100)}%")
            for i in range(self.stage_list.count()):
                it = self.stage_list.item(i)
                if i < idx:
                    self._pinta_etapa(it, "feita", STAGES[i][1])
                elif i == idx:
                    self._pinta_etapa(
                        it, "feita" if fraction >= 1.0 else "rodando", label
                    )
                else:
                    self._pinta_etapa(it, "pendente", STAGES[i][1])
        # "Cortando cena 214 de 331" → título limpo à esquerda e a CONTAGEM
        # em âmbar do lado, que é o que a pessoa olha de longe.
        titulo, contagem, n = _parte_contagem(msg)
        self.status_label.setText(titulo)
        self.count_label.setText(contagem)
        self._mede_ritmo(stage_id, n)

    def _mede_ritmo(self, stage_id: str, n: int | None) -> None:
        """Ritmo REAL da etapa (itens por segundo), medido entre duas
        contagens da mesma etapa. Zerado a cada troca de etapa: cortar e
        reconhecer têm ritmos diferentes, e misturar os dois mente."""
        agora = self._clock.elapsed() / 1000.0 if self._clock.isValid() else 0.0
        if n is None or stage_id != self._ritmo_etapa:
            self._ritmo_etapa = stage_id
            self._ritmo_base = (n, agora) if n is not None else None
            self._ritmo = ""
            return
        if self._ritmo_base is None:
            self._ritmo_base = (n, agora)
            return
        n0, t0 = self._ritmo_base
        dn, dt = n - n0, agora - t0
        if dt >= 3.0 and dn > 0:
            self._ritmo = f"{dn / dt:.1f} cena/s"

    def _on_finished(self, result: PipelineResult) -> None:
        self.progress.setValue(100)
        self.pct_label.setText("100%")
        self.count_label.setText("")
        for i in range(self.stage_list.count()):
            self._pinta_etapa(self.stage_list.item(i), "feita", STAGES[i][1])
        secs = self._stop_clock()
        tempo = f" em {_fmt_clock(secs)}" if secs >= 1 else ""
        self.status_label.setText(
            f"Concluído{tempo}: {result.total_shots} shots · "
            f"{result.total_characters} personagens identificados."
        )
        self.clock_label.setText(self._medida("levou", _fmt_clock(secs)))
        self._notify(
            "Corte Cenas — Análise concluída",
            f"{result.anime_title} {result.season}x{result.episode:02d}: "
            f"{result.total_shots} cenas, {result.total_characters} "
            f"personagens{tempo}.",
        )
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.discovery_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)

        # Conferência do elenco: só pergunta quando há SUSPEITOS — elenco
        # limpo não gera diálogo nenhum.
        if result.cast_review and any(c["suspicious"] for c in result.cast_review):
            dlg = CastReviewDialog(result.cast_review, self)
            if dlg.exec():
                to_remove = dlg.removed()
                if to_remove:
                    from ..curation import remove_character_from_episode
                    from ..storage.db import Database
                    db = Database(self.config.cache_path / "index.db")
                    n_scenes = 0
                    for c in to_remove:
                        n_scenes += remove_character_from_episode(
                            db, result.episode_id, c["character_id"],
                            Path(result.episode_root),
                            by_character=self.config.organize_by_character_enabled,
                            by_pair=self.config.organize_by_pair_enabled,
                        )
                    gone = {c["name"] for c in to_remove}
                    result.identified_characters = [
                        n for n in result.identified_characters if n not in gone
                    ]
                    result.total_characters = len(result.identified_characters)
                    self.status_label.setText(
                        f"Elenco conferido: {len(to_remove)} personagem(ns) "
                        f"removido(s) ({n_scenes} cenas). Decisão lembrada."
                    )

        self.pipeline_finished.emit(result)
        # Skeleton-crew run (1-2 characters usable, rest skipped for lack of
        # refs): worth a heads-up + the way to fix it. 3+ = no nagging.
        if result.low_refs_warning:
            box = QMessageBox(self)
            set_quiet_icon(box, QMessageBox.Icon.Warning)
            box.setWindowTitle("Poucos personagens com referências")
            box.setText(result.low_refs_warning)
            box.setInformativeText(
                "Pra identificar os que ficaram de fora: adicione prints do "
                "episódio (um personagem só por imagem) nas subpastas deles "
                "na pasta de refs, 3-8 por personagem, e analise de novo — "
                "os shots ficam em cache."
            )
            open_btn = None
            if result.refs_dir:
                open_btn = box.addButton(
                    "📂 Abrir pasta de refs", QMessageBox.ButtonRole.AcceptRole
                )
            box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if open_btn is not None and box.clickedButton() is open_btn:
                p = Path(result.refs_dir)
                p.mkdir(parents=True, exist_ok=True)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

        # Ponte verde→batismo: sobraram grupos de rostos que se parecem
        # entre si mas não bateram com referência nenhuma — oferece dar
        # nome agora, sem precisar rodar o Modo Descoberta separado.
        if result.leftover_groups and result.leftover_groups.groups:
            lg = result.leftover_groups
            box = QMessageBox(self)
            set_quiet_icon(box, QMessageBox.Icon.Question)
            box.setWindowTitle("Grupos sem nome")
            box.setText(
                f"{len(lg.groups)} grupo(s) de cenas ficaram sem personagem — "
                "rostos que se parecem entre si, mas não bateram com nenhuma "
                "referência conhecida."
            )
            box.setInformativeText(
                "Quer batizar agora? Dar nome coloca essas cenas nas pastas "
                "e vira referência — os próximos episódios já reconhecem "
                "sozinhos. Grupo de figurante? Deixa o nome vazio."
            )
            bat_btn = box.addButton(
                "🔍 Batizar agora", QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton("Depois", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is bat_btn:
                dlg = DiscoveryNamingDialog(lg, self)
                if dlg.exec():
                    self.status_label.setText("Salvando personagens batizados...")
                    self._start_discovery_commit(lg, dlg.names(), dlg.removed())

    def _on_refs_missing(self, message: str, refs_dir: str) -> None:
        """Zero characters got usable reference photos. Instead of a dead-end
        error, offer the way out: open the refs folder so the user can drop
        face images per character and re-run."""
        self._stop_clock("")
        first_line = message.splitlines()[0] if message else "refs insuficientes"
        self._notify("Corte Cenas — Análise parou", first_line)
        self.status_label.setText(f"Erro: {first_line}")
        self.status_label.setStyleSheet(theme.label("danger"))
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.discovery_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)

        box = QMessageBox(self)
        set_quiet_icon(box, QMessageBox.Icon.Warning)
        box.setWindowTitle("Sem referências suficientes")
        box.setText(first_line)
        box.setInformativeText(
            "Você pode adicionar fotos manualmente: cada personagem tem uma "
            "subpasta na pasta de refs. Jogue imagens .jpg/.png com o rosto "
            "bem visível — prints do próprio episódio funcionam ótimo — umas "
            "3-8 por personagem, e clique em Analisar de novo (os shots já "
            "cortados ficam em cache).\n\n"
            "As fotos valem pra todos os próximos episódios desse anime."
        )
        box.setDetailedText(message)
        open_btn = box.addButton("📂 Abrir pasta de refs", QMessageBox.ButtonRole.AcceptRole)
        disc_btn = box.addButton("🔍 Modo Descoberta", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            p = Path(refs_dir)
            p.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        elif box.clickedButton() is disc_btn:
            self._start(discovery=True)

    def _on_failed(self, message: str) -> None:
        self._stop_clock("")
        first_line = message.splitlines()[0] if message else "falhou"
        self._notify("Corte Cenas — Análise falhou", first_line)
        self.status_label.setText(f"Erro: {first_line}")
        self.status_label.setStyleSheet(theme.label("danger"))
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.discovery_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)

        box = QMessageBox(self)
        set_quiet_icon(box, QMessageBox.Icon.Critical)
        box.setWindowTitle("Falha ao analisar episódio")
        box.setText(first_line)
        box.setInformativeText(
            "A pipeline foi interrompida. O traceback completo está em 'Mostrar detalhes' "
            "e também foi escrito no terminal."
        )
        box.setDetailedText(message or "(sem detalhes)")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _reset_status_style(self) -> None:
        self.status_label.setStyleSheet(theme.label("dim"))

    # --- cronômetro + notificação ---

    def _medida(self, rotulo: str, valor: str) -> str:
        return (
            f"<span style='color:{theme.TXT_FAINT}'>{rotulo}</span>&nbsp;&nbsp;"
            f"<span style='color:{theme.TIME}'>{valor}</span>"
        )

    def _update_clock(self) -> None:
        if not self._clock.isValid():
            return
        secs = self._clock.elapsed() / 1000.0
        partes = [self._medida("decorrido", _fmt_clock(secs))]
        # Estimativa só depois de progresso real (antes disso seria chute):
        # extrapola pelo ritmo global e suaviza pra não ficar pulando.
        if self._overall >= 0.06 and secs > 12:
            remaining = secs / self._overall - secs
            if self._eta_smooth is None:
                self._eta_smooth = remaining
            else:
                self._eta_smooth = 0.85 * self._eta_smooth + 0.15 * remaining
            partes.append(self._medida("resta ~", _fmt_clock(self._eta_smooth)))
        if self._ritmo:
            partes.append(self._medida("ritmo", self._ritmo))
        self.clock_label.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(partes))

    def _stop_clock(self, final_text: str | None = None) -> float:
        """Para o tique e devolve o total em segundos."""
        secs = self._clock.elapsed() / 1000.0 if self._clock.isValid() else 0.0
        self._tick.stop()
        self.clock_label.setText(
            final_text if final_text is not None
            else self._medida("levou", _fmt_clock(secs))
        )
        return secs

    def _notify(self, title: str, body: str) -> None:
        """Toast do Windows — só quando a janela NÃO está em foco (análise é
        longa, o usuário foi fazer outra coisa; se está olhando, não enche).
        Sem som próprio, regra da casa; o clique traz a janela de volta."""
        win = self.window()
        if win.isActiveWindow():
            return
        QApplication.alert(win)  # pisca na barra de tarefas
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self._tray is None:
            self._tray = QSystemTrayIcon(win.windowIcon(), self)
            self._tray.setToolTip("Corte Cenas")
            self._tray.activated.connect(lambda *_: self._focus_from_tray())
            self._tray.messageClicked.connect(self._focus_from_tray)
        self._tray.show()
        self._tray.showMessage(
            title, body, QSystemTrayIcon.MessageIcon.Information, 8000
        )
        QTimer.singleShot(15000, self._hide_tray)

    def _focus_from_tray(self) -> None:
        w = self.window()
        w.showNormal()
        w.raise_()
        w.activateWindow()
        self._hide_tray()

    def _hide_tray(self) -> None:
        if self._tray is not None:
            self._tray.hide()

    def _cleanup_thread(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # --- Refs preview ---

    def _start_preview(self) -> None:
        anime = self.anime_edit.text().strip()
        if not anime:
            self.status_label.setText("⚠ Informe o nome do anime.")
            return
        self.config.last_anime = anime
        self.config.save()

        self.run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self._reset_status_style()
        self.status_label.setText("Buscando refs...")

        self._thread = QThread(self)
        self._worker = RefsPreviewWorker(
            self.config, anime, season=int(self.season_spin.value())
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # `setText` num lambda roda na thread do WORKER (conexão com lambda
        # não tem receptor, então o Qt escolhe direta) — mexer em widget fora
        # da thread da interface é o mesmo tipo de coisa que abortava o app na
        # UI web. O `setText` do label é um slot do próprio label: enfileira.
        self._worker.status.connect(self.status_label.setText)
        self._worker.finished.connect(self._on_preview_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_preview_finished(self, info: dict) -> None:
        self.run_btn.setEnabled(True)
        self.preview_btn.setEnabled(True)
        per_char = info.get("per_char", {})
        total = sum(per_char.values())
        top = sorted(per_char.items(), key=lambda kv: kv[1], reverse=True)[:8]
        summary = ", ".join(f"{n}={c}" for n, c in top)
        self.status_label.setText(
            f"{info.get('title')}: {total} imagens em {len(per_char)} personagens ({summary}...)"
        )

        warnings = info.get("warnings") or []
        box = QMessageBox(self)
        set_quiet_icon(
            box,
            QMessageBox.Icon.Warning if warnings else QMessageBox.Icon.Information,
        )
        box.setWindowTitle("Refs baixadas" + (" (com aviso)" if warnings else ""))
        head = f"{info.get('title')}\n{total} imagens em {len(per_char)} personagens."
        if warnings:
            head += (
                "\n\n⚠️ ATENÇÃO: o MyAnimeList estava fora do ar — estas fotos "
                "vieram das reservas (AniList/Kitsu), poucas por personagem.\n"
                + "\n".join(f"• {w}" for w in warnings)
                + "\n\nTente de novo mais tarde pra completar as galerias, ou "
                "use o Modo Descoberta agora."
            )
        box.setText(head)
        box.setInformativeText(
            "Abre a pasta pra inspecionar o que foi baixado.\n\n"
            "Cada personagem tem sua subpasta. Você pode adicionar .jpg/.png "
            "manualmente dentro dessas subpastas ANTES de clicar em 'Analisar "
            "episódio' — qualquer imagem ali vira referência no próximo run."
        )
        open_btn = box.addButton("Abrir pasta", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            import os, subprocess, sys as _sys
            folder = info.get("folder")
            if folder:
                if _sys.platform.startswith("win"):
                    os.startfile(folder)  # type: ignore[attr-defined]
                elif _sys.platform == "darwin":
                    subprocess.Popen(["open", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
