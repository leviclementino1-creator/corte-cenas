# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Corte Cenas.

Build with:
    pyinstaller build.spec --noconfirm --clean

Output: dist/CorteCenas/ (onedir mode).
Zip this folder to share with users who have an NVIDIA GPU + CUDA 12.8 driver.
FFmpeg binary must be available on the target machine's PATH.
"""
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)


# --- Hidden imports ---------------------------------------------------------
# Heavy ML libs rely on runtime-discovered modules that PyInstaller can't
# find statically. We collect them explicitly.

hidden = []
hidden += collect_submodules("open_clip")
hidden += collect_submodules("ultralytics")
hidden += collect_submodules("torch")
hidden += collect_submodules("torchvision")
hidden += collect_submodules("cv2")
hidden += collect_submodules("PIL")
# CCIP (segunda opinião local) roda em ONNX na CPU; o hook contrib do
# PyInstaller cuida das DLLs, o collect garante os submódulos.
hidden += collect_submodules("onnxruntime")

# Extra imports for model registries / tokenizer files these libs pull lazily.
# QtWebEngine: o PyInstaller acha os binários sozinho, mas os módulos
# Python do PySide6 só entram se pedidos.
hidden += [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
]
hidden += [
    "ftfy",
    "regex",
    "huggingface_hub",
    "hf_xet",           # fast-path de download do HF Hub (senão cai em HTTP + warning)
    "safetensors",
    "timm",
    "yaml",
]


# --- Data files -------------------------------------------------------------
datas = []
# Bundled FFmpeg (~200 MB). Put next to the app so users don't have to
# install ffmpeg separately or add anything to PATH. Populated by
# fetch_ffmpeg.py before this spec runs (see _build_all.bat).
datas += [("bin/ffmpeg.exe", "bin")]
datas += [("bin/ffprobe.exe", "bin")]
# Elevated helper for delta updates. Shipped alongside the exe so the
# updater can hand it a source dir + target dir and let it copy files.
datas += [("apply_update.ps1", ".")]
# Deps fingerprint (sha256 do requirements.txt, gerado por
# gen_deps_fingerprint.py). O updater compara o do delta zip com o local:
# deps diferentes = delta entregaria app quebrado -> forca setup completo.
datas += [("app/deps_fingerprint.txt", "app")]
# App icon (all sizes) — needed at runtime for QApplication.setWindowIcon().
# A interface inteira é UM arquivo HTML — sem ele o app abre uma janela em
# branco, e o erro só aparece em runtime.
datas += [("app/ui/web/interface.html", "app/ui/web")]

datas += [("app/assets/icon.ico", "app/assets")]
datas += [("app/assets/icon_256.png", "app/assets")]
datas += [("app/assets/icon_128.png", "app/assets")]
datas += [("app/assets/icon_64.png", "app/assets")]
datas += [("app/assets/icon_48.png", "app/assets")]
datas += [("app/assets/icon_32.png", "app/assets")]
datas += [("app/assets/icon_16.png", "app/assets")]
datas += collect_data_files("open_clip")
datas += collect_data_files("ultralytics")
datas += collect_data_files("torchvision")
# Package metadata some libs inspect at runtime:
datas += copy_metadata("torch")
datas += copy_metadata("open_clip_torch")
datas += copy_metadata("ultralytics")
datas += copy_metadata("huggingface_hub")
datas += copy_metadata("onnxruntime")


a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib.tests",
        "notebook",
    ],
    noarchive=False,
)

# --- Poda do QtWebEngine ----------------------------------------------------
# O GitHub recusa asset acima de 2 GiB, e a v0.5.0 estourou por 13 MB: o
# instalador saiu com 2,013 GiB e o upload voltou HTTP 422. Não é caso de
# espremer compressão — é peso que nunca deveria estar aqui.
#
# Medido no bundle de 5,3 GB:
#   72 MB  qtwebengine_devtools_resources.debug.pak
#   44 MB  qtwebengine_locales/*.pak  — 53 idiomas
#    5 MB  os outros arquivos .debug (v8 snapshot, resources)
#
# Os `.debug` só são lidos com depuração remota do Chromium ligada, coisa que
# um app empacotado nunca faz. Dos idiomas ficam pt-BR e en-US: a interface é
# HTML nosso, o que o WebEngine traduz são os menus dele, e sem o arquivo do
# idioma ele cai no inglês em vez de quebrar.
IDIOMAS_QUE_FICAM = {"pt-BR.pak", "en-US.pak"}


def _cabe_no_pacote(destino: str) -> bool:
    baixo = destino.replace("\\", "/").lower()
    if ".debug." in baixo and "qtwebengine" in baixo:
        return False
    if "v8_context_snapshot.debug.bin" in baixo:
        return False
    if "qtwebengine_locales/" in baixo:
        return destino.replace("\\", "/").rsplit("/", 1)[-1] in IDIOMAS_QUE_FICAM
    return True


_antes = len(a.datas) + len(a.binaries)
a.datas = [t for t in a.datas if _cabe_no_pacote(t[0])]
a.binaries = [t for t in a.binaries if _cabe_no_pacote(t[0])]
print(f"[build.spec] poda do WebEngine: {_antes - len(a.datas) - len(a.binaries)} "
      f"arquivos fora (debug do DevTools + idiomas não usados)")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CorteCenas",
    icon="app/assets/icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX often breaks torch/cuda libs; disable
    console=False,            # no CMD popup; crash traceback is captured
                              # by run.py's crash handler into a log + dialog
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CorteCenas",
)
