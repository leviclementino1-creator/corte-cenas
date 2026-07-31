# Inventário de avisos — Corte Cenas v0.5.0

> **ESTADO EM 31/07/2026 — TODOS OS 51 ITENS CORRIGIDOS.**
>
> Auditoria de tudo que o app avisa e de tudo que deveria avisar e não avisa.
> 67 acusações levantadas, 66 sobreviveram à verificação, 51 depois de tirar
> repetido. Distribuição: 35 alta, 29 média, 2 baixa.
>
> O Levi pediu a lista TODA antes de publicar a v0.5.0, e ela foi feita em
> cinco ondas, cada uma testada e commitada:
>
> | Onda | O que é | Itens | Commit |
> |---|---|---|---|
> | A | destrói dado | 1.4 1.5 1.6 1.7 2.2 2.3 3.1 3.4 | `43ef413` |
> | B | mente sobre sucesso | 1.1 1.2 1.3 1.10 1.11 1.12 1.17 1.18 5.11 | `e9c0298` |
> | C | UI web incompleta | 1.8 1.9 1.14 1.19 1.20 2.1 2.4 4.1 4.2 4.3 4.4 4.5 5.8 5.9 5.10 | `dcded45` |
> | D | arranque/updater | 1.13 5.1 5.2 5.3 5.4 5.5 5.6 5.7 | `92d2ae2` |
> | E | o resto | 1.15 1.16 1.21 2.5 2.6 2.7 2.8 3.2 3.3 3.5 4.6 | `50a881f` |
>
> **O texto abaixo descreve o estado ANTES das correções.** Ele fica como
> está de propósito: é o registro do que estava errado e por quê, e vale
> mais como memória do que como lista de tarefas. Cada correção está
> comentada no código, no lugar onde o defeito morava.
>
> **Achados que só apareceram DURANTE o conserto** (nenhum estava na lista):
> - `shutil.move` numa pasta não é atômico — falha no meio deixa a cópia na
>   lixeira E a original, possivelmente incompleta (onda A).
> - `var(--bad)` não existe no CSS: o ✕ da etapa que morreu não ficaria
>   vermelho. Era `--danger` (onda C).
> - `Ponte.cfg` era uma property com `Config.load()` a cada acesso — a Ponte
>   ignorava a config recebida, e isso tornou impossível isolar um teste.
>   Custou o cache do Levi, recuperado da `cache_lixeira` (onda C).
> - Duas mensagens novas mentiam: `clean_catalog_refs` desempacotado
>   invertido e `wipe_cache` lido como "movidos" quando devolve "não
>   movidos" (onda C).
> - Um `print` com `→` DENTRO do try de `salvar_config`: em console cp1252
>   ele levanta, o `except` pega, e um Salvar que funcionou virava "não deu
>   pra salvar" (onda D).
> - NVENC recusado na máquina do Levi: driver com nvenc API 13.0, exigido
>   13.1 — todos os cortes indo pra CPU (onda B).
>
> A seção "Avisos que já estão bons" no fim continua sendo o melhor lugar
> pra copiar padrão de aviso.
>
> ---
>
> ## O que a lista NÃO pegou — e o Levi pegou em duas horas de uso
>
> Terminada a lista, ele abriu o app e usou. Saíram **nove** defeitos que 67
> acusações de leitura de código não tinham visto, e o motivo é o mesmo em
> todos: **eles precisam de um segundo anime na biblioteca, de uma análise
> rodando, ou de mais de uma cena marcada.** Nenhum aparece num app parado.
>
> | O que ele viu | O que era | Commit |
> |---|---|---|
> | "não mostra progresso nenhum" | o QWebChannel para de entregar sinal Python→JS enquanto uma análise existe. A tela só tinha sido testada pelo `ensaiar()`, que roda SEM análise | `9d61f07` |
> | "bateu 100% e não abriu o batismo" | mesmo canal; e o caminho da Descoberta dava `return` sem encerrar a thread, o que também travava o Cancelar e condenava o `batizar` | `cfb1d3b` |
> | "o app fechou sozinho" | `finished.connect(lambda ...)` → conexão DIRETA → o fim da análise rodava na thread do worker e destruía a própria QThread. `qFatal`, 0xc0000409, sem traceback | `f76395a` |
> | "não abriu em Resultados" | ninguém trocava de aba (o Qt faz desde sempre), e o fim do batismo ia sem `episodio_id` | `4831963` |
> | "a UI bugou quando o anime entrou" | nome comprido é item anônimo de flex: quebra em 3 linhas dentro de uma linha de 28px | `4831963` |
> | "a prévia não carrega" | ela carregava — de outra cena. E a guarda de resposta atrasada lia o PRIMEIRO `.viva`, não o último clicado | `4831963` |
> | "removi e não removeu" | `cenas[dataset.idx]` mistura POSIÇÃO de array com NÚMERO de cena. Episódio com OP pulado começa na #0022: `cenas[22]` é a #0044 | `42b9810` |
> | (não relatado, achado na auditoria) | Del com os Resultados na frente agia na seleção invisível da Biblioteca | `42b9810` |
> | (não relatado, achado na auditoria) | Shift+clique + J gravava junção de 21 minutos sem perguntar | `42b9810` |
>
> **A lição, que vale mais que os nove:** ler código acha o que está escrito
> errado. Não acha o que **nunca foi exercitado**. Os cinco piores do dia
> moram em estados que só existem com o app trabalhando — thread viva, duas
> abas cheias, dois animes, seleção múltipla — e o teste que "provava" o
> progresso rodava justamente no único estado em que ele funciona.
>
> Também não achei nada disso auditando: quem achou foi o Levi usando, e
> depois uma auditoria em cinco frentes com cético adversarial por achado
> (15 levantados, 11 confirmados). O `feature_cache` que **nunca salvou desde
> que virou "atômico"** (`c4ebfe8`) apareceu porque fui olhar uma pasta no
> disco pra responder outra pergunta.

## O padrão que isso tudo desenha

