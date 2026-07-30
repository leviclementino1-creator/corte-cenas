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
| Menu do botão direito | menu do Chromium desligado; o nosso chama `Ponte.acao_cena` |
| Atalhos J / M / Del / Ctrl+P / setas | mesmo portão dos botões; as setas andam na grade |
| Arrastar o episódio | tratado no Qt (o File do Chromium não tem caminho) |
| "Selecionar..." | `QFileDialog` nativo, chamado do Python |
| Elenco (aba Resultados) | clicar troca a grade e o cabeçalho |
| Trocar de aba / abrir Configurações | funciona |
| Árvore do ACERVO | montada do banco por `Ponte.acervo`; clicar troca o episódio, e a pasta que sumiu aparece em itálico apagado |
| Filtro por personagem | as pílulas filtram a grade de verdade (331 → 91 no teste) |
| Ordenação | cronológica / duvidosas / mais longas |
| Atualizar lista | remonta a árvore |
| Juntar / desfazer junção | `Ponte.acao_cena` → `db.add_shot_merge` / `remove_shot_merge` |
| Remover desta pasta | bloqueia no banco + `refresh_shot_links`; o clipe mestre fica em `shots/` |
| Mover pra outro personagem | pergunta o destino na página e regrava os hardlinks |
| Menu de contexto da árvore | apagar episódio/temporada/anime, com a pasta indo pra `Output/_lixeira` |
| Abrir no Explorer | `Ponte.abrir_pasta` |
| Progresso da análise | as 10 etapas vêm de `pipeline_types.STAGES`; `Ponte.progresso` tem a MESMA assinatura do `PipelineWorker.stage` |

Botão do painel, item do menu e atalho de teclado entram todos pelo MESMO
portão (`pedeAcao` → `Ponte.acao_cena`). Duas portas pra mesma ação é como
uma delas fica quebrada sem ninguém perceber — foi o que aconteceu na versão
Qt, onde o menu emitia um sinal que ninguém ouvia.

## Ainda é casca (desenho sem ação)

- **Rodar a análise de verdade.** O progresso está todo ligado, mas o botão
  dispara um ENSAIO (`Ponte.ensaiar`) que percorre as etapas reais numa
  thread de fundo sem tocar em vídeo, banco ou pasta. Ligar o pipeline é
  trocar o ensaio por `worker.stage.connect(self.progresso)` — a assinatura
  já é a mesma de propósito. Falta ainda o que vem junto: refs faltando,
  anime não encontrado (oferta do Modo Descoberta) e a tela de batismo.
- **Ações da aba Resultados** — sincronizar, abrir pasta, exportar refs,
  reforçar refs, exportar vertical: todos parados. `_sync_from_disk` é o
  mais trabalhoso: está preso ao widget (pergunta com `QMessageBox` quando
  uma pasta inteira sumiu) e precisa sair de lá antes.
- **Campos da aba Analisar** — arquivo e saída são reais, o resto é texto
  fixo: os giros de Temporada/Ep não giram e o OP/ED não é editável.
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
