# -*- mode: python ; coding: utf-8 -*-
"""Build MÍNIMO só pra responder uma pergunta: o PyInstaller consegue
empacotar o QtWebEngine no Windows?

Não é o app. Não leva torch, YOLO nem ONNX — leva o PoC e mais nada, pra
sair em um ou dois minutos em vez dos quinze do build completo. Se este
exe abrir, servir as miniaturas e tocar a prévia, o caminho web está
liberado; se não abrir, a migração morre aqui e a gente economizou o
trabalho de converter três telas.

    pyinstaller poc_web/build_poc.spec --noconfirm --clean
    dist/PocWeb/PocWeb.exe
"""
from pathlib import Path

RAIZ = Path(SPECPATH).parent  # noqa: F821 — SPECPATH vem do PyInstaller

a = Analysis(
    [str(RAIZ / "poc_web" / "poc.py")],
    pathex=[str(RAIZ)],
    binaries=[],
    # A página e a ponte precisam viajar junto — o servidor lê o HTML do disco.
    datas=[(str(RAIZ / "poc_web" / "app_poc.html"), "poc_web")],
    hiddenimports=[
        "poc_web.ponte",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "psutil",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Fora tudo que o PoC não usa: o teste é sobre o QtWebEngine, não sobre
    # o tamanho do app.
    excludes=[
        "torch", "torchvision", "ultralytics", "open_clip", "onnxruntime",
        "cv2", "matplotlib", "scipy", "pandas", "tkinter",
        "PySide6.QtQuick3D", "PySide6.Qt3DCore", "PySide6.QtCharts",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PocWeb",
    debug=False,
    strip=False,
    upx=False,
    console=True,          # console ligado: quero VER o erro se houver
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PocWeb",
)