1. **O app só sabe falar quando dá certo.** Quase todo caminho de erro termina em `print`, `except: pass` ou num `lambda` que joga o objeto fora — e a tela, no silêncio, mostra o estado de sucesso por padrão. O `terminou` é o retrato: falha, refs faltando e anime não achado chegam como string e viram tarja verde com 100% e ✓ em tudo.
2. **A ponte web é um funil que estreita informação boa.** O Python produz os melhores textos do projeto (protagonista cego, refs faltando, conferência de elenco, os três botões de reanálise) e `ponte.py` descarta o `PipelineResult` e o `refs_dir` pra emitir uma palavra.
3. **A inteligência de aviso ficou toda no fallback Qt.** A UI web herdou o layout e não herdou as perguntas: reanálise, closeEvent, cancelar, pasta parecida, pasta de personagem sumida — tudo existe no Qt e some na interface padrão.
4. **Onde a página é maquete, ela afirma coisas que ninguém checou:** "RTX 4070" verde fixa, chave de API mascarada sem chave, T=3/E=2 cravados no HTML, sete botões que hoverizam e não fazem nada.
5. **As ações mais permanentes são justamente as que menos perguntam:** `record_manual('block')` numa tecla Del, `clear_episode_shots` num clique no Modo Descoberta, `delete_episode` mesmo quando a lixeira falhou.

---

## 1. Analisar um episódio

### 1.1 ALTA — Análise que morre diz "Análise terminada." com 100% e tudo ✓
- **Quando:** a análise falha por qualquer motivo (exceção, refs faltando, anime não encontrado) com a janela em foco.
- **Hoje:** `recado(como === 'cancelado' ? 'Análise cancelada.' : 'Análise terminada.');` — tarja verde de 4s, sem a flag `ruim`; e o `if (como !== 'cancelado')` logo abaixo força `100%`, enche a barra e marca TODAS as etapas com ✓. A mensagem real do Python é descartada.
- **Devia:** usar a mesma regra que já existe uma linha acima (`como === 'pronto' || como === 'batizado'`): sucesso pinta 100%, falha vira tarja vermelha + caixinha que fica, com a etapa que estava rodando marcada ✕ e o motivo completo.
- `app/ui/web/interface.html:1778` (e o bloco 1789-1796)

### 1.2 ALTA — As duas mensagens mais bem escritas do app nunca chegam na tela
- **Quando:** o anime não é achado na AniList/Jikan, ou ninguém ficou com refs utilizáveis.
- **Hoje:** `lambda m, _p: self._fim_da_analise(f"refs faltando: {m}")` — o `_p` é o `refs_dir`, jogado fora; tudo vira string em `terminou` e cai no 1.1. Some o "Abrir pasta de refs" e some a oferta do Modo Descoberta, que são exatamente as duas saídas.
- **Devia:** sinais próprios (`refsFaltando(msg, pasta)`, `animeNaoAchado(msg)`) e caixinha com os botões — o Modo Descoberta já existe na web (`interface.html:490`), o docstring que diz que ele "ainda mora no app Qt" está desatualizado.
- `app/ui/web/ponte.py:651-654`

### 1.3 ALTA — Sucesso degradado não avisa nada (protagonista cego, elenco suspeito, grupos sem nome)
- **Quando:** a análise termina bem, mas o protagonista ficou sem refs, sobrou cena sem dono ou o elenco veio suspeito.
- **Hoje:** `self._worker.finished.connect(lambda _r: self._fim_da_analise("pronto"))` — o `_r` leva junto `low_refs_warning`, `cast_review` e `leftover_groups`. O Qt mostra os três; a web, nenhum.
- **Devia:** emitir um segundo sinal com o resultado serializado; no mínimo a caixinha do `low_refs_warning` + "Abrir pasta de refs" e reaproveitar `abreBatismo` pros `leftover_groups` (o payload é o mesmo que `_guarda_descoberta` já traduz).
- `app/ui/web/ponte.py:648`

### 1.4 ALTA — Reanalisar substitui a análise antiga sem perguntar
- **Quando:** analisa de novo um episódio que já tem análise salva (depois de reforçar refs, por exemplo).
- **Hoje:** o `PipelineWorker` é montado sem `merge_previous` (default False) e sem consultar `db.has_analysis` → `clear_episode_shots` e pronto. O Qt pergunta com três botões: "Substituir (recomendado)" / "Adicionar por cima" / "Cancelar".
- **Devia:** checar `has_analysis` antes de montar o worker e devolver `{ok:false, precisa_escolher:'reanalise'}` pra página abrir a mesma pergunta — incluindo o bullet de que a curadoria manual sobrevive nas duas opções.
- `app/ui/web/ponte.py:637`

### 1.5 ALTA — Temporada e episódio vêm da maquete (3 e 2) e gravam por cima de outro episódio
- **Quando:** solta `Mushoku Tensei S03E05.mkv` na janela e clica em Analisar.
- **Hoje:** os giros nascem com `<span class="v">3</span>` e `<span class="v">2</span>` no HTML; nada chama `parse_filename` na web. O E05 é gravado como S03E02, `upsert_episode` casa por (anime, season, episode) e o `clear_episode_shots` seguinte apaga os shots do E02 de verdade.
- **Devia:** expor `parse_filename` na ponte e preencher anime/T/E no drop e no `escolher_arquivo`, como o Qt faz. Sem parse, marcar os giros como palpite em vez de mostrar número com cara de valor lido.
- `app/ui/web/interface.html:468`

### 1.6 ALTA — "Só cortar" apaga a identificação inteira do episódio, e ainda engole o botão de IA
- **Quando:** a caixa "✂ Só cortar as cenas" ficou marcada e ele clica em Analisar (ou em "Analisar + IA") num episódio já identificado.
- **Hoje:** `clear_episode_shots` + reinserção sem dono, e `return` antes de reaplicar `manual_override`. As pastas `by_character/` continuam no disco (o `clear_grouping` não roda nesse caminho), então parecem intactas enquanto o acervo mostra cenas sem dono. Pior: `cut_only=bool(d.get("so_cortar"))` é passado sempre, e `pipeline.run` testa `cut_only` primeiro — clicar em "Analisar + IA" com a caixa marcada ignora a IA em silêncio.
- **Devia:** perguntar quando o episódio já tem atribuições ("apaga a identificação de N cenas; sua curadoria manual continua guardada e volta numa análise completa") e não deixar `so_cortar` valer quando o clique foi no botão de IA.
- `app/pipeline.py:131` (passagem em `app/ui/web/ponte.py:643`)

### 1.7 ALTA — Modo Descoberta apaga a identificação antes de qualquer nome ser dado
- **Quando:** clica em "🔍 Modo Descoberta" (que fica coladinho no Analisar) num episódio que já tem análise.
- **Hoje:** `run_discovery` chama `clear_episode_shots`, e `shot_character` cai por CASCATA. O único retorno é o recado de 4s "Procurando rostos — o batismo abre no fim."
- **Devia:** perguntar antes quando há atribuições ("a identificação atual é apagada agora e só volta quando você batizar os grupos ou reanalisar") e avisar, ao fechar o batismo, que o episódio ficou sem identificação.
- `app/ui/web/interface.html:1084-1086` (efeito em `app/pipeline.py:1829`)

