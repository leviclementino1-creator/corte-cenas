# O que está ligado e o que ainda é casca

O PoC provou que o caminho funciona. Mas ele foi feito pra **provar**, não
pra usar: a maior parte dos controles é desenho sem fio atrás. Esta lista
existe pra ninguém confundir uma coisa com a outra — inclusive eu.

Anotado em 30/07/2026, depois do Levi apontar que "vários botões não estão
funcionando".

## Ligado de verdade

| O quê | Como |
|---|---|
| Clicar num cartão | seleciona, preenche A CENA e pede a prévia |
| Prévia em loop | `Ponte.previa` transcodifica WebM sob demanda (0,14 s) |
| Prévia de hover | tira de 8 quadros por `cena:/tira/`, o mouse escolhe o quadro |
| Menu do botão direito | menu do Chromium desligado, o nosso aparece e chama `Ponte.menu_cena` |
| Atalhos J / M / Del / Ctrl+P / setas | `Ponte.atalho`, com as setas andando na grade |
| Arrastar o episódio | tratado no Qt (o File do Chromium não tem caminho) |
| "Selecionar..." | `QFileDialog` nativo, chamado do Python |
| Elenco (aba Resultados) | clicar troca a grade e o cabeçalho |
| Trocar de aba / abrir Configurações | funciona |
| **Árvore do ACERVO** | montada do banco por `Ponte.acervo`; clicar num episódio troca a grade, e a pasta que sumiu do disco aparece em itálico apagado |
| **Filtro por personagem** | as pílulas filtram a grade de verdade (331 → 91 no teste) |
| **Ordenação** | cronológica / duvidosas / mais longas |
| Atualizar lista | remonta a árvore |

## Ainda é casca (desenho sem ação)

- **Menu de contexto na árvore** — apagar episódio/temporada/anime não existe
  no PoC (no app é `_apagar_do_acervo`).
- **Botões do painel A CENA** — "Juntar com a próxima" só avisa o Python;
  "Remover desta pasta" e "Mover pra outro personagem" não fazem nada.
- **Itens do menu de contexto** — chegam no Python com o nome da ação e o
  número da cena, e param aí.
- **Ações da aba Resultados** — sincronizar, abrir pasta, exportar refs,
  reforçar refs, exportar vertical: todos parados.
- **Aba Analisar inteira** — os campos são texto fixo (menos o Arquivo), os
  giros de Temporada/Ep não giram, e os três botões de análise não analisam.
  O progresso mostrado é o da maquete, não de uma análise real.
- **Configurações** — radios e caixas não guardam nada; Salvar não salva.

## O que cada um precisa

Quase tudo é religar em `Ponte` o que já existe. Nada de lógica nova:

| Controle | Onde a lógica já mora |
|---|---|
| Árvore do acervo | `library_tab._on_tree_selection`, `_menu_acervo` |
| Filtro por personagem | `library_tab._load_episode` (o filtro sobrevive à recarga) |
| Ordenação | `character_grid.SORT_MODES` |
| Juntar / remover / mover | `library_tab._juntar`, `_handle_shot_action`, `_alvo_da_acao` |
| Apagar do acervo | `_apagar_do_acervo` → `curation.enviar_para_lixeira` |
| Sincronizar pastas | `results_tab._sync_from_disk` |
| Exportar refs | `results_tab._export_refs` |
| Rodar a análise | `ui/worker.py` (é o único que precisa de sinal de volta, pro progresso) |

## Armadilhas que já custaram tempo

**Caminho relativo no banco.** `shot.file` e `shot.keyframe` são relativos à
pasta do episódio (`shots\0002.mp4`). Mandar isso cru pro navegador faz o
pedido falhar em silêncio — foi o que quebrou a prévia de hover. Todo caminho
que sai da `Ponte` tem que ser absoluto.

**Raiz que não acompanha o episódio.** `_raiz_do_episodio` devolvia `None`
quando a pasta não existia, e o chamador só trocava a raiz se viesse algo.
Resultado: abrir um episódio apagado continuava servindo as miniaturas do
último aberto, sem aviso nenhum. Agora sempre devolve o caminho esperado — se
a pasta sumiu, a imagem falta, que é honesto. Quem decide se existe é o `ok`
do `acervo()`, olhando a pasta `shots`.

**Pasta das prévias tem que ser propriedade, não atributo.** Guardada no
`__init__`, as prévias do segundo episódio iam parar na pasta do primeiro.
