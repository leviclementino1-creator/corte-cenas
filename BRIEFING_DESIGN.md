# Corte Cenas — briefing pra identidade visual

Cole este arquivo inteiro na conversa de design. Ele descreve o app, todas as
telas e controles, e — a parte que mais importa — **o que a tecnologia de
interface do app consegue desenhar**. Um mockup lindo que usa recurso que o Qt
não tem vira trabalho jogado fora.

---

## 1. O que é o app

Aplicativo de desktop (Windows, PySide6/Qt) que pega um episódio de anime e
devolve as cenas cortadas, separadas por personagem.

O usuário arrasta um `.mkv`, o app detecta os cortes de câmera, recorta cada
cena num clipe, reconhece quem aparece (visão computacional: CLIP + YOLO +
CCIP, com uma IA opcional pros casos duvidosos) e organiza tudo em pastas:
`shots/` (todas as cenas), `by_character/Nome/`, `by_pair/A+B/`.

Um episódio de 23 minutos vira ~330 clipes em ~4 minutos.

**Quem usa:** um editor de vídeo, sozinho, no PC dele, várias horas seguidas.
Ele cria conteúdo com esses cortes. A sessão típica é: analisar um episódio,
depois **curar** o resultado por muito tempo — olhar cena por cena, tirar o
que o app errou, juntar cenas partidas, mover pra pasta certa.

**A tela mais usada é uma grade de miniaturas de vídeo.** Tudo gira em torno
de conseguir bater o olho em 300 cenas e achar as erradas rápido.

## 2. Personalidade pretendida

Instrumento de trabalho de vídeo — a família do DaVinci Resolve, Premiere,
Final Cut. Escuro porque fica ao lado de miniaturas de vídeo o dia inteiro
(fundo claro suja a leitura de cor da imagem). Sóbrio, dado numérico legível
de relance, nada de brinquedo colorido.

Não é um app de consumo, não precisa encantar em 3 segundos: precisa aguentar
4 horas de uso sem cansar e não mentir sobre o que está acontecendo.

## 3. Regras de cor que o app já segue (mantenha ou substitua conscientemente)

Cada cor tem um **papel**, não é enfeite. É o que faz a tela ser lida de
relance:

- **Ciano `#4cc9c0`** = selecionado, ativo, acontecendo agora.
- **Âmbar `#e8a15c`** = tempo e quantidade (duração, timecode, contagem).
  Ter uma cor *só* pra isso é o que deixa a grade legível sem ler.
- **Verde `#6fcf8b`** = estado bom real (GPU ativa). Nunca decoração.
- **Vermelho `#e5686f`** = destrutivo/erro. Nunca decoração.
- Números sempre em monoespaçada — impede as colunas de dançarem.

Paleta atual completa (pode ser substituída, mas mantenha os *papéis*):

| token | hex | uso |
|---|---|---|
| BG | `#0f1115` | fundo da janela |
| SURFACE | `#161a21` | painéis, barras laterais |
| SURFACE_2 | `#1d222b` | cartões, campos, botões |
| SURFACE_3 | `#252c37` | hover |
| LINE | `#272d38` | bordas |
| LINE_SOFT | `#1f242d` | bordas apagadas |
| TXT | `#e7ecf3` | texto principal |
| TXT_DIM | `#98a2b3` | secundário |
| TXT_FAINT | `#6b7484` | rótulo, terciário |
| ACCENT | `#4cc9c0` | destaque |
| ACCENT_DARK | `#2a6f68` | borda de destaque |
| ACCENT_INK | `#0c2b29` | fundo de item selecionado |
| TIME | `#e8a15c` | tempo/quantidade |
| OK | `#6fcf8b` | estado bom |
| DANGER | `#e5686f` | destrutivo |

Fontes hoje: `Segoe UI Variable Text` (texto), `Segoe UI Variable Display`
(títulos), `Cascadia Mono` (números).

---

## 4. Inventário de telas

### Janela

Barra de abas no topo com **Analisar · Resultados · Biblioteca**. No canto
direito da MESMA linha das abas: selo de GPU (`🟢 RTX 5080` / `🟡 CPU (lento)`
/ `⏳ detectando…`) e botão `⚙ Configurações`.

Tamanho mínimo 980×640; típico 1180×760; usado também em 1600×950. Precisa
funcionar em janela pequena e em monitor grande.

### Aba 1 — Analisar (formulário + progresso)

Seção **"1. Episódio"**:
- `Arquivo:` campo de texto + botão `Selecionar...` (aceita arrastar e soltar)
- `Anime:` campo de texto
- `Temporada/Ep:` dois campos numéricos (`T:` e `E:`)
- `Saída:` campo de texto + botão `Escolher pasta...`
- `OP/ED:` dois campos curtos — `Pular início até (MM:SS)`, `Pular fim após`

