"""Auto-update via GitHub Releases.

On app startup we hit the GitHub Releases API and compare `tag_name` against
`app.__version__`. If a newer release exists AND publishes a
`CorteCenas-Setup-*.exe` asset, we prompt the user, download it to a temp
folder, launch the installer detached, and quit — the installer overwrites
Program Files while the app is no longer running.

Silent no-op if:
  - offline / GitHub API unreachable / rate-limited
  - local version >= remote tag
  - the release has no installer asset yet
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from . import __version__

# CHANGE ME: your GitHub "<user>/<repo>" (must be public, or the API returns 404
# for anonymous requests). The updater silently no-ops until this is real.
GITHUB_REPO = "leviclementino1-creator/corte-cenas"

_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' or '1.2.3' -> (1, 2, 3). Bad input -> (0,), which loses every compare."""
    cleaned = tag.lstrip("vV").strip()
    parts: list[int] = []
    for chunk in cleaned.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


def _fetch_latest_release() -> dict | None:
    try:
        r = httpx.get(_RELEASES_API, timeout=5.0, follow_redirects=True)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _find_installer_url(release: dict) -> str | None:
    for asset in release.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe") and "setup" in name:
            return asset.get("browser_download_url")
    return None


class _DownloadThread(QThread):
    progress = Signal(int)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, dest: Path):
        super().__init__()
        self._url = url
        self._dest = dest

    def run(self) -> None:
        try:
            with httpx.stream("GET", self._url, timeout=None, follow_redirects=True) as r:
                total = int(r.headers.get("content-length") or 0)
                downloaded = 0
                with open(self._dest, "wb") as fh:
                    for chunk in r.iter_bytes(chunk_size=128 * 1024):
                        if self.isInterruptionRequested():
                            return
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded * 100 / total))
            self.finished_ok.emit(str(self._dest))
        except Exception as e:
            self.failed.emit(str(e))


def check_and_offer_update(parent: QWidget | None = None) -> None:
    """Called once on app startup. Returns as soon as the check settles;
    if an update is applied, quits the QApplication so the installer can run."""
    release = _fetch_latest_release()
    if not release:
        return

    remote_tag = release.get("tag_name") or ""
    if _parse_version(remote_tag) <= _parse_version(__version__):
        return

    installer_url = _find_installer_url(release)
    if not installer_url:
        return

    notes = (release.get("body") or "").strip()
    if len(notes) > 800:
        notes = notes[:800] + "…"

    box = QMessageBox(parent)
    box.setWindowTitle("Nova versão disponível")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(
        f"<b>Corte Cenas {remote_tag}</b> está disponível.<br>"
        f"Você tem <b>{__version__}</b>.<br><br>"
        "Quer atualizar agora?"
    )
    if notes:
        box.setInformativeText("Ver detalhes da versão abaixo.")
        box.setDetailedText(notes)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.Yes)
    if box.exec() != QMessageBox.Yes:
        return

    dest = Path(tempfile.gettempdir()) / f"CorteCenas-Setup-{remote_tag}.exe"

    progress = QProgressDialog("Baixando atualização...", "Cancelar", 0, 100, parent)
    progress.setWindowTitle("Atualizando Corte Cenas")
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setValue(0)

    thread = _DownloadThread(installer_url, dest)

    def _launch_and_quit(path: str) -> None:
        progress.close()
        try:
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([path], close_fds=True, creationflags=flags)
        except Exception as e:
            QMessageBox.warning(parent, "Erro ao iniciar instalador", str(e))
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _show_error(err: str) -> None:
        progress.close()
        QMessageBox.warning(
            parent, "Erro ao atualizar",
            f"Não foi possível baixar a atualização:\n\n{err}\n\n"
            "Tenta de novo em alguns minutos ou baixa manualmente do GitHub."
        )

    thread.progress.connect(progress.setValue)
    thread.finished_ok.connect(_launch_and_quit)
    thread.failed.connect(_show_error)
    progress.canceled.connect(thread.requestInterruption)

    thread.start()
    progress.exec()

    # If user canceled mid-download, give the thread a moment to bail out cleanly.
    if thread.isRunning():
        thread.requestInterruption()
        thread.wait(2000)