### 1.8 ALTA — Fechar o batismo joga a Descoberta fora, sem uma palavra
- **Quando:** a tela de batismo abre e ele clica em Cancelar pra "ver depois".
- **Hoje:** `bat_nao` faz só `classList.remove('viva')`. O `DiscoveryResult` continua vivo em `ponte._descoberta`, mas nenhum slot o devolve — não existe caminho pra reabrir a tela.
- **Devia:** confirmar antes ("Nada foi gravado; os cortes ficam em shots/; pra ver os grupos de novo é preciso rodar a Descoberta outra vez") e guardar o payload no JS pra oferecer "Reabrir batismo".
- `app/ui/web/interface.html:1060`

### 1.9 ALTA — Fechar a janela no X mata a análise sem perguntar
- **Quando:** clica no X com 30 minutos de análise rodando.
- **Hoje:** `JanelaWeb` não tem `closeEvent` — o QThread morre com o processo. O Qt pergunta ("O processamento vai ser interrompido; shots já cortados ficam salvos.") e ainda encerra a thread limpo.
- **Devia:** `closeEvent` checando `ponte._thread`, com as três coisas: o que para, o que fica em cache, e que rodar de novo continua de onde parou.
- `app/ui/web/janela.py:76` (modelo em `app/ui/main_window.py:215-247`)

### 1.10 ALTA — Cancelar no meio pode deixar um resultado parcial misturado com a análise antiga, e a tarja não conta
- **Quando:** cancela depois que a análise passou do corte e entrou em "Analisando cenas".
- **Hoje:** "Análise cancelada." e mais nada. Mas `clear_episode_shots` já rodou: ficam as cenas parciais inseridas até ali, as pastas `by_character/` com o espelho antigo e o `shots.json` desatualizado. (Cancelar durante detecção/corte é inofensivo — `_prepare_shots` roda antes.)
- **Devia:** mandar o estágio junto do "cancelado" e dizer só a verdade daquele caso: no corte, "nada foi perdido, os clipes ficam em cache"; depois disso, "o resultado anterior foi substituído por um parcial — rode de novo antes de curar".
- `app/ui/web/interface.html:1778` (causa em `app/pipeline.py:213`)

### 1.11 ALTA — Cenas que o ffmpeg não consegue cortar somem sem serem contadas
- **Quando:** disco enche, arquivo travado por outro programa, codec que o ffmpeg não corta — no meio do corte de 300 cenas.
- **Hoje:** `process()` devolve `None` e o `return [r for r in indexed if r is not None]` filtra fora. Nada é contado, nada é impresso, nada é emitido; o pipeline usa `len(cut_results)` e nunca compara com `len(shots)`.
- **Devia:** contar as falhas e reportar no fim: "N cena(s) o ffmpeg não conseguiu cortar (<motivo>) — reanalisar tenta de novo só as que faltaram" (o `.mp4` não existe, então o `skip_existing` não pula).
- `app/keyframe_extractor.py:421-424` e `:461`

### 1.12 ALTA — OP/ED podem zerar o episódio inteiro e a análise termina "com sucesso"
- **Quando:** digita 15:00 no OP de um episódio de 12 min, ou troca OP e ED de lugar.
- **Hoje:** o filtro esvazia a lista, o único registro é um `print` no app.log, `cut_all_shots` devolve `[]` e a análise vai até o fim com `total_shots=0` — barra 100%, tudo ✓.
- **Devia:** abortar com erro dedicado quando o skip apagar tudo (ou mais de ~80%): "O OP e o ED juntos cobrem o episódio inteiro — não sobrou nenhuma cena. Eles são a DURAÇÃO de cada trecho, não o horário."
- `app/pipeline.py:2243-2250`

### 1.13 ALTA — Nenhuma checagem de espaço em disco, em lugar nenhum
- **Quando:** começa uma análise com pouco espaço livre na saída.
- **Hoje:** `analisar()` valida três coisas (análise rodando, arquivo existe, nome do anime) e dispara. Se o disco enche no corte, cai no 1.11: a cena é descartada em silêncio e o app emite `finished` → "pronto". Zero hits de `disk_usage`/`ENOSPC` no projeto.
- **Devia:** `shutil.disk_usage(saida)` antes do worker e `pergunta()`: "Sobram X GB e essa análise deve escrever ~Y GB. Se acabar o espaço a análise para no meio e os cortes já feitos ficam onde estão. Continuar?"
- `app/ui/web/ponte.py:595`

### 1.14 MÉDIA — Batismo mostra 6 fotos, mas 8 viram referência
- **Quando:** tira uma foto intrusa de um grupo na tela de batismo.
- **Hoje:** a ponte publica `g.thumbs_jpg` (`thumbs[:6]`), enquanto o commit aplica os índices sobre `g.ref_crops_jpg` (`thumbs[:8]`). A dica na tela diz "clique numa foto pra tirar ela das referências" — e duas das que entram nunca apareceram.
- **Devia:** publicar `ref_crops_jpg` (os índices já batem). O Qt faz isso de propósito: "o que você vê é exatamente o que vai pro banco".
- `app/ui/web/ponte.py:684`

### 1.15 MÉDIA — O 1:30 do OP/ED já vem posto e ninguém diz que aquilo é descartado
- **Quando:** solta um episódio sem abertura (recap, especial, ep 1) e clica em Analisar sem mexer nos campinhos.
- **Hoje:** rótulo "OP/ED" e dois campos com `1:30`. 90s do começo e 90s do fim são jogados fora — nem cortados, nem analisados — e o único registro é um print.
- **Devia:** nota embaixo: "Esses trechos não são cortados nem analisados. Deixe 0:00 se o episódio não tiver abertura/encerramento."
- `app/ui/web/interface.html:472-474`

### 1.16 MÉDIA — Escrever o tempo errado é indistinguível de deixar em branco
- **Quando:** digita `1;30`, `90s` ou `1.30` no campo contenteditable.
- **Hoje:** `except Exception: return 0.0` — o OP não é pulado e a abertura inteira entra como ~40 cenas de créditos. `1.30` é pior: não levanta, vira 1,3 segundo.
- **Devia:** `parse_mmss` devolver `None` em erro e a ponte responder `{ok:false, msg:"'1;30' não é um tempo válido — use MM:SS (ex.: 1:30) ou segundos (90)"}` antes de começar. E desconfiar de valores abaixo de ~5s.
- `app/video_ingest.py:84-85`

