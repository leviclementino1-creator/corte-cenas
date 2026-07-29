# Estado da interface — 29/07/2026

Nota de passagem escrita antes de compactar a conversa. Descreve o que está
no disco AGORA (nada commitado desde `71c7fe0`) e o que foi decidido.

## Decisão

A camada visual sai de **QWidgets + QSS** e vai pra **QWebEngineView +
QWebChannel**, mantendo backend, pipeline, IA e banco em Python. Primeiro uma
**prova de conceito**, e a decisão final com números na mão.

Por quê: o design vem do Claude Design em HTML/CSS e a tradução pra QSS nunca
fecha 1:1 — QSS é um subconjunto pequeno de CSS 2.1 (sem flexbox, sem grid,
sem sombra nem transição) e o motor de lista do Qt tem manias próprias.

Custo medido: QtWebEngine = 207 MB de binários + 106 MB de recursos do
Chromium (~313 MB). Instalador ~2,0 GB → ~2,3 GB; o delta de 55 MB carrega
isso uma vez.

## Prova de conceito (o combinado)

1. Abrir a maquete dentro de um `QWebEngineView` (aba escondida, sem tocar
   no que existe).
2. Tirar o runtime do bundler do Claude Artifact do HTML.
3. Conferir se fica visualmente idêntico.
4. Ligar UM botão do HTML a um método Python via `QWebChannel`.
5. Medir: tamanho do exe, tempo de abertura, memória com 331 miniaturas
   reais, comportamento do vídeo em loop.

Só depois migrar tela por tela. Nada de reimplementar lógica em JavaScript.

## Arquivos do design

- `design/identidade_v2.html` — a maquete (template legível, com `{{ }}` do
  runtime do bundler e os dados num `<script type="text/x-dc">` no fim).
- `design/identidade_v2_standalone.html` — auto-desempacotável, abre direto
  no navegador. É o que serve de base pro PoC.
- `design/_desempacotado/` — o que saiu do bundle (JS do runtime + template).
- `BRIEFING_DESIGN.md` — o briefing que gerou o design, com as restrições do
  Qt. Se a interface virar web, a seção 6 (restrições) fica obsoleta.

## O que está no disco e NÃO commitado

Revisão cirúrgica do layout segundo a especificação, toda em Qt:

- **Barra de abas**: 44 no total, aba de 36 encostada embaixo, 8 de respiro
  em cima, recuo de 12. Selo de GPU e Configurações alinhados na mesma
  linha de base, ambos em cápsula h26.
- **Aba Analisar**: deixou de ser três cartões e virou superfície plana —
  cada seção é `01 · Título · traço` com o conteúdo recuado 20. Rótulos em
  coluna fixa de 98, campos de 32, Temporada/Ep e OP/ED na mesma linha.
  Hierarquia dos botões: Descoberta vazado ciano, IA cinza, Analisar
  preenchido; secundários (Testar refs, Só cortar) numa linha compacta
  acima. Progresso mostra só texto + barra enquanto parado (a lista de
  etapas aparece quando a análise começa). UM stretch no fim.
- **Biblioteca**: ACERVO 248 e A CENA 320 fixos, só a grade estica; filtros
  em UMA linha com rolagem horizontal; ordenação sem a palavra "primeiro";
  atalhos logo abaixo dos botões (o vazio foi todo pro fim do painel).
- **Grade de cenas**: respiro de 12 desenhado POR DENTRO da célula (o
  `spacing` do QListWidget não somava nada entre as células — medido), item
  ocupando a célula inteira (sem isso o respiro vertical saía 18 e o
  horizontal 12), e a sobra da divisão repartida entre os dois lados.
- **Correções de verdade** achadas medindo: `_StripJob` emitia sinal pra
  grade já destruída ao trocar de episódio; a conta de colunas ignorava a
  moldura de 1px da lista (perdia uma coluna inteira).

### Pendência conhecida (se continuar em Qt)

A contagem de colunas ainda fica UMA abaixo do esperado em algumas larguras:
o Qt quebra a fileira quando a última coluna termina exatamente na borda do
viewport. A última tentativa (deixar 2px de folga dentro do viewport, em
`_ajustar_celulas`) está no disco e **não foi medida** — se o caminho web for
adiante, isso deixa de importar.

## Regras que continuam valendo

- Build só com `cmd //c "G:\App Corte Cenas\_build_all.bat"`, um por vez.
- Mudança em matching → `benchmarks/run_bench.py` antes e depois.
- `git commit -F <arquivo>` quando a mensagem tiver aspas ou travessão.