Seção **"Modo de reconhecimento"**:
- três opções exclusivas: `Muito Fiel` · `Auto (recomendado)` · `Pouco Fiel`
- link/botão `Mostrar valores manuais ⌄` que revela uma sub-seção
  **"Valores manuais"** com 6 campos numéricos: `Confiança mínima`,
  `Margem do top-1`, `Mín. shots por personagem`, `Padding do rosto`,
  `Limiar de créditos`, e caixas de marcar: `Detectar shots de créditos`,
  `Usar Danbooru como fonte extra`, `Segunda opinião local (CCIP)`,
  `Detecção de cenas rápida (experimental)`,
  `✂️ Só cortar as cenas (sem identificar personagens)`,
  `Criar pastas por personagem`, `Criar pastas de duplas`

Linha de ações (4 botões, hierarquia importa):
`Testar refs (preview)` · `🔍 Modo Descoberta` · **`Analisar episódio`**
(ação principal) · `Analisar + IA nos duvidosos`

Seção **"2. Progresso"**:
- barra de progresso + cronômetro/ETA em monoespaçada
- linha de status ("Aguardando…", "Cortando cena 214 de 331…")
- **lista de etapas** com estado: Lendo arquivo → Detectando shots →
  Cortando → Extraindo keyframes → Baixando referências → Reconhecendo →
  Organizando pastas. Cada uma pode estar: pendente, em andamento, feita,
  falhou. (Hoje é `○` / `●` / `✓` em texto — precisa de tratamento.)
- botão `✕ Cancelar análise` (aparece só rodando)

### Aba 2 — Resultados (o resultado da análise que acabou)

Duas colunas:
- **Esquerda:** título do episódio, resumo (`331 shots · 6 personagens ·
  top duplas: …`), lista de personagens com contagem de cenas, e botões
  `🔄 Sincronizar pastas`, `Abrir pasta do episódio`,
  `Exportar refs deste anime (.zip)`, `Reforçar refs com este ep`,
  `Exportar vertical 1080×1920`
- **Direita:** a grade de cenas do personagem selecionado

### Aba 3 — Biblioteca (o acervo; a tela mais usada)

Três colunas:

**Esquerda — ACERVO:** árvore `anime → temporada → episódio`, cada linha com
o nome à esquerda e uma contagem à direita (episódios, ou nº de cenas). O
episódio cuja pasta sumiu do disco aparece em itálico apagado. Embaixo, seção
**AÇÕES** com `↻ Atualizar lista` e `📂 Abrir pasta do episódio`. Botão
direito na árvore: apagar episódio / temporada / anime.

**Meio — as cenas:**
- título do episódio (`Mushoku Tensei III: … — S03E02`)
- linha de dados: `331 cenas · 6 personagens · 23:40` + seletor de ordem
  (`⏱ cronológica` / `⚠ duvidosas primeiro` / `⏳ mais longas`)
- **pílulas de filtro** por personagem: `📼 Todas (331)`, `Farion, Nina (91)`,
  `Greyrat, Eris Boreas (85)`, … (até ~10, quebram em 2 fileiras)
- **a grade de cartões de cena** — o coração do app, ver §5

**Direita — A CENA (o que está selecionado):**
- tela do clipe em 16:9 tocando **em loop**, com selo `▸ em loop`
- bloco de dados em monoespaçada: `cena #0129`, `tempo 08:24.6 → 08:28.0`,
  `duração 3.4s`, `quem Nina, Eris`
- três ações: **`⛓ Juntar com a próxima`** (principal), `↗ Mover pra outro
  personagem`, `⤫ Remover desta pasta` (destrutivo)

### Diálogos (todos precisam do mesmo tratamento)

`Configurações` (o maior: rola, tem 5 seções — pasta de saída, referências e
cache, AI principal, AI fallback, sobre/atualizações), `Modo Descoberta —
quem é quem?` (grade de rostos agrupados pra batizar), `Conferência do
elenco` (lista de caixas de marcar com ⚠ nos suspeitos), `GPU NVIDIA não
detectada`, `FFmpeg não encontrado`, `Reanalisar episódio`, `Apagar TODO o
cache`, e ~10 avisos menores.

---

## 5. O componente mais importante: o cartão de cena

A grade mostra de 2 a 5 colunas conforme a largura da janela, com 330 cartões
roláveis. Cada cartão carrega:

- a **miniatura** do frame (16:9)
- o **número da cena** (`#0129`) sobreposto no canto superior esquerdo da
  imagem — sobreposto de propósito, pra não roubar altura
