"""Tira o runtime do bundler da maquete.

Carrega a versão auto-desempacotável num QWebEngineView, deixa o Chromium
resolver os `{{ }}` e o <helmet>, e depois copia o DOM já resolvido pra um
HTML estático. O resultado não tem uma linha de JavaScript do Claude Artifact
— é só marcação com estilo embutido, que é o que vai virar a interface.

    python poc_web/extrai_maquete.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

RAIZ = Path(__file__).resolve().parent.parent
FONTE = RAIZ / "design" / "identidade_v2_standalone.html"
SAIDA = RAIZ / "poc_web" / "maquete_limpa.html"

# Pega o documento inteiro já resolvido, e também só a seção das três abas —
# que é a parte que descreve o app de verdade.
JS = r"""
(() => {
  const doc = document.documentElement.outerHTML;
  const telas = document.getElementById('telas');
  const estilos = [...document.querySelectorAll('style')].map(s => s.textContent).join('\n');
  return JSON.stringify({
    titulo: document.title,
    scripts: document.querySelectorAll('script').length,
    xdc: document.querySelectorAll('x-dc').length,
    chaves: (doc.match(/\{\{/g) || []).length,
    estilos: estilos,
    telas: telas ? telas.outerHTML : '',
    doc: doc,
  });
})()
"""


def main() -> int:
    if not FONTE.exists():
        print(f"não achei {FONTE}")
        return 1

    app = QApplication(sys.argv)
    vista = QWebEngineView()
    vista.resize(1760, 1100)
    vista.show()

    t0 = time.perf_counter()
    estado = {"pronto": False}

    def carregou(ok: bool) -> None:
        print(f"load {'ok' if ok else 'FALHOU'} em {time.perf_counter() - t0:.2f}s")
        # dá um respiro pro runtime montar o DOM antes de copiar
        QTimer.singleShot(1500, colher)

    def colher() -> None:
        vista.page().runJavaScript(JS, guardar)

    def guardar(bruto) -> None:
        import json

        d = json.loads(bruto)
        print(f"  título   : {d['titulo']}")
        print(f"  <script> : {d['scripts']}")
        print(f"  <x-dc>   : {d['xdc']}")
        print(f"  '{{{{'      : {d['chaves']}  (0 = runtime já resolveu tudo)")
        print(f"  seção telas: {len(d['telas']):,} chars")

        SAIDA.write_text(
            "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>\n"
            "<title>Corte Cenas — maquete sem runtime</title>\n"
            "<style>\nhtml,body{margin:0;padding:0;background:#0b0d11}\n"
            "body{font-family:'Segoe UI Variable Text','Segoe UI',system-ui,sans-serif;"
            "-webkit-font-smoothing:antialiased}\n"
            + d["estilos"]
            + "\n</style>\n</head>\n<body>\n"
            + d["telas"]
            + "\n</body></html>\n",
            encoding="utf-8",
        )
        print(f"\nescrevi {SAIDA}  ({SAIDA.stat().st_size:,} bytes)")
        estado["pronto"] = True
        app.quit()

    vista.loadFinished.connect(carregou)
    vista.load(QUrl.fromLocalFile(str(FONTE)))

    QTimer.singleShot(30000, app.quit)  # trava de segurança
    app.exec()
    return 0 if estado["pronto"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
