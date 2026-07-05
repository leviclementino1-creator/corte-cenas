# Corte Cenas

Analisador de episódios de anime pra Windows. Corta em shots automaticamente, identifica os personagens em cada shot via embeddings CLIP + YOLO anime-face, e organiza os clipes por personagem e por dupla — sem tu precisar alimentar pasta com fotos.

---

## 📥 Instalar (usuário final)

**1 arquivo, 3 cliques, ~1 minuto:**

1. Baixe **[CorteCenas-Setup mais recente](https://github.com/leviclementino1-creator/corte-cenas/releases/latest)** (~2 GB, procura o `CorteCenas-Setup-X.Y.Z.exe`).
2. Dois cliques no arquivo baixado.
3. Wizard do Windows: **Avançar → Avançar → Instalar → Concluir**.

Aparece atalho na área de trabalho e no menu iniciar. Fim.

O instalador já traz **FFmpeg embutido** — nada de baixar de outro site ou mexer em PATH.

### Requisitos

| Item | Recomendado | Mínimo |
|---|---|---|
| SO | Windows 10/11 x64 | Windows 10 x64 |
| GPU | NVIDIA RTX 20xx+ com CUDA 12.8+ | Qualquer (roda em CPU, ~20x mais lento) |
| RAM | 16 GB | 8 GB |
| Disco | 8 GB livres (setup + modelos + cache) | 5 GB |
| Internet | Necessária pra baixar modelos na primeira análise (~900 MB) e pra baixar as fotos dos personagens de cada anime | idem |

Sem GPU NVIDIA o app roda mesmo assim (avisa que vai ficar lento). Sem internet, o primeiro uso pra cada anime não funciona (precisa baixar refs).

---

## 🔄 Atualização automática

Toda vez que o app abre, ele checa o GitHub por versão nova. Se tiver, mostra:

> **Corte Cenas v0.1.3 está disponível.**
> Você tem **v0.1.2**.
> Quer atualizar agora?

Se aceitar: baixa o novo instalador em background, roda por cima da versão atual (dados/configurações preservados), pronto.

Pra forçar checagem manual: **⚙ Configurações → 🔄 Verificar atualizações agora**.

---

## 🎬 Como usar

### 1. Aba "Analisar"

- **Arquivo:** seleciona o `.mp4` do episódio.
- **Anime / Temporada / Episódio:** o app tenta deduzir do nome do arquivo, mas confirma. Pra spin-offs / temporadas específicas (Dr. Stone S4), preencher direito ajuda.
- **Saída:** onde ficam os clipes cortados. Trocar em ⚙ Configurações.
- **Pular OP/ED:** timestamps `MM:SS` — o app ignora tudo antes/depois.
- **Modo de reconhecimento:**
  - **Muito Fiel** — threshold alto, só marca quando tem certeza. Menos shots por personagem, quase zero erro.
  - **Auto (recomendado)** — balanceado.
  - **Pouco Fiel** — threshold baixo, marca mais. Você filtra manualmente no fim.

### 2. Botões de análise

- **Analisar episódio** (verde) — pipeline padrão CLIP + YOLO. Rápido, gratuito, sem chamar API externa.
- **Analisar com IA** (azul, dropdown com 2 modos) — usa Gemini pra classificar cada shot. Melhor precisão em animes menos conhecidos, mas gasta quota das API keys.
  - **Full** — manda o keyframe inteiro pra IA.
  - **Hybrid** — YOLO detecta rostos, IA classifica só os crops (mais barato).

### 3. Aba "Resultados"

- Lista de personagens com quantos shots cada um teve.
- Grid de thumbnails do personagem selecionado.
- **Duplo clique** num thumb abre o `.mp4` do shot.
- **Botão direito** num thumb: aprovar, remover ou mover pra outro personagem.
- Botões laterais:
  - **Cortar vertical (Reels/TikTok)** — gera versão 1080×1920 focando no rosto do personagem.
  - **Reforçar refs com este ep** — pega os melhores frames deste episódio e adiciona ao banco de refs. Melhora o próximo episódio.

---

## 🤖 AI Review (opcional)

Duas providers configuráveis em ⚙ Configurações:

### NavyAI (principal)

Gateway compatível com OpenAI que roteia pro Gemini 2.0. Se tu tem key `sk-navy-...`, cola em **AI principal**.

### Gemini direto (fallback)

Se a NavyAI falhar (429 quota, 500 erro, timeout), o app **automaticamente** cai na API direta do Gemini. Pega a key gratuita em [aistudio.google.com/apikey](https://aistudio.google.com/apikey), cola em **AI fallback**.

Se só uma das duas estiver preenchida, ela é usada sozinha. Nada pra escolher, nada pra clicar — fallback é automático.

---

## 📂 Onde ficam as coisas

**Instalação:** `C:\Program Files\CorteCenas\`

**Config:** `C:\Users\<seu_user>\AppData\Local\CorteCenas\config.json`

**Saída dos clipes** (customizável): `Output\<Anime>\SxxEyy\`

```
Output/
  Dr Stone [al172019]/
    S04E25/
      shots/
        0001.mp4        # shots cortados
        0002.mp4
      keyframes/
        0001_0.jpg      # 3 keyframes por shot pra análise
      by_character/
        Senku/
          0002.mp4      # hardlink NTFS, não duplica bytes
          0007.mp4
        Kohaku/
          0001.mp4
      by_pair/
        Senku+Kohaku/
          0007.mp4      # shot onde os dois aparecem
      metadata/
        shots.json
        characters.json
```

**Cache global** (todos animes): `cache/` — reutilizado entre episódios do mesmo anime. Se tu apagar, refaz do zero.

---

## 🖥️ Rodar do código-fonte (desenvolvedor)

Se quiser modificar o código, não precisa do instalador:

```bat
git clone https://github.com/leviclementino1-creator/corte-cenas.git
cd corte-cenas
install.bat
```

O `install.bat` cria `.venv/`, instala torch+cu128 (~2.7 GB), demais deps do `requirements.txt`, ultralytics, huggingface_hub. Demora 5-10 min.

Depois:

```bat
run.bat
```

O código roda **direto** do fonte. Editar `app/*.py` reflete no próximo `run.bat`.

### Buildar teu próprio instalador

Precisa de **Inno Setup 6** ([jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php)).

```bat
build_installer.bat
```

Roda em ordem: `fetch_ffmpeg.py` (baixa ffmpeg~200 MB) → PyInstaller (~10 min) → Inno Setup (~3 min). Saída: `releases/CorteCenas-Setup-X.Y.Z.exe`.

### Publicar release nova

1. Bump `__version__` em [app/\_\_init\_\_.py](app/__init__.py) → `"0.1.3"`.
2. Bump `AppVersion` em [installer.iss](installer.iss) → `"0.1.3"`.
3. `git commit -am "v0.1.3"` + `git push`.
4. `git tag v0.1.3` + `git push origin v0.1.3`.
5. `build_installer.bat`.
6. `gh release create v0.1.3 releases/CorteCenas-Setup-0.1.3.exe --title "v0.1.3" --notes "..."`.

Todos os usuários com o app instalado recebem o popup de update no próximo abrir.

---

## ⚙️ Pipeline (o que acontece internamente)

1. **Parse do arquivo** — extrai anime/temporada/episódio do nome do `.mp4`.
2. **Detecção de shots** — [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) `ContentDetector` no espaço HSV.
3. **Corte + keyframes** — FFmpeg gera `.mp4` de cada shot (re-encode `libx264 ultrafast`), extrai 3 keyframes JPG.
4. **Banco de personagens** — [AniList GraphQL](https://anilist.gitbook.io/) resolve o anime, pega lista de personagens e MAL id. [Jikan REST](https://jikan.moe/) pega múltiplas fotos por personagem. Franchise pooling: se for temporada N, BFS pelas relações do AniList pra pegar personagens das temporadas anteriores.
5. **Download de referências** — 8 imagens por personagem (padrão), filtradas por saturação (manga preto-e-branco vai pra `_filtered/`).
6. **Embeddings** — `open_clip ViT-L/14` (openai pretrained). Centroide por personagem (média dos embeddings de todas as refs).
7. **Análise dos shots** — YOLO `deepghs/anime_face_detection` detecta rostos nos keyframes → CLIP no rosto → cosine contra os centroides → threshold por personagem. Fallback: se YOLO não achar rosto, CLIP no keyframe inteiro (opcional).
8. **Organização** — cria hardlinks NTFS: `by_character/<Nome>/`, `by_pair/<A>+<B>/`. Sem duplicar bytes.

Se tu usar **"Analisar com IA"**, os passos 6-7 são substituídos: pipeline manda o frame (ou o crop YOLO) pro Gemini via NavyAI/Google, ele responde qual personagem.

---

## 🏗️ Arquitetura (pastas)

```
app/
  main.py                  entrada PySide6 (QApplication)
  __init__.py              __version__
  pipeline.py              orquestra o fluxo, emite progresso
  config.py                config persistente (AppData/Local/CorteCenas)
  updater.py               auto-update via GitHub Releases API
  deps_check.py            checa ultralytics/hf_hub/ffmpeg/CUDA
  ffmpeg_locate.py         resolve bundled ffmpeg vs PATH
  ai_review.py             NavyAI + Gemini fallback (OpenAI-compat)
  video_ingest.py          parse do nome do arquivo
  shot_detection.py        PySceneDetect wrapper
  keyframe_extractor.py    FFmpeg + OpenCV
  reframe.py               vertical crop pra Reels/TikTok
  harvest.py               reforço de refs a partir de shots identificados

  providers/
    anilist.py             GraphQL search + relations
    jikan.py               REST v4 (character pictures)
    danbooru.py            (opt-in, off por padrão)
    anime_provider.py      resolver unificado + franchise pooling

  references/
    image_downloader.py    httpx async
    image_filters.py       saturação (drop manga refs)
    reference_store.py     cache/anime_db/<id>/

  matching/
    face_detector.py       YOLO deepghs + lbpcascade fallback
    embedding_engine.py    open_clip ViT-L/14, GPU/CPU
    character_matcher.py   centroides + cosine + threshold
    cooccurrence.py        pares (A+B) por co-ocorrência

  storage/
    db.py                  SQLite (schema + queries)
    metadata_writer.py     shots.json, characters.json
    organizer.py           hardlinks NTFS
    skip_ranges.py         OP/ED time skip

  ui/
    main_window.py         topo com botão settings + tabs
    analyze_tab.py         seleção de arquivo, presets, botão IA
    results_tab.py         lista de personagens + grid + botões laterais
    character_grid.py      thumbnails + right-click menu
    settings_dialog.py     3 blocos: NavyAI, Gemini, Sobre/GPU/updater
    deps_dialog.py         MissingDeps, FFmpeg, NoGpu
    worker.py              QThread wrapper da pipeline

  assets/
    icon.ico + icon_*.png  ícone do app (7 tamanhos)

bin/                       (não versionado — baixado por fetch_ffmpeg.py)
  ffmpeg.exe               empacotado no instalador
  ffprobe.exe

cache/                     (não versionado)
  index.db                 SQLite global
  anime_db/<id>/characters/<slug>/*.jpg
  huggingface/             modelos (YOLO, CLIP)

Output/<Anime>/SxxEyy/     (customizável em Configurações)
```

---

## 🔧 Ajustar threshold por personagem

Cada personagem tem threshold próprio no SQLite. Padrão: `0.80`. Pra subir/descer:

```sql
-- cache/index.db
UPDATE character SET threshold = 0.85 WHERE name = 'Senku';
```

O próximo `Analisar` respeita o valor novo (sem re-baixar refs, sem re-embeddar).

---

## 🔁 Reprocessar um episódio sem refazer tudo

O banco de personagens é cacheado por `anilist_id` em `cache/anime_db/<id>/`. Ao re-rodar o mesmo episódio:

- **Não** refaz download de imagens de referência.
- **Não** refaz embeddings de referência (centroides salvos no SQLite).
- **Apaga e recria** só as linhas `shot` + `shot_character` daquele episódio.

Pra forçar recomputo total: `DELETE FROM character WHERE anime_id = ?` no SQLite, ou apagar `cache/anime_db/al<ID>/characters/`.

---

## 🧪 Escolhas técnicas

- **PySceneDetect ContentDetector** — pega cortes duros, ignora variação de iluminação.
- **Re-encode com preset ultrafast** — stream-copy seria 10x mais rápido mas corta em keyframe mais próximo (causa flash inicial). Ajustável via `Config.reencode_shots = False`.
- **open_clip ViT-L/14** — melhor discriminação em anime que ViT-B/32. Roda em GPU (segundos) e CPU (~3-5 min/episódio).
- **YOLO deepghs anime_face** — ~3x melhor hit rate que `lbpcascade_animeface`, 34 ms/img em GPU.
- **Fallback pra lbpcascade** — se `ultralytics` não estiver instalado, cai no XML clássico.
- **Hardlinks NTFS** em vez de cópia — shot entra em N categorias sem duplicar bytes. Fallback pra cópia se drives diferentes.
- **Centroide por personagem** em vez de 1-NN contra todas as refs — mais robusto contra refs ruins.
- **Franchise pooling** — Dr. Stone S4 puxa personagens de S1/S2/S3 automaticamente via AniList relations.
- **NavyAI → Gemini fallback** — plano free dos dois cobre bastante volume.

---

## 🗺️ Próximos passos

- **Revisão manual UI**: o schema tem `shot_character.reviewed/approved` — falta botão "Aprovar / Rejeitar" batch.
- **Detecção de objetos da cena** (livro, cajado, tubo de ensaio) via Grounding DINO / CLIPSeg.
- **Transcrição** (Whisper) pra reforçar identificação por quem está falando.
- **Ranking semântico por trecho de roteiro** — integração com apps de geração de vídeo.
- **Jikan anime search fallback** quando AniList não conhece o título.
- **Progress bar de download de modelos** — hoje só mostra texto.

---

## 📄 Licença

MIT. Vide [LICENSE](LICENSE) (a criar).
