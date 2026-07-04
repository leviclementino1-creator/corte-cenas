# Corte Cenas

Analisador de episódios de anime: corta em shots, identifica personagens via embeddings CLIP e organiza os clipes por personagem/dupla — sem depender de pastas alimentadas manualmente.

---

## Distribuir pra outras pessoas

Dois caminhos possíveis:

### A) Distribuição como código-fonte (recomendado — ~50 MB)

Zipa a pasta do projeto (sem `.venv`, `cache`, `Output`, `build`, `dist`) e manda. Usuário precisa ter:

- **Python 3.11+** ([python.org](https://python.org), marcar "Add to PATH")
- **NVIDIA GPU + CUDA 12.8+** (RTX 20xx ou mais nova)
- **FFmpeg** no PATH ([gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/))

Setup na máquina dele (uma vez):
```bat
install.bat
```
Cria `.venv`, baixa torch+cu128, ultralytics, PySide6, CLIP, etc. Demora 5-10min na primeira vez.

Pra rodar:
```bat
run.bat
```

### B) .exe empacotado com PyInstaller (~3 GB zipado)

Pra você gerar:
```bat
build.bat
```
Gera `dist/CorteCenas/CorteCenas.exe` + ~3 GB de libs. Zipa a pasta inteira e envia.

Usuário só extrai e roda `CorteCenas.exe`. Ainda precisa de **NVIDIA GPU + CUDA 12.8 + FFmpeg no PATH**.

Modelos (YOLO anime-face ~22 MB, CLIP ViT-L/14 ~890 MB) são baixados no primeiro run em ambos os caminhos.

---

## Como funciona (pipeline)

1. **Parse do arquivo** — extrai anime/temporada/episódio do nome (com override manual na UI).
2. **Detecção de shots** — PySceneDetect (ContentDetector).
3. **Corte + keyframes** — FFmpeg gera os `.mp4` de cada shot, 3 keyframes por shot para análise.
4. **Banco de personagens** — AniList (lista + MAL id) + Jikan (múltiplas fotos por personagem). Cacheado em `cache/anime_db/<id>/`.
5. **Download de referências** — 8 imagens por personagem (padrão).
6. **Embeddings** — open_clip ViT-B/32. Centroid por personagem armazenado no SQLite.
7. **Análise** — keyframes + rostos (cascade `lbpcascade_animeface`) → CLIP → cosine contra centroides → threshold por personagem.
8. **Organização** — `shots/`, `keyframes/`, `by_character/<Nome>/`, `by_pair/<A>+<B>/` (hardlinks NTFS — sem duplicar o .mp4), mais `metadata/shots.json`, `metadata/characters.json` e índice SQLite global.

---

## Estrutura de saída

```
Output/
  Witch Hat Atelier/
    S01E03/
      shots/0001.mp4 ...
      keyframes/0001_0.jpg ...
      by_character/Coco/0007.mp4 (hardlink)
      by_pair/Coco+Agott/0007.mp4 (hardlink)
      metadata/
        shots.json
        characters.json
```

Índice global: `cache/index.db` (SQLite). Reuso entre episódios: `cache/anime_db/<anilist_id>/`.

---

## Requisitos

- **Python 3.11+**
- **FFmpeg** no PATH (`ffmpeg -version` precisa funcionar)
- **NVIDIA GPU + CUDA** (opcional, muito recomendado — CPU roda mas é lento)
- Saída em volume **NTFS** no mesmo drive dos shots para hardlinks funcionarem (fallback para cópia se estiverem em drives diferentes).

---

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### Torch com CUDA (GPU NVIDIA)

O `requirements.txt` instala a versão CPU do Torch. Para GPU:

```bash
pip uninstall -y torch torchvision
pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
```

Se sua CUDA for diferente de 12.8, troque `cu128` por `cu121` / `cu124` conforme sua driver (Blackwell / RTX 50xx precisa cu128).

**Importante**: se depois instalar/atualizar `ultralytics`, ele pode reinstalar torch CPU. Rodar o comando acima de novo pra restaurar CUDA.

O primeiro run baixa automaticamente:
- `models/lbpcascade_animeface.xml` (~2 MB)
- Pesos CLIP ViT-B/32 (~350 MB) — cacheados pelo `open_clip`.

---

## Como rodar

```bash
python run.py
```

Fluxo na UI:

1. Aba **Analisar** → selecione o `.mp4`, confira anime/temporada/episódio, escolha a pasta de saída.
2. Clique em **Analisar episódio**. Acompanha pelas etapas.
3. Aba **Resultados** abre automaticamente: lista de personagens com contagem de shots, grid de thumbnails, duplo clique abre o .mp4.

---

## Ajustar threshold por personagem

O MVP grava um threshold padrão (`0.74`, recall-leaning) por personagem em SQLite. Para subir/descer:

```sql
-- cache/index.db
UPDATE character SET threshold = 0.82 WHERE name = 'Coco';
```

O próximo `Analisar` respeita o valor novo (sem re-baixar nada).

---

## Reprocessar só um episódio sem refazer o banco

O banco de personagens é cacheado por anilist_id em `cache/anime_db/`. Re-rodar um episódio do mesmo anime:

- **Não** refaz download de imagens (usa cache).
- **Não** refaz embeddings de referência se o registro no SQLite já estiver populado (centroide salvo na coluna `character.embedding`). Forçar recomputo: `DELETE FROM character WHERE anime_id = ?` ou apagar o folder `cache/anime_db/alXXXX/characters/`.
- Apaga e recria apenas as linhas `shot` + `shot_character` daquele episódio (ver `clear_episode_shots`).

---

## O que ainda falta / próximos passos

- **Revisão manual**: o schema já tem `shot_character.reviewed/approved` — falta UI para "Aprovar / Rejeitar" por thumbnail.
- **Detecção de objetos da cena** (livro, cajado, etc.) via Grounding DINO / CLIPSeg.
- **Transcrição** (Whisper) para reforçar identificação por quem está falando.
- **Ranking semântico por trecho de roteiro** — integra com seu app atual de geração de vídeo.
- **Escolher melhor CLIP** (ViT-L/14 ou Marqo/anime-aesthetic) se a precisão em estilos de traço específicos ficar baixa.
- **Jikan anime search fallback** quando AniList não conhece o título.

---

## Arquitetura (pastas)

```
app/
  main.py                  entrada PySide6
  pipeline.py              orquestra todas as etapas, emite progresso
  config.py                config persistente (~/.config/CorteCenas)
  video_ingest.py          parse do nome do arquivo
  shot_detection.py        PySceneDetect
  keyframe_extractor.py    FFmpeg + OpenCV
  providers/
    anilist.py             GraphQL
    jikan.py               REST v4
    anime_provider.py      resolver unificado + cache
  references/
    image_downloader.py
    reference_store.py     cache/anime_db/<id>/characters/<slug>/
  matching/
    face_detector.py       lbpcascade_animeface
    embedding_engine.py    open_clip, GPU/CPU
    character_matcher.py   centroides + cosine + threshold
    cooccurrence.py
  storage/
    db.py                  SQLite (schema + queries)
    metadata_writer.py     shots.json, characters.json
    organizer.py           hardlinks by_character/by_pair
  ui/
    main_window.py
    analyze_tab.py
    results_tab.py
    character_grid.py
    worker.py              QThread wrapper da pipeline
cache/
  index.db
  anime_db/<id>/characters/<slug>/*.jpg
Output/<Anime>/SxxEyy/...
models/
  lbpcascade_animeface.xml (auto-download)
```

---

## Escolhas técnicas

- **PySceneDetect ContentDetector** em vez do `absdiff` fixo do V7 — pega cortes duros, ignora variação de iluminação.
- **Re-encode com preset ultrafast** para shots. Stream-copy seria ~10x mais rápido mas corta em keyframe mais próximo, causando flash inicial. Ajustável via `Config.reencode_shots = False`.
- **open_clip ViT-B/32** — balance entre velocidade e qualidade. Roda em CPU (~3-5min/episódio) e em GPU (segundos).
- **lbpcascade_animeface** — detector dedicado a rostos de anime (o Haar frontal de humano do V7 falha). CLIP no crop de rosto costuma ser bem mais discriminante do que no keyframe inteiro.
- **Hardlinks NTFS** em vez de cópia — um shot entra em N categorias sem duplicar bytes.
- **Centroide por personagem** em vez de 1-NN contra todas as refs — mais robusto contra refs ruins.
- **Recall-leaning** (threshold 0.74) — melhor marcar a mais e filtrar na revisão.
```