### 1.17 MÉDIA — Revisão por IA pulada em silêncio (sem chave, sem crédito)
- **Quando:** pede análise com IA sem chave configurada ou com a quota estourada.
- **Hoje:** `print(f"[AI review] Pulado: {e}")` + `client = None`, sem nenhum `cb(...)`. Mesmo padrão na revisão por grupo e na quota. A análise termina como sucesso legítimo.
- **Devia:** `cb('ai_review', 1.0, 'Revisão IA PULADA: falta chave de API em Configurações')` e o aviso propagado no resultado. Na web o botão "Analisar + IA nos duvidosos" segue outro caminho (`_run_ai_recognition`), onde a exceção vira `failed` e a tela diz "Análise terminada." — conserta junto.
- `app/pipeline.py:1450-1454` (e `:1204-1208`, `:1470-1473`)

### 1.18 MÉDIA — Elenco veio pela metade (MAL fora do ar) e ninguém conta
- **Quando:** o Jikan responde 504 em parte das consultas mas ainda sobram 5+ personagens com refs.
- **Hoje:** o comentário promete avisar "no progresso agora", mas só há `print`; o `cb` manda apenas "{n} personagens". `source_warnings` só vira tela nos dois caminhos de erro.
- **Devia:** `cb('fetch_characters', 1.0, f'{n} personagens — ATENÇÃO: o MyAnimeList falhou em {k} consulta(s), o elenco pode estar incompleto')` e anexar no `low_refs_warning` com "reanalisar mais tarde deve recuperar o elenco completo".
- `app/pipeline.py:184-190`

### 1.19 MÉDIA — A tela finge que a análise começou antes de saber se começou
- **Quando:** clica em Analisar sem arquivo escolhido, ou clica de novo com uma análise rodando.
- **Hoje:** `zeraProgresso()` roda antes do `ponte.analisar`. Com `{ok:false}` a tela fica com cara de análise em curso e o botão Cancelar visível pra sempre (quem o esconde é o `terminou`, que não vem). No clique duplo durante uma análise real, `t0Analise` reinicia: o "decorrido" volta pra 00:00 e o "resta ~" mente o resto da rodada. Mesmo defeito no Modo Descoberta.
- **Devia:** chamar `zeraProgresso()` só dentro do callback quando `r.ok`, nos dois botões; e fazer `Ponte.cancelar` devolver JSON em vez de ser slot mudo.
- `app/ui/web/interface.html:998` (e `:1083`)

### 1.20 MÉDIA — Cancelar não dá sinal de vida
- **Quando:** clica em "✕ Cancelar" durante a análise.
- **Hoje:** chama `ponte.cancelar()` e pronto — botão continua ativo, barra continua andando, nenhum recado. O cancelamento é cooperativo e pode demorar minutos se um modelo estiver baixando.
- **Devia:** trocar o rótulo pra "Cancelando…", desabilitar o botão e mostrar o texto que o Qt já tem: "espera a operação atual terminar (um download de modelo pode levar minutos)".
- `app/ui/web/interface.html:1090`

### 1.21 MÉDIA — O balão da bandeja despeja traceback cortado no caractere 180
- **Quando:** a análise falha com exceção e a janela está fora de foco.
- **Hoje:** `titulos.get(motivo, ("Corte Cenas — Análise parou", motivo[:180]))` — e o `motivo` é `"falhou: " + exceção + traceback`. É o único canal que hoje diz alguma coisa sobre falha, e ele entrega texto ilegível.
- **Devia:** só a primeira linha da exceção (como `analyze_tab._on_failed` faz) e corpo fixo: "A análise parou. Abra o app pra ver o motivo e os detalhes."
- `app/ui/web/janela.py:94-95`

---

## 2. Mexer na Biblioteca e curar cenas

### 2.1 ALTA — A aba Resultados fica vazia logo depois de analisar
- **Quando:** termina uma análise sem ter aberto a Biblioteca e clica em Resultados.
- **Hoje:** o `terminou` copia o `#titulo` da Biblioteca (que vale `—`, ou "Biblioteca vazia" pra quem acabou de analisar o primeiro episódio) e revela o `#res_corpo`; quem preenche meta, elenco e grade é o `carregaCenas`, que só roda por `abreEpisodio`. Resultado: painel em branco com "Analise um episódio e o resultado aparece aqui".
- **Devia:** usar o `PipelineResult` (que já chega no `finished` e hoje é descartado) pra preencher título/meta/elenco, e trocar pra aba Resultados quando a análise termina com a janela em foco, como o Qt faz.
- `app/ui/web/interface.html:1784`

### 2.2 ALTA — Remover UMA cena é permanente, silencioso e cabe numa tecla
- **Quando:** aperta <kbd>Del</kbd>, clica em "⤫ Remover desta pasta" ou usa o menu do botão direito.
- **Hoje:** cai no `else` sem caixinha nenhuma e grava `record_manual(..., 'block')` — a reanálise nunca mais devolve aquela cena pra aquele personagem. O retorno é um recado de 4s: `"1 cena(s) fora da pasta de Rimuru"`. (O ramo plural, com o texto bom, é código morto: a grade é de seleção única e todos os chamadores mandam um índice só.)
- **Devia:** manter sem caixinha, mas ensinar a volta no próprio recado: "Cena #0142 fora da pasta de Rimuru — lembrado nas próximas análises. Pra desfazer, mova ela de volta pra ele." E apagar ou implementar o ramo plural.
- `app/ui/web/interface.html:1270-1272`

### 2.3 ALTA — Ação nos Resultados bate na pasta do personagem filtrado na Biblioteca
- **Quando:** filtra a Biblioteca por um personagem, vai pros Resultados, abre OUTRO personagem e manda Remover/Mover pelo botão direito.
- **Hoje:** o menu de contexto está no `document` e pega qualquer `.cartao`, inclusive os de `#r_grade`; mas `donoDaCena` decide pelo `filtro`, que é estado da Biblioteca (`if (filtro && filtro !== '__sem__') return filtro;`). Grava `block` no personagem errado e confirma com o nome errado. Depois o `resposta()` recarrega e pula pro PRIMEIRO personagem da lista.
- **Devia:** `donoDaCena` saber de qual grade veio a ação, e `resposta()` redesenhar a grade de origem mantendo o personagem aberto.
- `app/ui/web/interface.html:1238`

