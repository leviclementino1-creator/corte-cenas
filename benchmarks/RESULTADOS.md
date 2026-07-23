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

## Como reproduzir

```
python benchmarks/run_bench.py golden/mushoku_s03e02.json
```

Gabaritos novos: `make_golden.py` (do banco ou de um shots.json). Regra da
casa: NENHUMA mudança de matching entra em release sem passar pelo juiz.
