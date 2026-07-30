"""Grava app/deps_fingerprint.txt = o que está EMPACOTADO nesta build.

Roda no _build_all.bat ANTES do PyInstaller. O arquivo é embarcado no build
(datas do build.spec) e viaja no delta zip; o updater compara o fingerprint
do zip com o da instalação atual — se não bate, o delta de ~57 MB entregaria
um app quebrado (ele só carrega o exe e `_internal/app`, não as bibliotecas),
então o updater recusa e manda o usuário pro instalador completo.

**Por que o build.spec entra na conta.** Antes o fingerprint era só o hash do
`requirements.txt`, e isso mede a coisa errada: o que quebra o delta não é a
lista de dependências instaladas, é o conjunto de arquivos EMPACOTADOS.

Custou quase uma release: a v0.5.0 passou a usar QtWebEngine, 340 MB de DLL
que a v0.4.10 não tinha. O `requirements.txt` NÃO mudou (o PySide6 sempre
trouxe o WebEngine junto, só ninguém importava), então o fingerprint ficaria
igual, o updater aceitaria o delta, e quem atualizasse por ele ficaria com um
app procurando uma `Qt6WebEngineCore.dll` que não está na pasta.

O `build.spec` é quem decide o que entra no pacote. Mudou o spec, mudou o
pacote — e aí o delta não serve mais.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

h = hashlib.sha256()
for nome in ("requirements.txt", "build.spec"):
    h.update(nome.encode("ascii"))
    h.update((ROOT / nome).read_bytes())

fp = h.hexdigest()
out = ROOT / "app" / "deps_fingerprint.txt"
out.write_text(fp + "\n", encoding="ascii")
print(f"[deps_fingerprint] {fp} -> {out}")
print("[deps_fingerprint] cobre requirements.txt + build.spec — o spec decide "
      "o que é EMPACOTADO, e é isso que o delta precisa casar")
