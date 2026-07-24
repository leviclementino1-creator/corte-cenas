# Resultados de benchmark — memória institucional

## Baseline v0.4.7 — Mushoku S03E02 (23/07/2026)

Pipeline completo vs gabarito (293 atribuições validadas):

| Personagem | Prec | Rec | F1 |
|---|---|---|---|
| Shirone, Zanoba | 1.00 | 1.00 | 1.00 |
| Farion, Nina | 0.94 | 0.91 | 0.93 |
| Greyrat, Eris Boreas | 0.87 | 0.97 | 0.92 |
| Greyrat, Paul | 0.88 | 0.82 | 0.85 |
| Nanahoshi, Shizuka | 0.65 | 0.72 | 0.68 |
| Superdia, Ruijerd (2 refs!) | 0.46 | 0.69 | 0.55 |
| **MACRO F1** | | | **0.82** |

- Higiene do banco (seed ≥0.85): tabela idêntica — sem custo, proteção
  profilática pros casos tipo Granbell (bola de neve de cenas escuras).
- Alvo prioritário: Ruijerd (refs fracas = ímã de FP, como previsto).

## A/B CLIP × CCIP — discriminação por rosto (23/07/2026)

104 rostos rotulados (cenas de 1 pessoa do gabarito), sim = max sobre refs:

| | CLIP ViT-L/14 | CCIP (deepghs) |
|---|---|---|
| Acerto top-1 | **0.96** | 0.94 |
| Margem média top1−top2 | 0.077 | **0.329 (4.3x)** |
| Custo | GPU, ~s/episódio | CPU ONNX, ~4 rostos/s |

Leitura: empate no acerto NAS CENAS FÁCEIS (e com vantagem circular pro
CLIP — os rótulos nasceram de decisões dele). A diferença brutal é a
FOLGA: o CLIP decide raspando (0.077 — qualquer ruído flipa, vide o caso
threshold 0.84), o CCIP decide com 4x mais separação entre o certo e o
segundo colocado.

**Decisão (v0.4.8): híbrido.** CLIP segue como motor rápido; CCIP entra
como SEGUNDA OPINIÃO local nos pontos frágeis — duvidosos, âncoras de
presença, decisões por grupo — onde folga importa mais que velocidade, e
sem gastar API de IA. Requer empacotar onnxruntime + modelo CCIP (~200MB)
no instalador.

## v0.4.8 — Híbrido CLIP+CCIP no pipeline (23/07/2026)

Knobs calibrados nos 104 rostos rotulados (crop pad 0.55, extractor
vendorizado idêntico ao imgutils, cos=1.000000):

- sim personagem-certo: mediana 0.89 (p25 0.85) × impostor: mediana 0.53
  (p95 0.81) → gap>=0.15 = zero erros de resgate nos 104;
- top-1 CCIP no pad 0.55: 0.952 (melhor que 0.942 no pad 0.25).

Juiz A/B (mesmo processo, flag on/off): **tabela IDÊNTICA, MACRO F1 0.82 =
0.82** — o híbrido não regrediu NADA e custou +6s frio / +0s quente.
Lição de projeto: a 1ª versão do veto (só ranking) derrubou um Paul
verdadeiro decidindo na zona de sims baixas — veto agora exige
reconhecimento POSITIVO do outro (sim >= 0.80), não só "ranquear melhor".

Slime S04E15 (cenário anti-fantasma): todos os testes passaram; o grupo
fantasma de 17 rostos "Gale" foi RECUSADO pelo CCIP (vai pro batismo), e
5/27 cenas duvidosas foram resolvidas localmente sem IA (Diablo 0.90,
Rimuru 0.86...) em 9s de CPU.

**Achado de auditoria (o CCIP pagou a entrada): o gabarito do Mushoku está
SUBROTULADO no "Ruijerd".** Os keyframes #0132/#0153 mostram o homem de
cabelo azul (que o próprio gabarito valida como Ruijerd nas cenas solo)
presente em cenas onde o gabarito só lista Nina/Eris/Zanoba — ou seja,
parte dos 13 "FP" do Ruijerd são acertos não listados; o teto real dele é
maior que 0.55 e NÃO é alcançável por código. Bônus: pelo contexto visual
(dojo do Deus Espada, Nina Farion ao lado), esse personagem provavelmente
é o **Gall Falion (Deus Espada)**, ausente do elenco baixado — as refs
`auto_disc` do Ruijerd são desse homem. Corrigir é curadoria (re-rotular e
regenerar o gabarito), decisão do dono do app.

## Como reproduzir

```
python benchmarks/run_bench.py golden/mushoku_s03e02.json
```

Gabaritos novos: `make_golden.py` (do banco ou de um shots.json). Regra da
casa: NENHUMA mudança de matching entra em release sem passar pelo juiz.