### 2.4 ALTA — A prévia continua tocando a cena anterior quando a nova falha
- **Quando:** clica na segunda cena em diante e o ffmpeg falha ou o clipe não existe.
- **Hoje:** `if (!url) { ... 'sem prévia'; return; }` — só que o `#tv_vazio` foi escondido na primeira prévia e nada volta a mostrá-lo dentro do episódio, então "preparando…" e "sem prévia" são escritos num elemento invisível. E o `tv.src` não é limpo: o `<video loop>` segue rodando a prévia antiga enquanto a tabela mostra os dados da cena nova.
- **Devia:** `tv.removeAttribute('src'); tv.load();` e `tv_vazio.style.display=''` antes de escrever; restaurar o display também no "preparando…" e trocar por "gerando prévia…" (o ffmpeg roda síncrono, demora).
- `app/ui/web/interface.html:843-850`

### 2.5 ALTA — "Sincronizar pastas" aplica direto, sem dizer o que vai fazer
- **Quando:** mexeu nas pastas pelo Explorer e clica em "🔄 Sincronizar pastas".
- **Hoje:** `recado('Conferindo as pastas…')` e aplica. Cada arquivo que falta vira `remove_shot_character` + `record_manual('block')`, e toda pasta com nome desconhecido cria personagem novo — renomear "Rimuru" pra "Rimuru Tempest" no Explorer deixa dois personagens duplicados no acervo.
- **Devia:** sync em duas etapas — contar primeiro e mostrar: "Vou aplicar: N cenas movidas, M removidas (o app passa a LEMBRAR; pra desfazer, devolva o clipe pra pasta e sincronize de novo), P personagem(ns) novo(s) criado(s) a partir de pasta desconhecida: <nomes>. Aplicar?"
- `app/ui/web/interface.html:1647`

### 2.6 MÉDIA — "Pastas já estavam em dia." quando o app viu a diferença e decidiu ignorar
- **Quando:** apaga a pasta inteira de um personagem em `by_character/` e sincroniza.
- **Hoje:** `if not pasta.exists(): continue  # pasta inteira sumida: NÃO conta aqui` → nada movido, nada removido → `msg = "Pastas já estavam em dia."`. O Qt pergunta, uma vez por personagem: "A pasta do personagem X não existe mais em by_character/. Remover ele deste episódio?"
- **Devia:** contar as pastas sumidas e perguntar; enquanto não houver pergunta, no mínimo: "Nada a sincronizar — mas a pasta de <X> sumiu por inteiro e foi ignorada por segurança."
- `app/ui/web/ponte.py:927-928` e `:948`

### 2.7 MÉDIA — "Abrir no Explorer" abre a pasta do episódio errado (ou o Output inteiro, ou nada)
- **Quando:** botão direito num episódio da árvore → "📂 Abrir no Explorer"; ou "Abrir pasta do episódio" antes de abrir qualquer episódio.
- **Hoje:** os dois chamam `ponte.abrir_pasta('episodio')`, que abre `self.raiz` (o episódio ABERTO) e ignora o `alvoAcervo` que o menu acabou de calcular. Antes de abrir qualquer episódio, `raiz` ainda é a raiz de saída. E se a pasta não existe, o slot não faz nada e não devolve nada.
- **Devia:** passar o id do episódio alvo e fazer o slot devolver `{ok,msg}` — "Abri <pasta>" ou "A pasta deste episódio não existe mais em <saída>".
- `app/ui/web/interface.html:1170` (slot em `app/ui/web/ponte.py:1015-1024`)

### 2.8 BAIXA — "use o app atual pra isso" manda ele procurar um programa que não existe
- **Quando:** clica em "✚ Reforçar refs com este ep" ou "▯ Exportar vertical 1080×1920".
- **Hoje:** `recado('Ainda não migrado — use o app atual pra isso.', true);` — o "app atual" é o MESMO executável com `--classico`/`CORTECENAS_UI=classico`, que não está escrito em nenhum .md, .bat ou atalho do instalador. Some junto a explicação que o Qt dava em tooltip (o vertical é 9:16 centralizado no rosto; o reforço grava crops ≥0.90 como `auto_` nas refs).
- **Devia:** caixinha (não recado de 4s) dizendo o que a função faz e como chegar lá — e criar esse atalho no instalador, já que o reforço de refs é o jeito barato de melhorar o reconhecimento entre episódios.
- `app/ui/web/interface.html:1664`

---

## 3. Apagar coisa

### 3.1 ALTA — Apagar episódio: a lixeira falha, o banco apaga assim mesmo
- **Quando:** apaga do acervo um episódio com algum arquivo aberto (Explorer, VLC, a própria prévia .webm).
- **Hoje:** o `except Exception` só faz `print` e o `db.delete_episode` roda na linha seguinte, FORA do try. A caixinha tinha prometido: "• A pasta vai pra `Output/_lixeira` — **não** é apagada de verdade". Resultado: registro (cenas, atribuições, `manual_override`) apagado, pasta órfã no disco, e um recado dizendo "1 episódio(s) fora do acervo · 0 pasta(s) na lixeira" sem explicar o zero.
- **Devia:** copiar o Qt — não tocar no banco e devolver `{ok:false, msg:'Não deu pra mover a pasta de <ep> (algum arquivo está aberto em outro programa). Nada foi apagado do acervo.'}`.
- `app/ui/web/ponte.py:569-574` (modelo em `app/ui/library_tab.py:948-961`)

### 3.2 MÉDIA — O aviso de apagar não diz o tamanho nem que a curadoria daquele episódio morre
- **Quando:** botão direito num episódio (ou num anime inteiro) → Apagar do acervo.
- **Hoje:** `'• O personagem, as fotos de referência e o que o app aprendeu ficam'` — não diz quantas cenas saem (o Qt diz), e "o que o app aprendeu" é ambíguo: o `delete_episode` apaga o `manual_override` do episódio, que é a memória de curadoria. Arrastar a pasta de volta da `_lixeira` não traz isso.
- **Devia:** "Tirar <ep> (N cenas) do acervo? • A pasta vai pra Output/_lixeira — dá pra arrastar de volta • As fotos de referência e os personagens ficam • A curadoria DESTE episódio (X remoções/movidas lembradas) é apagada e NÃO volta com a pasta."
- `app/ui/web/interface.html:1179` (efeito em `app/storage/db.py:537`)