- **quem aparece** na cena, no rodapé à esquerda (`Nina`, `Nina, Eris`, ou
  vazio quando ninguém foi identificado)
- a **duração** no rodapé à direita, em âmbar (`3.4s`)
- marca `⛓` quando a cena veio de uma junção manual
- estados: normal, mouse em cima, **selecionada**, e seleção múltipla
  (Ctrl/Shift/laço — as ações valem pra todas de uma vez)

Ao passar o mouse, a posição horizontal dentro do cartão **navega pelo tempo
da cena** (scrub estilo YouTube). Botão direito abre menu com: aprovar,
remover dessa pasta, mover pra outro personagem, juntar cenas, desfazer
junção.

Este cartão eu desenho pixel a pixel (não é HTML nem widget padrão), então
aqui **quase tudo é possível**: cantos arredondados, imagem recortada,
sobreposições, gradiente, sombra interna, o que o design pedir.

---

## 6. RESTRIÇÕES TÉCNICAS — leia antes de desenhar

A interface é **Qt (PySide6)** estilizada com **QSS**, que é um subconjunto
pequeno do CSS 2.1. O que **NÃO existe**:

- ❌ flexbox, grid, `gap`, `calc()`, variáveis CSS
- ❌ `box-shadow`, `text-shadow`, `filter`, `backdrop-filter`, `blur`
- ❌ `transition`, `animation`, `transform` (nada anima por CSS)
- ❌ `::before` / `::after` com `content` (não dá pra injetar ícone por CSS)
- ❌ `opacity` em widget
- ❌ fontes da web (o app é um `.exe` offline: só fontes do Windows 11 —
  Segoe UI / Segoe UI Variable / Cascadia Mono / Consolas)
- ❌ SVG como ícone de widget por CSS (ícone hoje = emoji ou caractere
  unicode: `⛓ ↗ ⤫ ↻ 📂 ⚙ 🗑 ⏱ ⚠ ▸ ▾`)

O que **existe e funciona bem**:

- ✔ cor de fundo chapada, borda (1–2px), `border-radius` (com valor
  **concreto** — `999px` **não** vira cápsula no Qt, ele não clampa: dê o
  raio em px, ex. 13px pra um selo de 26px de altura)
- ✔ `padding`, `margin`, `min-height`, `font-size`, `font-weight`
- ✔ estados: `:hover`, `:pressed`, `:checked`, `:disabled`, `:selected`,
  `:focus`
- ✔ gradiente linear/radial simples (`qlineargradient`, `qradialgradient`)
- ✔ desenho livre (QPainter) nos componentes que eu pinto: **cartão de cena**,
  miniaturas, a tela do player. Aí vale antialiasing, clipping, sobreposição,
  o que quiser.

Outras verdades do ambiente:

- Só tema **escuro** (não precisa de versão clara).
- Windows com escala 100–150%: nada pode depender de pixel exato pra caber.
- A janela redimensiona de 980px a 2560px de largura — o layout precisa
  dizer **o que estica** (a grade de cenas) e **o que fica parado** (as
  colunas laterais).

---

## 7. O que eu preciso de volta (formato do entregável)

Nesta ordem de importância:

1. **Tabela de tokens**: nome → valor. Cores (hex), tamanhos de fonte (px),
   pesos, escala de espaçamento, raios de borda, alturas de controle. Com uma
   frase dizendo o **papel** de cada cor (não "azul bonito": "azul = ação
   principal").

2. **Especificação dos componentes com os estados, em pixel**: botão
   (principal / secundário / vazado / fantasma / destrutivo), campo de texto,
   campo numérico, caixa de marcar, opção exclusiva, pílula de filtro, selo,
   **cartão de cena**, linha de árvore, linha de lista, aba, barra de
   progresso, item de etapa (pendente/rodando/feito/falhou), menu de contexto,
   diálogo, cabeçalho de seção.
   Para cada um: fundo, borda, raio, padding, cor do texto — em normal, hover,
   pressionado, selecionado e desabilitado.

3. **Um HTML de página única** mostrando as três abas montadas com dados
   reais (os do §4), usando só o que o §6 permite. Serve como referência
   visual que eu traduzo pra QSS.

4. **Layout com medidas**: larguras das colunas, o que estica, alturas de
   cabeçalho, ritmo de espaçamento entre seções.

Se algo do §6 impedir uma ideia boa, **fale**: eu desenho o cartão de cena à
mão e posso levar bem mais longe do que o CSS permite ali.

---

## 8. Anexos que valem mandar junto

- `v3_biblioteca_grande.png` — Biblioteca em 1600×950 (estado atual)
- `v3_analisar_min.png` — aba Analisar em 980×640 (estado atual)
