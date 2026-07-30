"""Tira da maquete o TEXTO VISÍVEL de cada uma das três telas.

Serve de gabarito pra auditoria: o que a maquete escreve é o que o PoC tem
que mostrar — nem mais (coisa que eu inventei ou trouxe do Qt sem conferir)
nem menos (coisa que eu esqueci).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(r"G:\App Corte Cenas")
FONTE = RAIZ / "poc_web" / "maquete_limpa.html"
SAIDA = Path(__file__).parent / "maquete_textos.json"

from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402

JS = r"""
(() => {
  // Cada tela é a moldura arredondada mais justa que contém a marca.
  const marcas = {
    analisar: 'Cortando cenas',
    resultados: 'top duplas',
    biblioteca: 'Acervo',
  };
  const acha = marca => {
    const todos = [...document.querySelectorAll('div')]
      .filter(d => d.textContent.includes(marca));
    todos.sort((a, b) => a.textContent.length - b.textContent.length);
    return todos[0] || null;
  };
  const moldura = no => {
    let p = no;
    for (let i = 0; i < 14 && p; i++) {
      const cs = getComputedStyle(p);
      if (parseFloat(cs.borderTopLeftRadius) >= 6 &&
          p.getBoundingClientRect().width > 500) return p;
      p = p.parentElement;
    }
    return no;
  };
  const out = {};
  for (const [nome, marca] of Object.entries(marcas)) {
    const n = acha(marca);
    if (!n) { out[nome] = null; continue; }
    const m = moldura(n);
    out[nome] = m.innerText.split('\n')
      .map(s => s.trim()).filter(Boolean);
  }
  return JSON.stringify(out);
})()
"""


def main() -> int:
    app = QApplication(sys.argv)
    v = QWebEngineView()
    v.resize(1900, 1200)
    v.show()

    def colhe(bruto) -> None:
        d = json.loads(bruto)
        SAIDA.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        for nome, linhas in d.items():
            print(f"{nome:<12} {len(linhas) if linhas else 0} linhas")
        print(f"\n{SAIDA}")
        app.quit()

    v.loadFinished.connect(
        lambda ok: QTimer.singleShot(1400, lambda: v.page().runJavaScript(JS, colhe))
    )
    v.load(QUrl.fromLocalFile(str(FONTE)))
    QTimer.singleShot(25000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
