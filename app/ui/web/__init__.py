"""A interface do Corte Cenas desenhada pelo Chromium.

O design vem do Claude Design em HTML/CSS e a tradução pra QSS nunca fechava
1:1 — QSS é um subconjunto pequeno de CSS 2.1, sem grid, sem flex, sem
sombra. Aqui a página É o desenho, e o Python continua dono de tudo que
importa: pipeline, IA, banco, arquivos.

    ponte.py       o que o JavaScript enxerga do Python (QWebChannel) e o
                   servidor `cena:/` que entrega miniaturas, tiras de hover
                   e os crops do batismo
    janela.py      a janela Qt que hospeda a página
    interface.html a interface inteira: quatro telas e o batismo

O laboratório de testes vive em `poc_web/poc.py` e importa DESTE pacote —
uma fonte só, pra teste e app nunca divergirem.
"""
from .janela import JanelaWeb  # noqa: F401
from .ponte import Ponte, ServidorMiniatura, registra_esquema  # noqa: F401
