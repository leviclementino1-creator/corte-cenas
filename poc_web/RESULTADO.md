# Prova de conceito — QWebEngineView + QWebChannel

29/07/2026. Os cinco passos combinados foram feitos, com dados reais (banco
do Mushoku T3 E2, 331 cenas, keyframes de verdade). Tudo abaixo é medido,
não estimado.

## O que roda

    python poc_web/poc.py            janela normal
    python poc_web/poc.py --foto     mede, tira o retrato e fecha

- `app_poc.html` — a Biblioteca inteira em HTML/CSS, com os tokens copiados
  de `app/ui/theme.py`.
- `ponte.py` — `Ponte` (o que o JS enxerga do Python) e `ServidorMiniatura`
  (o esquema `cena:/`).
- `extrai_maquete.py` — tira o runtime do bundler do Claude Artifact da
  maquete, deixando o Chromium resolver os `{{ }}` e copiando o DOM final.

## Números

| | Qt hoje | PoC WebEngine |
|---|---|---|
| abrir (import + QApplication) | 523 ms | 123 ms¹ |
| janela na tela | 306 ms | 30 ms |
| 331 cenas na grade | 953 ms | 10 ms no DOM + 1626 ms de miniaturas |
| memória (episódio carregado) | 214 MB | 300–350 MB |
| colunas a 1600px | 3 | 4 |
| respiro da grade a 1600px | 6px esquerda, **263px de vazio à direita** | 20px dos dois lados |
| respiro em 980/1180/1280/1440/1920 | varia | 20/20 em todas |
| ponte JS → Python | — | canal em 2 ms, chamadas chegando |

¹ Não é comparável: o PoC não importa torch, YOLO nem ONNX. O custo próprio
do WebEngine some ao lado do que o app já paga pra carregar os modelos.

## As três coisas que a prova revelou

**1. O Chromium do PySide6 não tem H.264.** Só codecs livres. Como todo
clipe do app é H.264/AAC, o `<video>` apontado pro `.mp4` morre com
`SRC_NOT_SUPPORTED`. Confirmado por `canPlayType`: h264 = `""`,
webm-vp9 = `"probably"`.
Saída que funciona: o Python transcodifica uma prévia VP8/WebM a 640px sob
demanda — **0,14 s** por clipe de ~4 s (VP9 em 1080p levava 1,9 s), guardada
em `metadata/previas_web/`. O `.mp4` original nunca é tocado. Com isso o
loop toca (`readyState 4/4`, `loop=true`).

**2. As miniaturas precisam de um servidor.** Apontar `file://` pros
keyframes faria o Chromium decodificar 331 JPEGs de 1080p (233 MB) pra
desenhar cartões de 292x164. O esquema `cena:/mini/<caminho>` entrega a
imagem já reduzida: 331 servidas, 3,3 ms cada, **2,9 MB** de cache no total.
E a página tem que ser servida pelo MESMO esquema — com ela em `file://` o
Chromium barra todo pedido `cena:` antes de chegar no Python.

**3. O CSS também tem armadilhas — mas dá pra achar medindo.** Duas
apareceram, e as duas estão comentadas no `app_poc.html`:
- `overflow:hidden` num cartão cujo filho tira a altura de `aspect-ratio`
  zera a altura intrínseca: as linhas do grid saíam com 2px.
- `minmax(min, 300px)` faz o `auto-fill` contar pelo máximo, não pelo
  mínimo. O certo é `minmax(max(196px, calc(20% - 9.6px)), 1fr)` — o `max()`
  é o teto de 5 colunas, o `1fr` reparte a sobra sozinho.

## Custo de tamanho

    Qt6WebEngineCore.dll         195 MB
    resources/ (Chromium)        101 MB
    qtwebengine_locales/          44 MB   <- 42 MB dá pra cortar
    resto (Process.exe, Quick…)    3 MB
    TOTAL                        343 MB   (301 MB sem os idiomas extras)

Instalador vai de ~2,0 GB pra ~2,3 GB. O delta update de 55 MB carrega isso
uma vez só.

## O build — PASSOU (30/07)

Era o risco que podia matar o plano, então foi testado antes de converter
qualquer tela. `poc_web/build_poc.spec` monta um exe mínimo (só o PoC, sem
torch/YOLO/ONNX) pra responder em um minuto em vez dos quinze do build
completo:

    pyinstaller poc_web/build_poc.spec --noconfirm --clean \
        --distpath poc_web/dist --workpath poc_web/build

Resultado: **69 s de build, 553 MB, e o exe roda inteiro.** O PyInstaller
achou sozinho o `QtWebEngineProcess.exe`, o `qtwebengine_resources.pak`, o
`icudtl.dat` e os 53 locales — não precisou de hook manual nenhum.

Congelado, medido:

| | fonte | exe |
|---|---|---|
| import + QApplication | 123 ms | **45 ms** |
| HTML carregado | 153 ms | 153 ms |
| 331 cartões no DOM | 9 ms | 8 ms |
| 331 miniaturas | 1425 ms | 1441 ms |
| memória | ~320 MB | 342 MB |
| ponte JS→Python | ok | ok |
| prévia em loop | ok | ok (`readyState 4/4`) |
| colunas e respiro | 20/20 em todas | idêntico |

Divisão do peso: 340 MB de QtWebEngine + 213 MB de Python/Qt. Dos 340, uns
42 MB são idiomas que a gente não usa e dá pra cortar no spec.

## O que ainda NÃO foi provado

- Menu de contexto (o Chromium tem o dele, precisa desligar e pôr o nosso),
  arrastar o episódio pra janela, atalhos de teclado com o foco na página e
  os diálogos de arquivo (esses continuam Qt, chamados do Python).
- Os botões ligados de verdade: hoje só dois passam pela ponte, o resto é
  casca. O trabalho é religar em `Ponte` o que já existe em `library_tab` e
  `results_tab` — sem lógica nova.
- O `build.spec` de VERDADE (com torch junto) e o delta update de 55 MB.