### 3.3 MÉDIA — Reanalisar refaz `by_character/` do zero e leva o arranjo manual junto
- **Quando:** arruma clipes no Explorer e clica em Analisar sem ter clicado antes em "Sincronizar pastas".
- **Hoje:** `clear_grouping(episode_root)` faz `rmtree` em `by_character/` e `by_pair/` e reconstrói pelo banco. Nenhuma mensagem antes nem depois. Os clipes não se perdem (são hardlinks e o mestre fica em `shots/`), mas o arranjo que ainda não virou linha no banco some.
- **Devia:** contar arquivos que o banco não conhece e perguntar: "Achei N clipes nas pastas que o app ainda não registrou. Sincronizar antes? Se não, a organização é refeita pelo banco (os clipes continuam em shots/, só o arranjo se perde)." Ou chamar `apply_folder_moves` sozinho antes.
- `app/pipeline.py:1673`

### 3.4 MÉDIA — Juntar cenas apaga .mp4 de verdade, sem passar pela lixeira (só no Qt)
- **Quando:** usa "Juntar cenas" na interface clássica.
- **Hoje:** o aviso é bom mas incompleto — diz "Vira um arquivo só, sem recodificar" e "O app LEMBRA", e não diz que os clipes absorvidos somem: `merge_shots` faz `unlink()` nos .mp4 e nos keyframes. É o único ponto do app que destrói clipe, contra a regra da casa. (Na web o `_juntar` só marca e não apaga nada.)
- **Devia:** acrescentar "• Os outros N-1 clipes originais saem do shots/ — desfazer a junção só separa de novo na próxima análise" e mandar os arquivos pra `Output/_lixeira` em vez de `unlink`.
- `app/ui/results_tab.py:925-929` (efeito em `app/curation.py:157-159`)

### 3.5 BAIXA — "Isso não tem volta." sobre uma operação que tem volta
- **Quando:** Configurações → Apagar TODO o cache, no app Qt.
- **Hoje:** o aviso antes do clique diz `"Isso não tem volta."`, mas `wipe_cache` só faz `shutil.move` pra `cache_lixeira/<data>` — e a mensagem de sucesso, 23 linhas abaixo no mesmo método, diz que dá pra recuperar de lá.
- **Devia:** "Tudo isso vai pra cache_lixeira (ao lado do cache) — dá pra recuperar de lá enquanto você não apagar a pasta na mão."
- `app/ui/settings_dialog.py:657`

---

## 4. Trocar configuração

### 4.1 ALTA — Sete botões mortos, com hover, embaixo de uma nota que explica o que eles fariam
- **Quando:** abre Configurações → "03 Referências e cache" e clica em qualquer um: Testar refs, Abrir pasta de referências, Abrir pasta de cache, Fundir duplicados, Limpar fotos baixadas, Restaurar padrões, "🗑 Apagar TODO o cache" (mais o "Mostrar" da API key).
- **Hoje:** nenhum tem listener — os únicos handlers de `.btn-campo` filtram por texto começando com "Escolher" e "Selecionar". O CSS dá `:hover`, então o botão se anuncia como vivo. E a nota logo abaixo detalha o efeito de dois deles: "**Limpar fotos baixadas** zera só o que veio da internet…".
- **Devia:** ligar na ponte (os handlers do Qt já existem prontos) ou desativar visualmente com um "ainda não migrado" na seção. Botão que parece vivo e não faz nada é pior que botão ausente — quem tem ref suja acredita que limpou e queima uma análise inteira de GPU.
- `app/ui/web/interface.html:657-663` (nota em 664-667)

### 4.2 MÉDIA — Config que não carregou vira "está tudo desligado", e o Salvar grava isso
- **Quando:** o Config falha ao carregar (arquivo corrompido, campo novo, permissão) e ele abre Configurações.
- **Hoje:** `return json.dumps({"saida": ..., "erro": str(e)})` — sem `ok`, sem `preset`, sem as caixas; o `carregaConfig` nunca lê `erro`, nenhum modo fica selecionado e todas as caixas ficam desmarcadas. Salvar em cima grava `por_personagem`/`por_dupla`/`ccip`/`deteccao_rapida` como False.
- **Devia:** devolver `{"ok": false, "msg": ...}`, mostrar recado vermelho e bloquear o Salvar: "Não deu pra ler as configurações (<erro>). Salvar agora sobrescreveria seus ajustes."
- `app/ui/web/ponte.py:1062-1063`

### 4.3 MÉDIA — Cancelar nas Configurações não descarta nada
- **Quando:** desmarca "Criar pastas por personagem", clica em Cancelar (ou Esc) e reabre o diálogo.
- **Hoje:** `fechar_config` e o Esc só fazem `classList.remove('viva')`. `carregaConfig()` roda uma única vez, no boot — a marcação mexida continua na tela como se fosse o estado salvo, e o próximo Salvar leva ela junto.
- **Devia:** chamar `carregaConfig()` ao abrir e ao fechar sem salvar (e depois de um Salvar bem-sucedido).
- `app/ui/web/interface.html:756`

### 4.4 MÉDIA — "Escolher pasta..." na aba Analisar escreve no campo do outro diálogo
- **Quando:** clica em "Escolher pasta..." ao lado do campo Saída, na tela Analisar, e escolhe uma pasta.
- **Hoje:** o listener casa qualquer `.btn-campo` que comece com "Escolher", mas o callback escreve só em `#s_saida` (o campo do modal). O `#c_saida`, que é o que ele está olhando, não muda, nada é gravado, nenhum recado — e o valor fica pendurado pro próximo Salvar das Configurações.
- **Devia:** atualizar os dois campos e dar o recado ("Saída: <pasta> — vale a partir da próxima análise"), ou tirar o botão da aba Analisar e deixar a troca só onde já existe a pergunta de confirmação.
- `app/ui/web/interface.html:1728-1734`

### 4.5 MÉDIA — Chave de API mascarada sem chave nenhuma
- **Quando:** abre Configurações → IA de apoio sem nunca ter configurado chave.
- **Hoje:** o campo é `••••••••••••••••••••••••••••` fixo no HTML e o modelo é `gemini-2.5-flash` cravado. O `config()` até devolve `tem_chave` e `modelo`, e o `carregaConfig` não usa nenhum dos dois.
- **Devia:** preencher pelo `tem_chave` ("sem chave — a IA de apoio não vai rodar") e escrever o modelo real; melhor ainda, desabilitar o botão "Analisar + IA nos duvidosos" quando não houver chave — hoje ele confia na máscara, a análise morre lá na frente e a tela diz "Análise terminada." (1.1).
- `app/ui/web/interface.html:674-676`

