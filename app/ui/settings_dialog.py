from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from . import theme
from .presets import PRESETS
from ..config import Config
from ..deps_check import cuda_available, gpu_name
from ..updater import check_and_offer_update


class SettingsDialog(QDialog):
    """Central settings panel. Today it holds the NavyAI / AI integration
    credentials; more groups can be added here (paths, cache, etc.)
    without burying them in the Analisar tab.
    """

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Configurações")
        # Fits comfortably on a 1366×768 laptop with room for the taskbar;
        # anything smaller gets scrolled via the scroll area.
        self.setMinimumSize(560, 420)
        self.resize(640, 680)
        self._build_ui()

    def _build_ui(self) -> None:
        # OUTER layout: scroll area on top (grows), fixed buttons on bottom.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # INNER container that holds all the option groups. Wrapped in a
        # QScrollArea so the dialog stays usable on small screens — the Save
        # button never gets pushed offscreen.
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # --- Modo de reconhecimento ---
        # Mora AQUI, e não na aba Analisar: é uma decisão que a pessoa toma
        # UMA vez e mantém por dezenas de episódios. Ocupando espaço na tela
        # de análise, ela pedia uma escolha a cada arquivo aberto — e a
        # escolha é sempre a mesma.
        modo_group = QGroupBox("Modo de reconhecimento")
        modo_v = QVBoxLayout(modo_group)

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
        modo_v.addLayout(preset_row)

        self.show_adv_btn = QPushButton("Mostrar valores manuais ⌄")
        self.show_adv_btn.setCheckable(True)
        self.show_adv_btn.setFlat(True)
        self.show_adv_btn.setStyleSheet(theme.button("ghost"))
        modo_v.addWidget(self.show_adv_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self._adv_box = QWidget()
        self._adv_box.setVisible(False)
        adv_form = QFormLayout(self._adv_box)
        adv_form.setContentsMargins(0, 6, 0, 0)
        self.show_adv_btn.toggled.connect(self._toggle_advanced)
        modo_v.addWidget(self._adv_box)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.60, 0.98)
        self.threshold_spin.setSingleStep(0.01)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setValue(self.config.default_threshold)
        self.threshold_spin.setToolTip(
            "Score mínimo pra casar (cosine). Mais alto = mais exigente."
        )
        adv_form.addRow("Confiança mínima:", self.threshold_spin)

        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0.00, 0.20)
        self.margin_spin.setSingleStep(0.01)
        self.margin_spin.setDecimals(2)
        self.margin_spin.setValue(self.config.argmax_margin)
        self.margin_spin.setToolTip(
            "O personagem vencedor precisa ganhar do 2º por esta margem. "
            "Mais alto = menos falso positivo."
        )
        adv_form.addRow("Margem do top-1:", self.margin_spin)

        self.min_shots_spin = QSpinBox()
        self.min_shots_spin.setRange(1, 50)
        self.min_shots_spin.setValue(self.config.min_shots_per_character)
        self.min_shots_spin.setToolTip(
            "Personagens com menos shots que isso são considerados ruído e "
            "removidos."
        )
        adv_form.addRow("Mín. shots por personagem:", self.min_shots_spin)

        self.pad_spin = QDoubleSpinBox()
        self.pad_spin.setRange(0.00, 0.60)
        self.pad_spin.setSingleStep(0.05)
        self.pad_spin.setDecimals(2)
        self.pad_spin.setValue(self.config.face_crop_padding)
        self.pad_spin.setToolTip(
            "Margem ao redor do rosto detectado. Mais alto inclui cabelo/roupa "
            "(bom pra distinguir personagens). Muito alto traz fundo demais."
        )
        adv_form.addRow("Padding do rosto:", self.pad_spin)

        self.credit_spin = QDoubleSpinBox()
        self.credit_spin.setRange(0.10, 1.00)
        self.credit_spin.setSingleStep(0.05)
        self.credit_spin.setDecimals(2)
        self.credit_spin.setValue(self.config.credit_edge_threshold)
        self.credit_spin.setToolTip(
            "Score mínimo pra flagar um keyframe como 'créditos/texto'. "
            "Mais alto = menos shots pulados."
        )
        adv_form.addRow("Limiar de créditos:", self.credit_spin)

        self.credit_enable_cb = QCheckBox(
            "Detectar shots de créditos/texto automaticamente"
        )
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
            "Danbooru tem mais imagens, mas muita fan art com múltiplos "
            "personagens que contamina o centroide. Deixe ligado só se souber "
            "que o anime tem tag Danbooru boa e pouca fan art coletiva."
        )
        adv_form.addRow("", self.danbooru_cb)

        for key, rb in self.preset_buttons.items():
            rb.toggled.connect(
                lambda checked, k=key: self._apply_preset(k) if checked else None
            )
        self._select_matching_preset()
        root.addWidget(modo_group)

        # --- Output folder ---
        out_group = QGroupBox("Pasta de saída dos clipes")
        out_form = QFormLayout(out_group)

        self.output_edit = QLineEdit(self.config.output_dir)
        self.output_edit.setToolTip(
            "Onde ficam os shots cortados e as pastas by_character/by_pair "
            "de cada episódio analisado."
        )
        browse_btn = QPushButton("Escolher...")
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(self._pick_output_dir)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.output_edit, 1)
        row.addWidget(browse_btn)
        wrap = QWidget()
        wrap.setLayout(row)
        out_form.addRow("Saída:", wrap)

        info_out = QLabel(
            "Aqui dentro vão ser criadas subpastas por anime/episódio. "
            "Ex: <code>&lt;saída&gt;/Dr. Stone/S04E25/shots/</code>."
        )
        info_out.setWordWrap(True)
        info_out.setStyleSheet(theme.label("faint"))
        out_form.addRow("", info_out)

        self.chk_by_char = QCheckBox("Criar pastas por personagem (by_character)")
        self.chk_by_char.setChecked(self.config.organize_by_character_enabled)
        out_form.addRow("", self.chk_by_char)
        self.chk_by_pair = QCheckBox("Criar pastas de duplas (by_pair)")
        self.chk_by_pair.setChecked(self.config.organize_by_pair_enabled)
        self.chk_by_pair.setToolTip(
            "Em elenco grande vira dezenas de pastinhas — desligar aqui não "
            "afeta a seção Duplas da aba Resultados (ela lê do banco)."
        )
        out_form.addRow("", self.chk_by_pair)
        self.chk_ccip = QCheckBox("Segunda opinião local (CCIP) nos duvidosos")
        self.chk_ccip.setChecked(self.config.ccip_enabled)
        self.chk_ccip.setToolTip(
            "Modelo especializado em personagem de anime (deepghs), rodando "
            "na CPU: confere decisões apertadas, resolve cenas duvidosas sem "
            "gastar IA e lê retratos que o detector de rosto não lê.\n"
            "Baixa ~190 MB uma única vez no primeiro uso."
        )
        out_form.addRow("", self.chk_ccip)
        self.chk_fast_detect = QCheckBox("Detecção de cenas rápida (experimental)")
        self.chk_fast_detect.setChecked(self.config.fast_scene_detect)
        self.chk_fast_detect.setToolTip(
            "Corta o tempo de detecção em ~4x (98s → 22s num episódio de 24 min).\n"
            "Mesmo detector, só muda quem entrega os quadros pra ele.\n\n"
            "Desligada por padrão porque, medindo, 13 dos 332 cortes caem em\n"
            "frames um pouco diferentes — a quantidade de cenas bate, mas\n"
            "algumas fronteiras andam. Não é pior, é diferente.\n"
            "Só afeta análises NOVAS: episódio já analisado guarda os cortes."
        )
        out_form.addRow("", self.chk_fast_detect)

        root.addWidget(out_group)

        # --- Pastas, cache e limpeza ---
        cache_group = QGroupBox("Referências e cache")
        cache_layout = QVBoxLayout(cache_group)

        open_row = QHBoxLayout()
        refs_btn = QPushButton("📂  Abrir pasta de referências")
        refs_btn.setToolTip(
            "Um anime por pasta, um personagem por subpasta em characters/. "
            "Pode apagar/adicionar fotos à vontade — a próxima análise usa "
            "o que estiver lá."
        )
        refs_btn.clicked.connect(self._open_refs)
        open_row.addWidget(refs_btn)
        cache_btn = QPushButton("📂  Abrir pasta de cache")
        cache_btn.clicked.connect(self._open_cache)
        open_row.addWidget(cache_btn)
        open_row.addStretch(1)
        cache_layout.addLayout(open_row)

        # Duas fileiras de 2 botões: quatro numa linha forçavam largura
        # mínima maior que a janela e o diálogo abria decepado na horizontal
        # (a barra de rolagem horizontal é desligada de propósito).
        clean_row = QHBoxLayout()
        merge_btn = QPushButton("🧩  Fundir duplicados")
        merge_btn.setToolTip(
            "Acha pastas que são o MESMO personagem escrito diferente "
            "(\"Tempest, Rimuru\" ≡ \"Rimuru Tempest\") ou o mesmo anime com "
            "id diferente, mostra o plano e funde tudo com um clique."
        )
        merge_btn.clicked.connect(self._merge_dupes)
        clean_row.addWidget(merge_btn)
        clean_btn = QPushButton("🧹  Limpar fotos baixadas")
        clean_btn.setToolTip(
            "Apaga SÓ as imagens que vieram das galerias online (as que têm "
            "nome de código). Fotos de batismo (auto_disc_*) e as que você "
            "colocou na mão ficam intactas."
        )
        clean_btn.clicked.connect(self._clean_refs)
        clean_row.addWidget(clean_btn)
        clean_row.addStretch(1)
        cache_layout.addLayout(clean_row)

        danger_row = QHBoxLayout()
        reset_btn = QPushButton("♻️  Restaurar padrões de análise")
        reset_btn.setToolTip(
            "Volta os parâmetros de identificação (rigor, margem, mínimos) "
            "pros valores padrão do app."
        )
        reset_btn.clicked.connect(self._reset_analysis_defaults)
        danger_row.addWidget(reset_btn)
        wipe_btn = QPushButton("🗑  Apagar TODO o cache")
        wipe_btn.setStyleSheet(theme.button("danger"))
        wipe_btn.clicked.connect(self._wipe_cache)
        danger_row.addWidget(wipe_btn)
        danger_row.addStretch(1)
        cache_layout.addLayout(danger_row)

        cache_info = QLabel(
            "Foto de personagem errado no meio das referências suja a "
            "identificação. <b>Limpar fotos baixadas</b> zera só o que veio "
            "da internet (a próxima análise baixa de novo); pra cirurgia "
            "fina, abra a pasta e apague o que não presta. O apagão total "
            "remove <b>tudo</b> — inclusive batismos e a memória de "
            "curadoria — use só como último recurso."
        )
        cache_info.setWordWrap(True)
        cache_info.setStyleSheet(theme.label("faint"))
        cache_layout.addWidget(cache_info)

        root.addWidget(cache_group)

        # --- Primary AI: NavyAI ---
        ai_group = QGroupBox("AI principal (NavyAI / OpenAI-compatible)")
        ai_form = QFormLayout(ai_group)

        self.key_edit = QLineEdit(self.config.navyai_api_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-navy-...")
        show_key = QPushButton("Mostrar")
        show_key.setCheckable(True)
        show_key.setFixedWidth(80)
        show_key.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.key_edit, 1)
        key_row.addWidget(show_key)
        key_wrap = QWidget()
        key_wrap.setLayout(key_row)
        ai_form.addRow("API key:", key_wrap)

        self.model_edit = QLineEdit(self.config.navyai_model or "gemini-2.5-flash")
        ai_form.addRow("Modelo:", self.model_edit)

        self.base_edit = QLineEdit(self.config.navyai_base_url or "https://api.navy/v1")
        ai_form.addRow("Endpoint:", self.base_edit)

        info = QLabel(
            "Usado por padrão pelos botões <b>Analisar com IA</b>. "
            "Se falhar (rate-limit, quota, 5xx), cai automaticamente no Gemini abaixo."
        )
        info.setWordWrap(True)
        info.setStyleSheet(theme.label("faint"))
        ai_form.addRow("", info)

        root.addWidget(ai_group)

        # --- Fallback AI: Gemini direto ---
        gem_group = QGroupBox("AI fallback (Gemini direto, plano free)")
        gem_form = QFormLayout(gem_group)

        self.gem_key_edit = QLineEdit(self.config.gemini_api_key)
        self.gem_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.gem_key_edit.setPlaceholderText("AIza...")
        show_gem_key = QPushButton("Mostrar")
        show_gem_key.setCheckable(True)
        show_gem_key.setFixedWidth(80)
        show_gem_key.toggled.connect(
            lambda on: self.gem_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        gem_row = QHBoxLayout()
        gem_row.setContentsMargins(0, 0, 0, 0)
        gem_row.addWidget(self.gem_key_edit, 1)
        gem_row.addWidget(show_gem_key)
        gem_wrap = QWidget()
        gem_wrap.setLayout(gem_row)
        gem_form.addRow("API key:", gem_wrap)

        self.gem_model_edit = QLineEdit(self.config.gemini_model or "gemini-2.5-flash")
        gem_form.addRow("Modelo:", self.gem_model_edit)

        gem_info = QLabel(
            "Pega a key gratuita em <a href='https://aistudio.google.com/apikey' style='color:#4cc9c0'>"
            "aistudio.google.com/apikey</a>. "
            "Se as duas keys estiverem preenchidas, NavyAI é usada primeiro e o Gemini "
            "só entra em ação se ela falhar. Se só uma tiver, ela é usada sozinha. "
            "As keys ficam salvas em <code>~/AppData/Local/CorteCenas/config.json</code>."
        )
        gem_info.setOpenExternalLinks(True)
        gem_info.setWordWrap(True)
        gem_info.setStyleSheet(theme.label("faint"))
        gem_form.addRow("", gem_info)

        root.addWidget(gem_group)

        # --- App / Atualizações ---
        app_group = QGroupBox("Sobre / Atualizações")
        app_layout = QVBoxLayout(app_group)

        version_row = QHBoxLayout()
        version_label = QLabel(
            f"Corte Cenas <b>v{__version__}</b> — "
            "<a href='https://github.com/leviclementino1-creator/corte-cenas/releases' "
            "style='color:#4cc9c0'>ver histórico de versões</a>"
        )
        version_label.setOpenExternalLinks(True)
        version_row.addWidget(version_label)
        version_row.addStretch(1)
        app_layout.addLayout(version_row)

        # GPU / device status
        if cuda_available():
            gpu_html = f"GPU: <span style='color:#4cc9c0'>{gpu_name() or 'CUDA'}</span>"
        else:
            gpu_html = "GPU: <span style='color:#e8a15c'>não detectada — rodando em CPU (~20x mais lento)</span>"
        gpu_label = QLabel(gpu_html)
        gpu_label.setStyleSheet(theme.label("faint"))
        app_layout.addWidget(gpu_label)

        update_row = QHBoxLayout()
        self.update_btn = QPushButton("🔄  Verificar atualizações agora")
        self.update_btn.clicked.connect(self._check_updates)
        update_row.addWidget(self.update_btn)
        logs_btn = QPushButton("📂  Abrir pasta de logs")
        logs_btn.clicked.connect(self._open_logs)
        update_row.addWidget(logs_btn)
        update_row.addStretch(1)
        app_layout.addLayout(update_row)

        upd_info = QLabel(
            "O app já verifica atualizações ao abrir. Deu algum problema numa análise? "
            "Abra a pasta de logs e mande o arquivo <code>app.log</code> pra quem "
            "te passou o app — ele registra tudo que aconteceu na última execução."
        )
        upd_info.setWordWrap(True)
        upd_info.setStyleSheet(theme.label("faint"))
        app_layout.addWidget(upd_info)

        root.addWidget(app_group)
        root.addStretch(1)  # push groups up; empty space below scrolls last

        # Scroll wrapper around the inner content.
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        # A subtle separator line so the fixed button bar reads as its own strip.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color:{theme.LINE};")
        outer.addWidget(sep)

        # Fixed button bar at the bottom of the dialog — never scrolls away.
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Salvar")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 8, 12, 12)
        btn_row.addWidget(btns)
        outer.addLayout(btn_row)

    def _apply_preset(self, key: str) -> None:
        p = PRESETS[key]
        self.threshold_spin.setValue(p["threshold"])
        self.margin_spin.setValue(p["margin"])
        self.min_shots_spin.setValue(p["min_shots"])
        self.pad_spin.setValue(p["padding"])
        self.credit_spin.setValue(p["credit"])

    def _select_matching_preset(self) -> None:
        """Marca o modo que bate com os valores atuais, ou Auto."""
        atual = (
            round(self.threshold_spin.value(), 2),
            round(self.margin_spin.value(), 2),
            int(self.min_shots_spin.value()),
            round(self.pad_spin.value(), 2),
            round(self.credit_spin.value(), 2),
        )
        for key, p in PRESETS.items():
            ref = (p["threshold"], p["margin"], p["min_shots"], p["padding"], p["credit"])
            if atual == ref:
                self.preset_buttons[key].setChecked(True)
                return
        # Sem correspondência exata (o usuário mexeu na mão): fica em Auto,
        # SEM reescrever os valores dele.
        self.preset_buttons["auto"].blockSignals(True)
        self.preset_buttons["auto"].setChecked(True)
        self.preset_buttons["auto"].blockSignals(False)

    def _toggle_advanced(self, checked: bool) -> None:
        self._adv_box.setVisible(checked)
        self.show_adv_btn.setText(
            "Esconder valores manuais ⌃" if checked else "Mostrar valores manuais ⌄"
        )

    def _pick_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _open_logs(self) -> None:
        from ..applog import log_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def _open_refs(self) -> None:
        from ..cache_tools import refs_root
        p = refs_root(self.config.cache_path)
        p.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _open_cache(self) -> None:
        p = self.config.cache_path
        p.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _clean_refs(self) -> None:
        from ..cache_tools import clean_catalog_refs, refs_summary
        catalog, disc, manual = refs_summary(self.config.cache_path)
        if catalog == 0:
            QMessageBox.information(
                self, "Nada a limpar",
                "Nenhuma foto baixada de catálogo no cache — só "
                f"{disc} de batismo e {manual} manuais, que não são tocadas.",
            )
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Limpar fotos baixadas")
        box.setText(
            f"Apagar <b>{catalog}</b> fotos baixadas das galerias online?"
        )
        box.setInformativeText(
            f"Ficam intactas: {disc} fotos de batismo (auto_disc_*) e "
            f"{manual} adicionadas manualmente.\n\n"
            "A próxima análise baixa as galerias de novo (e você pode "
            "limpar o que vier errado pela pasta de referências)."
        )
        yes = box.addButton("🧹 Limpar", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        removed, animes = clean_catalog_refs(self.config.cache_path)
        QMessageBox.information(
            self, "Limpeza concluída",
            f"{removed} fotos de catálogo apagadas em {animes} anime(s). "
            "Batismos e fotos manuais preservados.",
        )

    def _merge_dupes(self) -> None:
        from ..cache_tools import merge_duplicates
        plan = merge_duplicates(self.config.cache_path, apply=False)
        if not plan["anime"] and not plan["chars"]:
            QMessageBox.information(
                self, "Sem duplicatas",
                "Nenhuma pasta duplicada de personagem ou anime encontrada. 👌",
            )
            return
        lines: list[str] = []
        for srcs, canon in plan["anime"]:
            lines.append(f"🎬 {' + '.join(srcs)}  →  {canon}")
        for anime, srcs, canon in plan["chars"]:
            lines.append(f"👤 [{anime.split(' [')[0]}] {' + '.join(srcs)}  →  {canon}")
        preview = "\n".join(lines[:20])
        if len(lines) > 20:
            preview += f"\n… e mais {len(lines) - 20} fusões."
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Fundir duplicados")
        box.setText(
            f"Encontrei <b>{len(plan['chars'])}</b> personagem(ns) e "
            f"<b>{len(plan['anime'])}</b> anime(s) duplicados. Fundir assim?"
        )
        box.setInformativeText(
            preview + "\n\nAs fotos são movidas pra pasta de nome mais "
            "completo (nada é apagado, só duplicata exata de download). "
            "Reanalise os episódios depois pra refazer as contagens."
        )
        yes = box.addButton("🧩 Fundir", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        result = merge_duplicates(self.config.cache_path, apply=True)
        QMessageBox.information(
            self, "Fusão concluída",
            f"{len(result['chars'])} personagem(ns) e {len(result['anime'])} "
            f"anime(s) fundidos, {result['moved']} arquivos reorganizados.\n\n"
            "A partir de agora o app reusa a pasta existente mesmo quando a "
            "fonte escreve o nome diferente — isso não volta a acontecer.",
        )

    def _reset_analysis_defaults(self) -> None:
        defaults = Config()
        fields = (
            "default_threshold", "argmax_margin", "min_shots_per_character",
            "face_crop_padding", "credit_edge_threshold",
        )
        current = {f: getattr(self.config, f) for f in fields}
        changed = {
            f: (current[f], getattr(defaults, f))
            for f in fields if current[f] != getattr(defaults, f)
        }
        if not changed:
            QMessageBox.information(
                self, "Já está no padrão",
                "Os parâmetros de análise já estão nos valores padrão.",
            )
            return
        detail = "\n".join(
            f"• {f}: {old} → {new}" for f, (old, new) in changed.items()
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Restaurar padrões de análise")
        box.setText("Voltar estes parâmetros pro padrão do app?")
        box.setInformativeText(detail)
        yes = box.addButton("♻️ Restaurar", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        for f in fields:
            setattr(self.config, f, getattr(defaults, f))
        self.config.save()
        # Os campos estão NESTA tela agora: sem isto eles continuariam
        # mostrando os valores antigos e o Salvar os gravaria de volta.
        self.threshold_spin.setValue(self.config.default_threshold)
        self.margin_spin.setValue(self.config.argmax_margin)
        self.min_shots_spin.setValue(self.config.min_shots_per_character)
        self.pad_spin.setValue(self.config.face_crop_padding)
        self.credit_spin.setValue(self.config.credit_edge_threshold)
        self._select_matching_preset()
        QMessageBox.information(
            self, "Padrões restaurados",
            "Parâmetros de análise de volta ao padrão — valem já na "
            "próxima análise.",
        )

    def _wipe_cache(self) -> None:
        from ..cache_tools import wipe_cache
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Apagar TODO o cache")
        box.setText("Apagar o cache INTEIRO? Isso remove:")
        box.setInformativeText(
            "• TODAS as referências — inclusive fotos de batismo e as "
            "adicionadas manualmente\n"
            "• A memória de curadoria (remover/mover/aprovar lembrados)\n"
            "• Resultados de análises no banco e elencos cacheados\n\n"
            "Os clipes na pasta de saída e os modelos baixados ficam. "
            "Não use durante uma análise em andamento.\n\n"
            # "Isso não tem volta." era falso e a própria mensagem de sucesso,
            # 23 linhas abaixo neste método, já dizia o contrário: o
            # `wipe_cache` faz `shutil.move` pra cache_lixeira. Aviso que
            # exagera é tão ruim quanto aviso que falta — ensina a não
            # acreditar nos outros.
            "Tudo isso vai pra cache_lixeira, ao lado da pasta de cache — "
            "dá pra recuperar de lá enquanto você não apagar essa pasta "
            "na mão."
        )
        yes = box.addButton(
            "🗑 Apagar tudo", QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        leftovers = wipe_cache(self.config.cache_path)
        self.config.ensure_dirs()
        if leftovers:
            QMessageBox.warning(
                self, "Cache apagado (com sobras)",
                "Quase tudo foi apagado, mas estes itens estavam EM USO e "
                "ficaram pra trás:\n\n• " + "\n• ".join(leftovers[:10]) +
                ("\n…" if len(leftovers) > 10 else "") +
                "\n\nFeche análises em andamento (ou o app) e aperte o "
                "botão de novo — ou apague pela pasta de cache.",
            )
        else:
            QMessageBox.information(
                self, "Cache apagado",
                "Cache zerado. Por segurança, tudo foi MOVIDO pra pasta "
                "cache_lixeira (ao lado do cache) — se você se arrepender, "
                "dá pra recuperar de lá; se quiser o espaço, apague-a. "
                "Modelos e seus clipes não foram tocados.",
            )

    def _check_updates(self) -> None:
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Verificando...")
        try:
            check_and_offer_update(parent=self, verbose=True)
        finally:
            self.update_btn.setEnabled(True)
            self.update_btn.setText("🔄  Verificar atualizações agora")

    def _save(self) -> None:
        out_path = self.output_edit.text().strip()
        if out_path:
            self.config.output_dir = out_path
        self.config.default_threshold = float(self.threshold_spin.value())
        self.config.argmax_margin = float(self.margin_spin.value())
        self.config.min_shots_per_character = int(self.min_shots_spin.value())
        self.config.face_crop_padding = float(self.pad_spin.value())
        self.config.credit_edge_threshold = float(self.credit_spin.value())
        self.config.skip_credit_shots = self.credit_enable_cb.isChecked()
        self.config.use_danbooru = self.danbooru_cb.isChecked()
        self.config.organize_by_character_enabled = self.chk_by_char.isChecked()
        self.config.organize_by_pair_enabled = self.chk_by_pair.isChecked()
        self.config.ccip_enabled = self.chk_ccip.isChecked()
        self.config.fast_scene_detect = self.chk_fast_detect.isChecked()
        self.config.navyai_api_key = self.key_edit.text().strip()
        self.config.navyai_model = self.model_edit.text().strip() or "gemini-2.5-flash"
        self.config.navyai_base_url = self.base_edit.text().strip() or "https://api.navy/v1"
        self.config.gemini_api_key = self.gem_key_edit.text().strip()
        self.config.gemini_model = self.gem_model_edit.text().strip() or "gemini-2.5-flash"
        self.config.save()
        self.accept()
