from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QMenu,
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


PRESETS = {
    "strict": {
        "label": "Muito Fiel",
        "tooltip": "Menos falsos positivos. Pode perder cenas rápidas ou ambíguas.",
        "threshold": 0.86, "margin": 0.05, "min_shots": 8,
        "padding": 0.25, "credit": 0.50,
    },
    "auto": {
        "label": "Auto (recomendado)",
        "tooltip": "Bom equilíbrio entre captura e precisão. Começa aqui.",
        "threshold": 0.80, "margin": 0.03, "min_shots": 3,
        "padding": 0.25, "credit": 0.55,
    },
    "loose": {
        "label": "Pouco Fiel",
        "tooltip": "Captura mais cenas. Aceita mais erros pra não perder nada.",
        "threshold": 0.74, "margin": 0.02, "min_shots": 2,
        "padding": 0.30, "credit": 0.70,
    },
}

from ..config import Config
from ..pipeline_types import AIMode, PipelineResult, STAGES
from ..storage.skip_ranges import SkipRangesStore
from ..video_ingest import EpisodeInfo, format_mmss, parse_filename, parse_mmss
from .worker import PipelineWorker, RefsPreviewWorker


class AnalyzeTab(QWidget):
    pipeline_finished = Signal(object)  # PipelineResult

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.skip_store = SkipRangesStore(self.config.cache_path)
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # --- input group ---
        inputs = QGroupBox("1. Episódio")
        form = QFormLayout(inputs)

        file_row = QHBoxLayout()
        self.video_edit = QLineEdit()
        self.video_edit.setPlaceholderText("Selecione um arquivo .mp4...")
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
        se_row.addStretch(1)
        form.addRow("Temporada/Ep:", self._wrap(se_row))

        out_row = QHBoxLayout()
        self.output_edit = QLineEdit(self.config.output_dir)
        btn_out = QPushButton("Escolher pasta...")
        btn_out.clicked.connect(self._pick_output)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(btn_out)
        form.addRow("Saída:", self._wrap(out_row))

        skip_row = QHBoxLayout()
        self.skip_head_edit = QLineEdit()
        self.skip_head_edit.setPlaceholderText("1:30")
        self.skip_head_edit.setFixedWidth(80)
        self.skip_tail_edit = QLineEdit()
        self.skip_tail_edit.setPlaceholderText("1:30")
        self.skip_tail_edit.setFixedWidth(80)
        skip_row.addWidget(QLabel("Pular início até (MM:SS):"))
        skip_row.addWidget(self.skip_head_edit)
        skip_row.addSpacing(16)
        skip_row.addWidget(QLabel("Pular fim após (MM:SS antes do final):"))
        skip_row.addWidget(self.skip_tail_edit)
        skip_row.addStretch(1)
        form.addRow("OP/ED:", self._wrap(skip_row))

        self.anime_edit.editingFinished.connect(self._load_skip_for_anime)

        layout.addWidget(inputs)

        # --- matching mode (presets) ---
        mode_box = QGroupBox("Modo de reconhecimento")
        mode_v = QVBoxLayout(mode_box)

        preset_row = QHBoxLayout()
        self.preset_group = QButtonGroup(self)
        self.preset_buttons: dict[str, QRadioButton] = {}
        for key, p in PRESETS.items():
            rb = QRadioButton(p["label"])
            rb.setToolTip(p["tooltip"])
            self.preset_buttons[key] = rb
            self.preset_group.addButton(rb)
            preset_row.addWidget(rb)
        preset_row.addStretch(1)
        mode_v.addLayout(preset_row)

        self.show_adv_btn = QPushButton("Mostrar valores manuais ⌄")
        self.show_adv_btn.setCheckable(True)
        self.show_adv_btn.setFlat(True)
        self.show_adv_btn.setStyleSheet("text-align:left; color:#aaa; padding:2px;")
        mode_v.addWidget(self.show_adv_btn)

        layout.addWidget(mode_box)

        # --- advanced filters (hidden by default) ---
        adv = QGroupBox("Valores manuais")
        adv.setVisible(False)
        adv_form = QFormLayout(adv)
        self._adv_box = adv
        self.show_adv_btn.toggled.connect(self._toggle_advanced)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.60, 0.98)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(self.config.default_threshold)
        self.threshold_spin.setToolTip("Score mínimo pra casar (cosine). Mais alto = mais exigente.")
        adv_form.addRow("Confiança mínima:", self.threshold_spin)

        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.00, 0.20)
        self.margin_spin.setSingleStep(0.01)
        self.margin_spin.setDecimals(2)
        self.margin_spin.setValue(self.config.argmax_margin)
        self.margin_spin.setToolTip("O personagem vencedor precisa ganhar do 2º por esta margem. Mais alto = menos falso positivo.")
        adv_form.addRow("Margem do top-1:", self.margin_spin)

        self.min_shots_spin = QSpinBox()
        self.min_shots_spin.setRange(1, 50)
        self.min_shots_spin.setValue(self.config.min_shots_per_character)
        self.min_shots_spin.setToolTip("Personagens com menos shots que isso são considerados ruído e removidos.")
        adv_form.addRow("Mín. shots por personagem:", self.min_shots_spin)

        self.pad_spin = QDoubleSpinBox()
        self.pad_spin.setRange(0.00, 0.60)
        self.pad_spin.setSingleStep(0.05)
        self.pad_spin.setDecimals(2)
        self.pad_spin.setValue(self.config.face_crop_padding)
        self.pad_spin.setToolTip("Margem ao redor do rosto detectado. Mais alto inclui cabelo/roupa (bom pra distinguir personagens). Muito alto traz fundo demais.")
        adv_form.addRow("Padding do rosto:", self.pad_spin)

        self.credit_spin = QDoubleSpinBox()
        self.credit_spin.setRange(0.10, 1.00)
        self.credit_spin.setSingleStep(0.05)
        self.credit_spin.setDecimals(2)
        self.credit_spin.setValue(self.config.credit_edge_threshold)
        self.credit_spin.setToolTip("Score mínimo pra flagar um keyframe como 'créditos/texto'. Mais alto = menos shots pulados.")
        adv_form.addRow("Limiar de créditos:", self.credit_spin)

        self.credit_enable_cb = QCheckBox("Detectar shots de créditos/texto automaticamente")
        self.credit_enable_cb.setChecked(self.config.skip_credit_shots)
        self.credit_enable_cb.setToolTip(
            "Desligado por padrão — o detector costuma marcar cenas normais "
            "como créditos em animes com traço rico (Witch Hat, Dr. Stone). "
            "Pra remover OP/ED de verdade, use o campo 'Pular início até' / "
            "'Pular fim após' (tempo manual, 100% confiável)."
        )
        adv_form.addRow("", self.credit_enable_cb)

        self.danbooru_cb = QCheckBox("Usar Danbooru como fonte extra de refs")
        self.danbooru_cb.setChecked(self.config.use_danbooru)
        self.danbooru_cb.setToolTip(
            "Danbooru tem mais imagens, mas muita fan art com múltiplos personagens "
            "que contamina o centroide. Deixe ligado só se souber que o anime tem "
            "tag Danbooru boa e pouca fan art coletiva."
        )
        adv_form.addRow("", self.danbooru_cb)

        layout.addWidget(adv)

        # Hook preset clicks and select initial preset based on current config
        for key, rb in self.preset_buttons.items():
            rb.toggled.connect(lambda checked, k=key: self._apply_preset(k) if checked else None)
        self._select_matching_preset()

        # --- action ---
        action_row = QHBoxLayout()
        self.preview_btn = QPushButton("Testar refs (preview)")
        self.preview_btn.setToolTip(
            "Só busca+baixa as imagens de referência e abre a pasta. "
            "Não corta shots nem roda CLIP. Útil pra inspecionar o que o sistema vai usar."
        )
        self.preview_btn.setStyleSheet(
            "QPushButton{background:#3a3d43;color:#ddd;padding:10px 14px;border-radius:6px;}"
            "QPushButton:disabled{background:#2a2c30;color:#666;}"
        )
        self.preview_btn.clicked.connect(self._start_preview)

        self.run_btn = QPushButton("Analisar episódio")
        self.run_btn.setStyleSheet(
            "QPushButton{background:#4CAF50;color:white;font-weight:bold;padding:10px 16px;border-radius:6px;}"
            "QPushButton:disabled{background:#555;}"
        )
        self.run_btn.clicked.connect(self._start)

        self.run_ai_btn = QPushButton("Analisar com IA  ▾")
        self.run_ai_btn.setToolTip(
            "Clica pra escolher o modo:\n"
            "• Completo — envia o frame inteiro pro Gemini (mais caro, identifica tudo)\n"
            "• YOLO + IA (híbrido) — YOLO extrai rostos, Gemini identifica cada um "
            "(mais barato, mais preciso, pula shots sem rosto)"
        )
        self.run_ai_btn.setStyleSheet(
            "QPushButton{background:#4169E1;color:white;font-weight:bold;padding:10px 16px;border-radius:6px;}"
            "QPushButton:disabled{background:#555;}"
        )
        ai_menu = QMenu(self.run_ai_btn)
        act_full = QAction("Analisar com IA — Completo (frame inteiro)", self)
        act_full.triggered.connect(lambda: self._start(use_ai=True, ai_mode=AIMode.FULL))
        act_hybrid = QAction("Analisar com IA — YOLO + Gemini (híbrido, recomendado)", self)
        act_hybrid.triggered.connect(lambda: self._start(use_ai=True, ai_mode=AIMode.HYBRID))
        ai_menu.addAction(act_hybrid)
        ai_menu.addAction(act_full)
        self.run_ai_btn.setMenu(ai_menu)

        # Only visible while an analysis is running. Cooperative cancel: the
        # worker stops at the next shot/stage boundary, so the click can take
        # a few seconds to land (one ffmpeg cut / API call finishes first).
        self.cancel_btn = QPushButton("✕  Cancelar análise")
        self.cancel_btn.setStyleSheet(
            "QPushButton{background:#8B3A3A;color:white;font-weight:bold;padding:10px 16px;border-radius:6px;}"
            "QPushButton:disabled{background:#555;}"
        )
        self.cancel_btn.clicked.connect(self._cancel_analysis)
        self.cancel_btn.setVisible(False)

        action_row.addStretch(1)
        action_row.addWidget(self.preview_btn)
        action_row.addSpacing(12)
        action_row.addWidget(self.run_btn)
        action_row.addSpacing(8)
        action_row.addWidget(self.run_ai_btn)
        action_row.addSpacing(8)
        action_row.addWidget(self.cancel_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        # --- progress ---
        progress_box = QGroupBox("2. Progresso")
        pv = QVBoxLayout(progress_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        pv.addWidget(self.progress)

        self.status_label = QLabel("Aguardando...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.status_label.setStyleSheet("color:#bbb;")
        pv.addWidget(self.status_label)

        self.stage_list = QListWidget()
        for stage_id, label in STAGES:
            item = QListWidgetItem(f"○  {label}")
            item.setData(Qt.ItemDataRole.UserRole, stage_id)
            self.stage_list.addItem(item)
        pv.addWidget(self.stage_list, 1)

        layout.addWidget(progress_box, 1)

    @staticmethod
    def _wrap(inner):
        w = QWidget()
        if hasattr(inner, "setContentsMargins"):
            inner.setContentsMargins(0, 0, 0, 0)
        w.setLayout(inner)
        return w

    def _pick_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar episódio", "", "Vídeo (*.mp4 *.mkv *.mov *.avi)"
        )
        if not path:
            return
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

    def _apply_preset(self, key: str) -> None:
        p = PRESETS[key]
        self.threshold_spin.setValue(p["threshold"])
        self.margin_spin.setValue(p["margin"])
        self.min_shots_spin.setValue(p["min_shots"])
        self.pad_spin.setValue(p["padding"])
        self.credit_spin.setValue(p["credit"])

    def _select_matching_preset(self) -> None:
        """Pick the preset that matches the current config values, or Auto."""
        current = (
            round(self.threshold_spin.value(), 2),
            round(self.margin_spin.value(), 2),
            int(self.min_shots_spin.value()),
            round(self.pad_spin.value(), 2),
            round(self.credit_spin.value(), 2),
        )
        for key, p in PRESETS.items():
            ref = (p["threshold"], p["margin"], p["min_shots"], p["padding"], p["credit"])
            if current == ref:
                self.preset_buttons[key].setChecked(True)
                return
        # No exact match — default to Auto without overwriting values
        self.preset_buttons["auto"].blockSignals(True)
        self.preset_buttons["auto"].setChecked(True)
        self.preset_buttons["auto"].blockSignals(False)

    def _toggle_advanced(self, checked: bool) -> None:
        self._adv_box.setVisible(checked)
        self.show_adv_btn.setText("Esconder valores manuais ⌃" if checked else "Mostrar valores manuais ⌄")

    def _pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Pasta de saída", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _start(self, use_ai: bool = False, ai_mode: AIMode = AIMode.FULL) -> None:
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
        if use_ai and not (
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
        self.config.default_threshold = float(self.threshold_spin.value())
        self.config.argmax_margin = float(self.margin_spin.value())
        self.config.min_shots_per_character = int(self.min_shots_spin.value())
        self.config.face_crop_padding = float(self.pad_spin.value())
        self.config.credit_edge_threshold = float(self.credit_spin.value())
        self.config.skip_credit_shots = self.credit_enable_cb.isChecked()
        self.config.use_danbooru = self.danbooru_cb.isChecked()
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

        self.run_btn.setEnabled(False)
        self.run_ai_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setVisible(True)
        self._reset_stages()
        self._reset_status_style()
        self.progress.setValue(0)
        self.status_label.setText("Iniciando..." + (" (IA)" if use_ai else ""))

        self._thread = QThread(self)
        self._worker = PipelineWorker(
            self.config, info, use_ai_recognition=use_ai, ai_mode=ai_mode
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.stage.connect(self._on_stage)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cancel_analysis(self) -> None:
        if self._worker is None or not isinstance(self._worker, PipelineWorker):
            return
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Cancelando — terminando a etapa atual...")
        self._worker.request_cancel()

    def _on_cancelled(self) -> None:
        self.cancel_btn.setVisible(False)
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.progress.setValue(0)
        self._reset_stages()
        self.status_label.setText(
            "Análise cancelada. Os shots já cortados ficam em cache — "
            "rodar de novo continua de onde parou."
        )

    def _reset_stages(self) -> None:
        for i in range(self.stage_list.count()):
            it = self.stage_list.item(i)
            label = STAGES[i][1]
            it.setText(f"○  {label}")

    def _on_stage(self, stage_id: str, fraction: float, msg: str) -> None:
        stage_labels = dict(STAGES)
        label = stage_labels.get(stage_id, stage_id)
        # global progress: each stage = 1/N
        idx = next((i for i, s in enumerate(STAGES) if s[0] == stage_id), -1)
        if idx >= 0:
            frac = max(0.0, min(1.0, fraction)) if fraction >= 0 else 0.5
            overall = (idx + frac) / len(STAGES)
            self.progress.setValue(int(overall * 100))
            for i in range(self.stage_list.count()):
                it = self.stage_list.item(i)
                if i < idx:
                    it.setText(f"●  {STAGES[i][1]}")
                elif i == idx:
                    marker = "▸" if fraction < 1.0 else "●"
                    it.setText(f"{marker}  {label}  —  {msg}")
                else:
                    it.setText(f"○  {STAGES[i][1]}")
        self.status_label.setText(msg)

    def _on_finished(self, result: PipelineResult) -> None:
        self.progress.setValue(100)
        for i in range(self.stage_list.count()):
            self.stage_list.item(i).setText(f"●  {STAGES[i][1]}")
        self.status_label.setText(
            f"Concluído: {result.total_shots} shots · "
            f"{result.total_characters} personagens identificados."
        )
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.pipeline_finished.emit(result)

    def _on_failed(self, message: str) -> None:
        first_line = message.splitlines()[0] if message else "falhou"
        self.status_label.setText(f"Erro: {first_line}")
        self.status_label.setStyleSheet("color:#ff6b6b;font-weight:bold;")
        self.run_btn.setEnabled(True)
        self.run_ai_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
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
        self.status_label.setStyleSheet("color:#bbb;")

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
        self._worker.status.connect(lambda m: self.status_label.setText(m))
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

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Refs baixadas")
        box.setText(
            f"{info.get('title')}\n"
            f"{total} imagens em {len(per_char)} personagens."
        )
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
