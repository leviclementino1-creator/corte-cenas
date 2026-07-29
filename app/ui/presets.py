"""Os três modos de reconhecimento, num lugar só.

Ficavam dentro da aba Analisar, que é quem os desenhava. Agora quem desenha
é o diálogo de Configurações — e a aba ainda lê os valores pra rodar. Um
módulo sem dependências evita que uma tela precise importar a outra só pra
saber quanto vale "Muito Fiel".
"""
from __future__ import annotations

PRESETS = {
    "strict": {
        "label": "Muito Fiel",
        "tooltip": "Menos falsos positivos. Pode perder cenas rápidas ou ambíguas.",
        "threshold": 0.86, "margin": 0.05, "min_shots": 8,
        "padding": 0.25, "credit": 0.50,
    },
    "auto": {
        "label": "Auto (recomendado)",
        "tooltip": "Bom equilíbrio entre captura e precisão. Começa aqui.",
        "threshold": 0.80, "margin": 0.03, "min_shots": 3,
        "padding": 0.25, "credit": 0.55,
    },
    "loose": {
        "label": "Pouco Fiel",
        "tooltip": "Captura mais cenas. Aceita mais erros pra não perder nada.",
        "threshold": 0.74, "margin": 0.02, "min_shots": 2,
        "padding": 0.30, "credit": 0.70,
    },
}

CHAVES = ("threshold", "margin", "min_shots", "padding", "credit")