### 4.6 MÉDIA — "Mushoku" e "Mushoku Tensei" viram duas pastas, e a escolha errada fica gravada
- **Quando:** digita o nome do anime diferente do que já existe na saída.
- **Hoje:** a validação da ponte para em `if not info.anime`. `app/storage/pastas.py` nem é importado na web, e ninguém no app inteiro define `perguntar_pasta` — então `resolver` cai no passo 4, cria a pasta com o nome digitado E GRAVA a decisão na memória. Depois disso nem o Qt pergunta mais, porque ele desiste cedo quando a chave já está na memória.
- **Devia:** chamar `pastas.parecidas()` no início do `analisar` e devolver `{ok:false, precisa_escolher:[...]}` pra página abrir as três opções do Qt ("Guardar em X" / "Criar Y" / "Cancelar"), gravando com `pastas.apontar` antes de montar o worker.
- `app/ui/web/ponte.py:633-634`

---

## 5. Abrir e atualizar o app

### 5.1 ALTA — Cair na interface antiga não é anunciado em lugar nenhum
- **Quando:** atualiza pra v0.5.0 numa máquina onde o QtWebEngine não carrega (DLL faltando, delta por cima de uma v0.4.x, driver que derruba o Chromium).
- **Hoje:** `_log.exception("QtWebEngine indisponível — caindo pra interface clássica")` e mais nada — vai pro app.log. A janela clássica sobe com o título de sempre e nenhuma tela diz que aquilo é fallback.
- **Devia:** QMessageBox antes de mostrar a janela ("A interface nova não carregou nesta máquina — abri a antiga pra você não ficar sem app. A v0.5.0 ESTÁ instalada; nada dos seus cortes mudou. Motivo: <erro>. Log em %LOCALAPPDATA%\CorteCenas\logs\app.log") + faixa fixa no topo. Não confundir com o caminho voluntário `--classico`, que não precisa de alarme.
- `app/main.py:199-201`

### 5.2 ALTA — Pasta de saída fora do ar: o app troca e GRAVA a troca em silêncio
- **Quando:** a saída está num HD externo e ele abre o app com o disco desconectado (ou a pasta perdeu permissão).
- **Hoje:** `except (OSError, PermissionError):` → troca pro padrão, marca dirty e chama `self.save()` dentro de `try/except: pass`. O caminho configurado é apagado do config.json. Quando ele reconecta o HD, o app continua apontando pra `Documentos\CorteCenas\Output`: **Biblioteca vazia**, e a tela de Configurações mostra o caminho novo como se sempre tivesse sido aquele.
- **Devia:** não persistir a troca. Usar o padrão só na sessão e avisar na abertura: "A pasta <X> não está acessível agora (disco desconectado?). Nesta sessão vou usar <Y>. NADA foi movido nem apagado — reconecte e reabra que ele volta sozinho." Só gravar se ele confirmar.
- `app/config.py:342-352`

### 5.3 ALTA — config.json corrompido: volta tudo pro padrão em silêncio, e o primeiro Salvar mata o original
- **Quando:** queda de energia durante um save, disco cheio no meio da escrita — e ele reabre o app.
- **Hoje:** `except Exception: pass` engole o JSON inválido e devolve um Config zerado, sem aviso e sem cópia. Qualquer Salvar depois reescreve o arquivo inteiro, levando junto as chaves de API, a pasta de saída, os thresholds e o `last_anime`. E o próprio `save()` não é atômico — é justamente o que produz a corrupção.
- **Devia:** mover pra `config.json.quebrado-<timestamp>` antes de seguir (mesma regra da `_lixeira`) e avisar na abertura, dizendo que os cortes não foram tocados e onde está o arquivo antigo. Bônus barato: `save()` atômico (.tmp + `Path.replace`).
- `app/config.py:277-284`

### 5.4 ALTA — Nada impede duas instâncias no mesmo banco
- **Quando:** dois cliques no ícone (ou o updater relança enquanto a instância velha respira) e ele roda análise nas duas.
- **Hoje:** zero trava (`QLockFile`/`QSharedMemory`/`QLocalServer` não aparecem no projeto), e `sqlite3.connect` é chamado cru — sem `timeout=`, sem WAL. A segunda leva "database is locked" depois de 5s e mostra... "Análise terminada." com 100%. As duas ainda escrevem clipes na mesma pasta.
- **Devia:** `QLockFile` no início do `main()` trazendo a janela existente pra frente ("O Corte Cenas já está aberto — trouxe a janela pra frente."). Se a decisão for permitir duas: WAL + `timeout=30` e mensagem própria pro lock.
- `app/main.py:203`

### 5.5 ALTA — Atualização: o app some depois do UAC, e uma falha de cópia não é reportada nunca
- **Quando:** aceita a atualização por delta e confirma o UAC.
- **Hoje:** o helper PowerShell é disparado com `SW_HIDE` e o app chama `app.quit()`. Nada diz que o app vai FECHAR nem que reabre sozinho. O `apply_update.ps1` espera 15s, dá `Stop-Process -Force` **por nome** (mata até uma instância nova que ele abrir nesse meio-tempo), roda robocopy e, se falhar, escreve "APPLY FAILED" num .log e não relança. Ninguém no app lê esse log nem o exit code.
- **Devia:** caixa antes ("Vou fechar o Corte Cenas agora pra aplicar a atualização. Ele reabre sozinho em até 1 minuto — não abra manualmente nesse tempo. Seus cortes e configurações não são tocados.") e o helper deixando um `apply_result.json` que o app lê no próximo arranque: "A atualização falhou (código N) — rode o CorteCenas-Setup por cima."
- `app/updater.py:216-224` + `apply_update.ps1:46-49`

### 5.6 MÉDIA — "Você pode dar dois cliques nele manualmente pra atualizar" — num .zip
- **Quando:** nega o UAC, roda do fonte, ou o `apply_update.ps1` não está na instalação.
- **Hoje:** o `except` é genérico e monta sempre a mesma mensagem, mas no caminho delta o arquivo é `CorteCenas-Update-<tag>.zip`. Dois cliques abrem o Explorer e não atualizam nada. Quem tenta ser esperto extrai por cima da instalação e fura a checagem de fingerprint que existe justamente pra evitar isso.
- **Devia:** usar o `is_delta` que já está no escopo: "Não consegui aplicar a atualização rápida (<motivo>). Ela precisa de permissão de administrador. Baixe o CorteCenas-Setup-X.Y.Z.exe da página da release — o arquivo em <path> é um pacote interno e dois cliques nele NÃO atualizam o app."
- `app/updater.py:338-342`

