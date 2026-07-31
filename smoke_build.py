"""Smoke test do build: o exe recém-gerado ABRE, na INTERFACE CERTA?

Roda no _build_all.bat entre o PyInstaller e o resto. Lança o exe, espera as
provas aparecerem no app.log, mata o processo e devolve exit code — build
quebrado falha AQUI em vez de na casa dos usuários.

DUAS provas, não uma. Até a v0.5.0 este teste conferia só a linha de versão,
e isso deixa passar exatamente o defeito mais provável desta release: o
`main.py` cai pra interface Qt clássica quando o QtWebEngine não carrega, e
o app sobe normalmente — mesma versão no log, mesma janela, tudo "ok". Um
build sem as DLLs do WebEngine passaria com louvor e chegaria no usuário
como "a atualização não pegou".

A prova da interface nova é o console do Chromium: a página manda
`canal_pronto` assim que o QWebChannel liga, e o `_Pagina.
javaScriptConsoleMessage` joga isso no log como "[js] canal_pronto".
Nenhuma dessas peças existe no caminho clássico.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

version = re.search(
    r'__version__\s*=\s*"([^"]+)"', (ROOT / "app" / "__init__.py").read_text()
).group(1)
exe = ROOT / "dist" / "CorteCenas" / "CorteCenas.exe"
if not exe.exists():
    print(f"[smoke_build] FALHOU: {exe} não existe")
    sys.exit(1)

log = Path.home() / "AppData" / "Local" / "CorteCenas" / "CorteCenas" / "Logs" / "app.log"
offset = log.stat().st_size if log.exists() else 0

PROVAS = {
    "versão": f"Corte Cenas v{version} | frozen=True",
    "interface web": "[js] canal_pronto",
}
# Se ISTO aparecer, o app subiu na interface antiga sem ninguém pedir.
QUEDA = "QtWebEngine indisponível"

proc = subprocess.Popen([str(exe)])
achadas: set[str] = set()
caiu = False
texto = ""
try:
    # 90s: em máquina ociosa o boot loga em ~2s, mas o build pode rodar em
    # paralelo com uma análise (ffmpeg saturando a CPU) — visto em produção.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        time.sleep(2)
        if log.exists() and log.stat().st_size > offset:
            with open(log, encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                texto = f.read()
            achadas = {k for k, v in PROVAS.items() if v in texto}
            caiu = QUEDA in texto
            if len(achadas) == len(PROVAS) or caiu:
                break
finally:
    subprocess.run(
        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
        capture_output=True,
    )

if caiu:
    print("[smoke_build] FALHOU: o app caiu pra interface CLÁSSICA — o "
          "QtWebEngine não carregou neste build. Confira o build.spec.")
    sys.exit(1)
faltando = [k for k in PROVAS if k not in achadas]
if not faltando:
    print(f"[smoke_build] OK: v{version} frozen, na interface web (canal ligado)")
    sys.exit(0)
print(f"[smoke_build] FALHOU: não achei no app.log em 90s: {', '.join(faltando)}")
for k in faltando:
    print(f"[smoke_build]   procurava: {PROVAS[k]!r}")
sys.exit(1)
