from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..pipeline_types import PipelineResult
from ..references.reference_store import ReferenceStore
from ..storage.db import Database
from . import quiet
from .character_grid import ShotGrid
from .quiet import set_quiet_icon
from .worker import HarvestWorker, ReframeWorker


class ResultsTab(QWidget):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.db = Database(self.config.cache_path / "index.db")
        self.ref_store = ReferenceStore(self.config.cache_path)
        self._current_result: PipelineResult | None = None
        self._anime_id: int | None = None
        self._anime_cache_id: str | None = None
        self._worker_thread: QThread | None = None
        self._current_worker: QObject | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        self.header = QLabel("Nenhum episódio processado nesta sessão.")
        self.header.setStyleSheet("font-size:14px;font-weight:bold;")
        root.addWidget(self.header)

        self.summary = QLabel("")
        self.summary.setStyleSheet("color:#bbb;")
        root.addWidget(self.summary)

        actions = QHBoxLayout()
        self.btn_open = QPushButton("Abrir pasta do episódio")
        self.btn_open.clicked.connect(self._open_folder)
        self.btn_open.setEnabled(False)
        actions.addWidget(self.btn_open)
        actions.addStretch(1)
        root.addLayout(actions)

        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(6)

        self.char_list = QListWidget()
        self.char_list.itemSelectionChanged.connect(self._on_character_selected)
        # Botão direito no personagem: remover ele do episódio INTEIRO
        # (cenas + pastas reais), com a decisão lembrada.
        self.char_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.char_list.customContextMenuRequested.connect(self._char_menu)
        left_v.addWidget(self.char_list, 1)

        self.btn_refs = QPushButton("Abrir pasta de refs")
        self.btn_refs.setEnabled(False)
        self.btn_refs.setToolTip(
            "Abre no Explorer a pasta de imagens de referência do personagem selecionado. "
            "Você pode arrastar .jpg/.png pra dentro — próximo run inclui automaticamente."
        )
        self.btn_refs.clicked.connect(self._open_refs_folder)
        left_v.addWidget(self.btn_refs)

        self.btn_vertical = QPushButton("Exportar vertical 1080×1920")
        self.btn_vertical.setEnabled(False)
        self.btn_vertical.setToolTip(
            "Gera uma versão vertical (9:16) de cada shot do personagem, "
            "centralizada no rosto detectado. Ideal pra Reels/Shorts/TikTok. "
            "Salva em Output/.../vertical/<Nome>/."
        )
        self.btn_vertical.clicked.connect(self._start_reframe)
        left_v.addWidget(self.btn_vertical)

        self.btn_export_refs = QPushButton("Exportar refs deste anime (.zip)")
        self.btn_export_refs.setEnabled(False)
        self.btn_export_refs.setToolTip(
            "Gera um .zip com o banco de referências deste anime (uma pasta "
            "por personagem) — pronto pra compartilhar ou subir num repositório "
            "de refs. Imagens filtradas (_filtered) ficam de fora."
        )
        self.btn_export_refs.clicked.connect(self._export_refs)
        left_v.addWidget(self.btn_export_refs)

        self.btn_harvest = QPushButton("Reforçar refs com este ep")
        self.btn_harvest.setEnabled(False)
        self.btn_harvest.setToolTip(
            "Pega os shots de mais alta confiança (≥0.90) de cada personagem "
            "deste episódio e adiciona os face crops à pasta de refs. "
            "Os novos arquivos começam com 'auto_' pra diferenciar dos manuais. "
            "Re-analise o próximo episódio com refs reforçados."
        )
        self.btn_harvest.clicked.connect(self._start_harvest)
        left_v.addWidget(self.btn_harvest)

        self.reframe_progress = QProgressBar()
        self.reframe_progress.setRange(0, 100)
        self.reframe_progress.setVisible(False)
        left_v.addWidget(self.reframe_progress)

        split.addWidget(left)

        self.grid: ShotGrid | None = None
        right = QWidget()
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(0)
        self._grid_container = QWidget()
        self._grid_layout = QVBoxLayout(self._grid_container)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        placeholder = QLabel(
            "Selecione um personagem para ver seus shots.\n"
            "Passe o mouse numa cena pra vê-la em movimento; duplo clique abre no player."
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color:#888;")
        self._grid_layout.addWidget(placeholder)
        right_v.addWidget(self._grid_container, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 700])

        root.addWidget(split, 1)

    def display_result(self, result: PipelineResult) -> None:
        self._current_result = result
        self.header.setText(f"{result.anime_title} — S{result.season:02d}E{result.episode:02d}")
        pairs = sorted(result.pair_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        pairs_txt = ", ".join(f"{k} ({v})" for k, v in pairs) or "—"
        self.summary.setText(
            f"{result.total_shots} shots · {result.total_characters} personagens · "
            f"top duplas: {pairs_txt}\n"
            f"Saída: {result.episode_root}"
        )
        self.btn_open.setEnabled(True)

        # Rebuild grid for this episode_root
        self._replace_grid(ShotGrid(result.episode_root))

        # Populate character list for this anime
        self._anime_id = self._lookup_anime_id(result)
        self._reload_characters()
        # Enable the episode-level action buttons (harvest, AI review) now
        # that both `_current_result` and `_anime_cache_id` are set.
        self._refresh_char_buttons()

    def _lookup_anime_id(self, result: PipelineResult) -> int | None:
        with self.db.connect() as c:
            row = c.execute(
                """SELECT e.anime_id, a.anilist_id, a.mal_id FROM episode e
                   JOIN anime a ON a.id = e.anime_id WHERE e.id = ?""",
                (result.episode_id,),
            ).fetchone()
            if not row:
                self._anime_cache_id = None
                return None
            self._anime_cache_id = self._resolve_franchise_cache_id(
                row["anilist_id"], row["mal_id"]
            )
            # Move any orphaned auto_ files from sibling season folders
            # (created before franchise pooling) into the real root folder.
            if self._anime_cache_id:
                self._migrate_orphan_auto_refs(self._anime_cache_id)
            return row["anime_id"]

    def _resolve_franchise_cache_id(
        self, anilist_id: int | None, mal_id: int | None
    ) -> str | None:
        """Find the franchise-root cache id for this anime by scanning every
        metadata.json under cache/anime_db/. Works with both legacy
        ``al<id>`` folders and new ``<title> [al<id>]`` folders.
        """
        import json as _json
        root_dir = self.config.cache_path / "anime_db"
        if not root_dir.exists():
            return None

        if anilist_id:
            # Scan every metadata.json for one that includes this anilist_id
            # in franchise_ids (that tells us the root even if the user is
            # in a sibling season's folder).
            for p in root_dir.glob("*/metadata.json"):
                try:
                    d = _json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if anilist_id == d.get("anilist_id") or anilist_id in (d.get("franchise_ids") or []):
                    r = d.get("franchise_root_id") or d.get("anilist_id")
                    if r:
                        return f"al{r}"
            return f"al{anilist_id}"
        if mal_id:
            return f"mal{mal_id}"
        return None

    def _migrate_orphan_auto_refs(self, root_cache_id: str) -> None:
        """Move auto_*.jpg files from sibling season folders (old per-season
        cache) into the current franchise root folder.
        """
        import shutil as _shutil
        from ..references.reference_store import resolve_anime_dir
        target_root = resolve_anime_dir(self.config.cache_path, root_cache_id)
        target_chars = target_root / "characters"
        if not target_chars.exists():
            return

        root_meta = target_root / "metadata.json"
        if not root_meta.exists():
            return
        import json as _json
        try:
            data = _json.loads(root_meta.read_text(encoding="utf-8"))
        except Exception:
            return
        franchise_ids = set(data.get("franchise_ids") or [])
        root_id = data.get("franchise_root_id")
        if root_id:
            franchise_ids.add(root_id)

        migrated = 0
        for fid in franchise_ids:
            sibling = resolve_anime_dir(self.config.cache_path, f"al{fid}") / "characters"
            if sibling == target_chars or not sibling.exists():
                continue
            for char_dir in sibling.iterdir():
                if not char_dir.is_dir() or char_dir.name.startswith("_"):
                    continue
                for f in char_dir.iterdir():
                    if not f.is_file() or not f.name.startswith("auto_"):
                        continue
                    dest_char = target_chars / char_dir.name
                    dest_char.mkdir(parents=True, exist_ok=True)
                    dest = dest_char / f.name
                    if dest.exists():
                        continue
                    try:
                        _shutil.move(str(f), str(dest))
                        migrated += 1
                    except OSError:
                        pass
        if migrated:
            print(
                f"[Refs] Migrados {migrated} refs 'auto_' de seasons órfãs pra "
                f"{root_cache_id}.",
                flush=True,
            )

    def _reload_characters(self) -> None:
        self.char_list.clear()
        if self._anime_id is None:
            return
        ep_id = self._current_result.episode_id if self._current_result else None
        for c in self.db.get_characters_for_anime(self._anime_id):
            shots = self.db.shots_for_character(c["id"], episode_id=ep_id)
            if not shots:
                continue
            item = QListWidgetItem(f"{c['name']}  ({len(shots)})")
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.char_list.addItem(item)

        # Duplas (o conteúdo do by_pair, que antes só existia como pasta):
        # shots em que os DOIS aparecem, contados direto do banco.
        if ep_id is None:
            return
        by_shot = self.db.assignments_for_episode(ep_id)
        pair_counts: dict[tuple[int, int], dict] = {}
        for assigns in by_shot.values():
            if len(assigns) < 2:
                continue
            srt = sorted(assigns, key=lambda a: a["id"])
            for i in range(len(srt)):
                for j in range(i + 1, len(srt)):
                    key = (srt[i]["id"], srt[j]["id"])
                    e = pair_counts.setdefault(
                        key,
                        {"names": (srt[i]["name"], srt[j]["name"]), "count": 0},
                    )
                    e["count"] += 1
        if not pair_counts:
            return
        sep = QListWidgetItem("───────  Duplas  ───────")
        sep.setFlags(Qt.ItemFlag.NoItemFlags)
        sep.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.char_list.addItem(sep)
        ranked = sorted(pair_counts.items(), key=lambda kv: -kv[1]["count"])[:40]
        for (id_a, id_b), e in ranked:
            na, nb = e["names"]
            item = QListWidgetItem(f"{na} + {nb}  ({e['count']})")
            item.setData(
                Qt.ItemDataRole.UserRole,
                {"pair": True, "ids": (id_a, id_b), "name": f"{na} + {nb}"},
            )
            self.char_list.addItem(item)

    def _on_character_selected(self) -> None:
        items = self.char_list.selectedItems()
        if not items or self.grid is None:
            self.btn_refs.setEnabled(False)
            self.btn_vertical.setEnabled(False)
            return
        c = items[0].data(Qt.ItemDataRole.UserRole)
        if not c:
            return  # separador "Duplas"
        ep_id = self._current_result.episode_id if self._current_result else None
        if c.get("pair"):
            # Dupla: interseção dos shots dos dois personagens.
            id_a, id_b = c["ids"]
            shots_a = self.db.shots_for_character(id_a, episode_id=ep_id)
            ids_b = {s["id"] for s in self.db.shots_for_character(id_b, episode_id=ep_id)}
            shots = [s for s in shots_a if s["id"] in ids_b]
            self.grid.load_for_character(shots, c["name"])
            # Refs/vertical/curadoria são POR personagem — na dupla, só ver e
            # dar play (duplo clique).
            self.btn_refs.setEnabled(False)
            self.btn_vertical.setEnabled(False)
            return
        shots = self.db.shots_for_character(c["id"], episode_id=ep_id)
        self.grid.load_for_character(shots, c["name"])
        self.btn_refs.setEnabled(self._anime_cache_id is not None)
        self.btn_vertical.setEnabled(
            self._current_result is not None and len(shots) > 0
        )

    def _open_refs_folder(self) -> None:
        items = self.char_list.selectedItems()
        if not items or not self._anime_cache_id:
            return
        c = items[0].data(Qt.ItemDataRole.UserRole)
        if not c or c.get("pair"):
            return
        folder = self.ref_store.character_dir(self._anime_cache_id, c["name"])
        folder.mkdir(parents=True, exist_ok=True)
        self._open_path(folder)

    def _export_refs(self) -> None:
        """Zip do banco de refs do anime atual — o insumo do futuro repo
        corte-cenas-refs. Exclui _filtered (imagens rejeitadas pelo filtro)."""
        if not self._anime_cache_id:
            return
        src = self.ref_store.anime_dir(self._anime_cache_id) / "characters"
        if not src.exists():
            quiet.information(
                self, "Nada pra exportar",
                "Esse anime ainda não tem banco de referências no cache."
            )
            return

        anime_folder = src.parent.name  # "<título> [al<id>]"
        default = str(
            Path.home() / "Documents" / f"CorteCenas-refs-{anime_folder}.zip"
        )
        dest_str, _ = QFileDialog.getSaveFileName(
            self, "Exportar refs", default, "Zip (*.zip)"
        )
        if not dest_str:
            return

        import zipfile
        n_files = 0
        n_chars = 0
        with zipfile.ZipFile(dest_str, "w", zipfile.ZIP_DEFLATED) as zf:
            for char_dir in sorted(src.iterdir()):
                if not char_dir.is_dir():
                    continue
                added_any = False
                for f in sorted(char_dir.iterdir()):
                    if not f.is_file():
                        continue  # pula _filtered/ e outras subpastas
                    if f.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                        continue
                    zf.write(f, arcname=f"{anime_folder}/{char_dir.name}/{f.name}")
                    n_files += 1
                    added_any = True
                if added_any:
                    n_chars += 1

        if n_files == 0:
            quiet.information(
                self, "Nada pra exportar",
                "O banco desse anime está vazio (nenhuma imagem aproveitável)."
            )
            Path(dest_str).unlink(missing_ok=True)
            return
        quiet.information(
            self, "Refs exportadas",
            f"{n_files} imagens de {n_chars} personagens em:\n{dest_str}"
        )
        self._open_path(Path(dest_str).parent)

    def _start_reframe(self) -> None:
        items = self.char_list.selectedItems()
        if not items or self._current_result is None:
            return
        c = items[0].data(Qt.ItemDataRole.UserRole)
        if not c or c.get("pair"):
            return
        shots = self.db.shots_for_character(
            c["id"],
            episode_id=self._current_result.episode_id if self._current_result else None,
        )
        if not shots:
            return
        self.btn_vertical.setEnabled(False)
        self.btn_refs.setEnabled(False)
        self.reframe_progress.setVisible(True)
        self.reframe_progress.setValue(0)

        self._worker_thread = QThread(self)
        self._current_worker = ReframeWorker(
            self.config,
            self._current_result.episode_root,
            c["id"],
            c["name"],
            shots,
        )
        self._current_worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._current_worker.run)
        self._current_worker.progress.connect(self._on_reframe_progress)
        self._current_worker.finished.connect(self._on_reframe_finished)
        self._current_worker.failed.connect(self._on_reframe_failed)
        self._current_worker.finished.connect(self._worker_thread.quit)
        self._current_worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker_thread)
        self._worker_thread.start()

    def _on_reframe_progress(self, done: int, total: int) -> None:
        pct = int(100 * done / max(total, 1))
        self.reframe_progress.setValue(pct)
        self.reframe_progress.setFormat(f"{done}/{total} shots ({pct}%)")

    def _on_reframe_finished(self, info: dict) -> None:
        self.reframe_progress.setVisible(False)
        self._refresh_char_buttons()
        box = QMessageBox(self)
        set_quiet_icon(box, QMessageBox.Icon.Information)
        box.setWindowTitle("Exportação vertical concluída")
        box.setText(
            f"{info['name']}: {info['ok']}/{info['total']} shots gerados em 1080x1920."
        )
        box.setInformativeText("Abrir a pasta agora?")
        open_btn = box.addButton("Abrir pasta", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Fechar", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            self._open_path(Path(info["folder"]))

    def _on_reframe_failed(self, msg: str) -> None:
        self.reframe_progress.setVisible(False)
        self._refresh_char_buttons()
        box = QMessageBox(self)
        set_quiet_icon(box, QMessageBox.Icon.Critical)
        box.setWindowTitle("Falha na exportação vertical")
        box.setText(msg.splitlines()[0] if msg else "erro desconhecido")
        box.setDetailedText(msg)
        box.exec()

    def _cleanup_worker_thread(self) -> None:
        if self._current_worker is not None:
            self._current_worker.deleteLater()
            self._current_worker = None
        if self._worker_thread is not None:
            self._worker_thread.deleteLater()
            self._worker_thread = None

    def _refresh_char_buttons(self) -> None:
        items = self.char_list.selectedItems()
        has = bool(items) and self._current_result is not None
        self.btn_vertical.setEnabled(has)
        self.btn_refs.setEnabled(has and self._anime_cache_id is not None)
        self.btn_harvest.setEnabled(
            self._current_result is not None and self._anime_cache_id is not None
        )
        self.btn_export_refs.setEnabled(self._anime_cache_id is not None)

    def _start_harvest(self) -> None:
        if not self._current_result or not self._anime_cache_id:
            return
        self.btn_harvest.setEnabled(False)
        self.btn_vertical.setEnabled(False)
        self.btn_refs.setEnabled(False)
        self.reframe_progress.setVisible(True)
        self.reframe_progress.setValue(0)
        self.reframe_progress.setFormat("iniciando...")

        self._worker_thread = QThread(self)
        self._current_worker = HarvestWorker(
            self.config,
            self._current_result.episode_id,
            self._current_result.episode_root,
            self._anime_cache_id,
        )
        self._current_worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._current_worker.run)
        self._current_worker.progress.connect(self._on_harvest_progress)
        self._current_worker.finished.connect(self._on_harvest_finished)
        self._current_worker.failed.connect(self._on_reframe_failed)
        self._current_worker.finished.connect(self._worker_thread.quit)
        self._current_worker.failed.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._cleanup_worker_thread)
        self._worker_thread.start()

    def _on_harvest_progress(self, name: str, done: int, total: int) -> None:
        pct = int(100 * done / max(total, 1))
        self.reframe_progress.setValue(pct)
        self.reframe_progress.setFormat(f"{name} ({done}/{total})")

    def _on_harvest_finished(self, results: dict) -> None:
        self.reframe_progress.setVisible(False)
        self._refresh_char_buttons()
        total = sum(results.values())
        if results:
            detail = ", ".join(f"{n}=+{c}" for n, c in sorted(results.items(), key=lambda x: -x[1])[:8])
        else:
            detail = "(nenhum personagem atingiu o limiar)"
        box = QMessageBox(self)
        set_quiet_icon(box, QMessageBox.Icon.Information)
        box.setWindowTitle("Refs reforçadas")
        box.setText(f"Adicionadas {total} refs em {len(results)} personagens.")
        box.setInformativeText(detail)
        box.exec()

    def _replace_grid(self, new_grid: ShotGrid) -> None:
        # clear layout
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.grid = new_grid
        self.grid.shot_activated.connect(self._play_shot)
        self.grid.shot_action.connect(self._handle_shot_action)
        self._grid_layout.addWidget(self.grid)

    def _char_menu(self, pos) -> None:
        item = self.char_list.itemAt(pos)
        d = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not d or d.get("pair") or self._current_result is None:
            return
        menu = QMenu(self)
        act_del = menu.addAction(f'🗑  Remover "{d["name"]}" do episódio')
        chosen = menu.exec(self.char_list.mapToGlobal(pos))
        if chosen is act_del:
            self._remove_character_from_episode(d)

    def _remove_character_from_episode(self, char: dict) -> None:
        """Demite o personagem do episódio inteiro: todas as cenas saem dele
        no banco E nas pastas reais (a pasta by_character dele esvazia e
        some), cada remoção vira bloqueio lembrado — a reanálise não devolve.
        Os clipes em si continuam em shots/ e nos outros personagens."""
        ep_id = getattr(self._current_result, "episode_id", None)
        if ep_id is None:
            return
        shots = self.db.shots_for_character(char["id"], ep_id)
        if not shots:
            quiet.information(
                self, "Nada a remover",
                f"{char['name']} não tem cenas neste episódio.",
            )
            return
        resp = quiet.question(
            self, "Remover personagem do episódio",
            f"Remover \"{char['name']}\" deste episódio?\n\n"
            f"• {len(shots)} cenas saem dele — a pasta real some junto\n"
            "• O app LEMBRA: reanálises não trazem essas cenas de volta\n"
            "• Os clipes continuam em shots/ e nos outros personagens\n"
            "• As fotos de referência dele não são tocadas",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        from ..curation import remove_character_from_episode
        remove_character_from_episode(
            self.db, ep_id, char["id"], Path(self._current_result.episode_root)
        )
        self._reload_characters()
        quiet.information(
            self, "Personagem removido",
            f"\"{char['name']}\" saiu do episódio: {len(shots)} cenas "
            "removidas das pastas. Decisão lembrada pras próximas reanálises.",
        )

    def _handle_shot_action(self, action: str, shots: list) -> None:
        """Context-menu callback from ShotGrid: manual cleanup of the
        currently-viewed character folder. `shots` carries every selected
        row (Ctrl/Shift/laço) — the action applies to all of them; "move"
        asks the target character once.
        """
        items = self.char_list.selectedItems()
        if not items or self._current_result is None or not shots:
            return
        current_char = items[0].data(Qt.ItemDataRole.UserRole)
        if not current_char or current_char.get("pair"):
            quiet.information(
                self, "Curadoria é por personagem",
                "Pra remover/mover/aprovar uma cena, selecione o personagem "
                "individual na lista — a visão de dupla é só pra navegar."
            )
            return
        shot_ids = [int(s.get("id") or 0) for s in shots]
        pairs = [(sid, s) for sid, s in zip(shot_ids, shots) if sid]
        if not pairs:
            return

        # Além de mexer nas linhas vivas, grava a decisão em manual_override:
        # a reanálise recria os shots do zero, e é essa memória que faz o
        # "removido fica removido / movido fica movido" sobreviver.
        ep_id = getattr(self._current_result, "episode_id", None)

        def _remember(shot: dict, char_id: int, act: str, conf=None) -> None:
            idx = shot.get("idx")
            if ep_id is None or idx is None:
                return
            self.db.record_manual(ep_id, int(idx), char_id, act, conf)

        if action == "approve":
            for sid, shot in pairs:
                self.db.set_assignment_review(sid, current_char["id"], approved=True)
                _remember(shot, current_char["id"], "add", shot.get("confidence"))
        elif action == "remove":
            for sid, shot in pairs:
                self.db.remove_shot_character(sid, current_char["id"])
                _remember(shot, current_char["id"], "block")
        elif action == "move":
            target = self._ask_target_character(skip_character_id=current_char["id"])
            if target is None:
                return
            for sid, shot in pairs:
                self.db.move_shot_to_character(
                    sid,
                    current_char["id"],
                    target["id"],
                    confidence=shot.get("confidence"),
                )
                _remember(shot, current_char["id"], "block")
                _remember(shot, target["id"], "add", shot.get("confidence"))
        else:
            return

        # Aplica na PASTA REAL na hora: sincroniza os hardlinks do clipe com
        # a nova lista de personagens do shot. Antes só a reanálise refazia
        # as pastas — o clipe "removido" continuava no Explorer.
        if action in ("remove", "move") and ep_id is not None:
            try:
                from ..storage.organizer import refresh_shot_links
                root = Path(self._current_result.episode_root)
                by_shot = self.db.assignments_for_episode(ep_id)
                for sid, shot in pairs:
                    rel = shot.get("file")
                    if not rel:
                        continue
                    names_now = [a["name"] for a in by_shot.get(sid, [])]
                    refresh_shot_links(root, root / rel, names_now)
            except Exception as e:
                print(f"[CorteCenas] Sincronização das pastas falhou: {e}")

        # Rebuild the grid for the character that's still selected
        self._reload_characters()
        # Try to re-select the same character we were viewing
        for i in range(self.char_list.count()):
            d = self.char_list.item(i).data(Qt.ItemDataRole.UserRole)
            if d and not d.get("pair") and d.get("id") == current_char["id"]:
                self.char_list.setCurrentRow(i)
                break
        self._on_character_selected()

    def _ask_target_character(self, skip_character_id: int) -> dict | None:
        """Show a dropdown of the other characters in the anime; return the
        chosen one's row, or None if cancelled / none available.
        """
        if self._anime_id is None:
            return None
        all_chars = self.db.get_characters_for_anime(self._anime_id)
        choices = [c for c in all_chars if c["id"] != skip_character_id]
        if not choices:
            quiet.information(
                self, "Sem opções", "Não há outros personagens no banco pra mover."
            )
            return None
        names = [c["name"] for c in choices]
        picked, ok = QInputDialog.getItem(
            self, "Mover pra...", "Novo personagem:", names, 0, False
        )
        if not ok or not picked:
            return None
        for c in choices:
            if c["name"] == picked:
                return c
        return None

    def _play_shot(self, row: dict) -> None:
        if not self._current_result:
            return
        p = self._current_result.episode_root / row["file"]
        self._open_path(p)

    def _open_folder(self) -> None:
        if self._current_result:
            self._open_path(self._current_result.episode_root)

    @staticmethod
    def _open_path(path: Path) -> None:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