### 5.7 MÉDIA — Download em "0%" por dez minutos, e cancelar não diz nada
- **Quando:** atualiza numa release que só tem o setup completo (~2 GB), ou num proxy sem Content-Length.
- **Hoje:** "Baixando atualização..." sem tamanho e sem origem; `if total:` significa que sem Content-Length o sinal de progresso NUNCA é emitido — barra cravada em 0%. Cancelar é mudo: a thread sai sem emitir nada e o arquivo parcial fica no %TEMP% pra sempre.
- **Devia:** usar o campo `size` do asset da API do GitHub ("Baixando CorteCenas-Setup-0.5.1.exe — 340 MB de 2,1 GB"); sem total, `setRange(0,0)` e MB acumulados. Ao cancelar: "Download cancelado — nada foi instalado, o app continua na v<atual>."
- `app/updater.py:305` e `:109`

### 5.8 MÉDIA — A UI padrão não diz em que versão você está, nem deixa verificar atualização
- **Quando:** quer saber a versão ou forçar uma checagem.
- **Hoje:** as Configurações web param na seção "04 — IA de apoio". Zero menção a versão no HTML e nenhum slot expõe `__version__`. A seção "Sobre / Atualizações" com o número e o botão "🔄 Verificar atualizações agora" só existe no Qt — e `check_and_offer_update(verbose=True)` tem um único chamador, lá. Sem internet na abertura, a checagem falha em silêncio absoluto.
- **Devia:** seção "05 — Sobre" com "Corte Cenas v0.5.0 — interface nova" (ou "interface antiga (fallback)"), botão de verificar ligado num slot novo, e "Abrir pasta de logs". Os três já existem prontos no lado Qt. Isso também é o que faz o 5.1 ser percebido.
- `app/ui/web/interface.html:678`

### 5.9 MÉDIA — "RTX 4070" com bolinha verde em qualquer máquina
- **Quando:** abre o app, em qualquer PC.
- **Hoje:** `<div class="capsula"><span class="ponto"></span><span class="leg">RTX 4070</span></div>` é HTML da maquete, e `.ponto` é verde fixo no CSS. Nenhum slot de GPU existe na ponte. Numa máquina sem GPU o app afirma em verde que está numa 4070 — contradizendo o NoGpuDialog que o `main.py` abre por cima da própria janela.
- **Devia:** slot `dispositivo()` usando `deps_check`, pintando âmbar + "CPU (lento)" sem GPU, neutro + "detectando…" enquanto o torch carrega, verde só com CUDA ativa. O Qt já monta esse selo direito.
- `app/ui/web/interface.html:444`

### 5.10 MÉDIA — "Nenhuma GPU NVIDIA com CUDA foi detectada" também quer dizer "não deu pra saber"
- **Quando:** o torch demora demais (HD lento/antivírus) ou o import levanta exceção (DLL do CUDA quebrada) numa máquina que TEM GPU.
- **Hoje:** `except Exception: has_cuda = False` no `main.py` e o mesmo apagamento em `deps_check.py:78-79`. O diálogo afirma categoricamente que não há GPU e que vai ficar "~20x mais lenta", sem dizer o que foi checado — e se ele marcar "Não mostrar de novo", `gpu_warning_dismissed=True` fica gravado pra sempre.
- **Devia:** três estados (GPU ok / GPU ausente / não deu pra saber). No desconhecido, não mostrar o diálogo categórico e não gravar o dismissed; e o texto dizer a evidência ("torch.cuda.is_available() respondeu False" vs "não consegui importar o torch: <erro>").
- `app/main.py:271-274` (e `app/deps_check.py:78-79`)

### 5.11 MÉDIA — NVENC recusado: os cortes vão pra CPU e nenhuma tela conta
- **Quando:** driver NVIDIA antigo/ausente ou GPU sem chip de encode.
- **Hoje:** `print(f"[CorteCenas] NVENC {mode}", flush=True)` — e o motivo exato do ffmpeg fica na linha de cima, também só no log. Nenhuma tela do app (web ou clássica) menciona NVENC ou libx264. Tem um segundo rebaixamento invisível quando o NVENC cai no meio do corte. E o número de workers muda junto, então a diferença de tempo é grande.
- **Devia:** mandar o estado junto do progresso da etapa de corte ("Cortando via CPU (libx264) — o NVENC foi recusado: <motivo>. Atualizar o driver NVIDIA costuma deixar essa etapa várias vezes mais rápida") e um selo permanente na seção "Sobre" do 5.8: "Corte: GPU (NVENC)" / "Corte: CPU".
- `app/ffmpeg_locate.py:85-89`

---

## Avisos que já estão bons (dá pra copiar deles)

- **`app/ui/library_tab.py:948-961`** — arquivo aberto em outro programa: explica o motivo provável, diz "O acervo não foi alterado." e dá `return` antes de tocar no banco. É exatamente o padrão que falta no 3.1.
- **`app/ui/analyze_tab.py:553-593`** — reanálise com três botões, incluindo "Sua curadoria manual (remover/mover/aprovar) é respeitada nas duas opções". Esse texto resolve o 1.4 sem precisar inventar nada.
- **`app/ui/analyze_tab.py:655-676` e `:996+`** — anime não encontrado / refs faltando: explicam o que fazer (3-8 prints por personagem, as fotos valem pros próximos episódios) e oferecem "📂 Abrir pasta de refs" e "🔍 Modo Descoberta". São os melhores textos do projeto — o problema é que só o fallback os vê.
- **`app/pipeline.py:453-470`** — "⚠ Protagonista(s) sem referências utilizáveis… As cenas deles podem contaminar personagens parecidos": diz o efeito, não só o fato.
- **`app/ui/main_window.py:215-247`** — closeEvent: pergunta, diz que os shots já cortados ficam salvos e respeita o "não" com `event.ignore()`.
- **`app/ui/analyze_tab.py:1004-1011`** — "Cancelando — espera a operação atual terminar (um download de modelo pode levar minutos)..." + botão desabilitado. Explica a demora antes de ela virar suspeita de travamento.
- **`app/ui/web/interface.html:1758-1769`** — trocar a pasta de saída pelas Configurações: pergunta antes, com o texto completo. (Só não dispara quando o acervo está vazio.)
- **`app/ui/web/ponte.py:513-538`** — o Juntar da web: não apaga nada e avisa "Vale na próxima análise deste episódio". Escopo temporal explícito, que é o que falta em quase todo o resto.
- **`app/ui/discovery_dialog.py:125-131`** — "o que você vê é exatamente o que vai pro banco". A régua certa; a versão web quebrou ela (1.14).