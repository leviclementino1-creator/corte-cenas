"""Startup dependency check + self-install for optional packages.

The core app works without `ultralytics` and `huggingface_hub` — it falls
back to the legacy lbpcascade face detector. But accuracy drops ~3x. This
module detects the missing packages and lets the user install them from
the UI with one click (using the current Python interpreter, so they land
in the exact environment the app runs under).
"""
from __future__ import annotations

import shutil
import subprocess
import sys


OPTIONAL_DEPS: dict[str, str] = {
    "ultralytics": "Detector de rosto YOLOv8 anime-face (3x melhor que lbpcascade)",
    "huggingface_hub": "Download do modelo YOLO (deepghs/anime_face_detection)",
    "onnxruntime": "CCIP — segunda opinião local nos personagens duvidosos",
}


def missing_optional_deps() -> list[str]:
    """Quais pacotes opcionais faltam — SEM importar nenhum deles.

    Importava de verdade só pra descobrir se existe: `import torch` +
    `import ultralytics` custam segundos e ~1 GB de RAM, e isso acontecia
    na abertura do app, antes da janela aparecer, toda vez. `find_spec` só
    procura o pacote no disco (microssegundos) e responde a mesma coisa."""
    from importlib.util import find_spec

    missing: list[str] = []
    for mod in OPTIONAL_DEPS:
        try:
            if find_spec(mod) is None:
                missing.append(mod)
        except (ImportError, ValueError):
            missing.append(mod)
    return missing


def ffmpeg_available() -> bool:
    """Return True iff we can invoke ffmpeg — either bundled with the
    install (preferred) or on the user's PATH (fallback for source runs)."""
    from .ffmpeg_locate import is_bundled
    if is_bundled():
        return True
    return shutil.which("ffmpeg") is not None


# Resposta memoizada: descobrir isso custa o import do torch (segundos, ~1 GB
# de RAM) e a resposta não muda durante a execução. A abertura do app pergunta
# em segundo plano; quem perguntar depois pega de graça.
_CUDA: bool | None = None
_GPU_NAME: str | None = None


def cuda_known() -> bool:
    """A resposta já está pronta? Permite à UI mostrar o selo sem travar
    esperando o torch carregar."""
    return _CUDA is not None


def cuda_available() -> bool:
    """True iff torch was built with CUDA AND a working NVIDIA GPU is
    visible. Anything else means the app will silently run on CPU."""
    global _CUDA, _GPU_NAME
    if _CUDA is not None:
        return _CUDA
    try:
        import torch
        _CUDA = bool(torch.cuda.is_available())
        if _CUDA:
            try:
                _GPU_NAME = torch.cuda.get_device_name(0)
            except Exception:
                _GPU_NAME = None
    except Exception:
        _CUDA = False
    return _CUDA


def gpu_name() -> str | None:
    """Return the first GPU's name when CUDA is up; None otherwise. Used
    by the settings dialog to confirm 'GPU: NVIDIA GeForce RTX 5080'."""
    if cuda_available():
        return _GPU_NAME
    return None


def install_with_pip(
    packages: list[str], use_user_site: bool = True
) -> tuple[bool, str]:
    """Install packages using the CURRENT Python's pip. Returns (ok, output)."""
    cmd = [sys.executable, "-m", "pip", "install"]
    if use_user_site:
        cmd.append("--user")
    cmd.extend(packages)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        ok = proc.returncode == 0
        out = (proc.stdout + "\n" + proc.stderr).strip()
        return ok, out[-4000:]
    except subprocess.TimeoutExpired:
        return False, "Timeout (10 minutos)"
    except Exception as e:
        return False, f"Falha ao rodar pip: {e}"
