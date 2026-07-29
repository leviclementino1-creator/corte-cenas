"""O sistema visual do app, num lugar só.

Antes disto a aparência vinha de trinta linhas de QSS escritas na v0.1 pra
"não ficar branco", mais uma dúzia de `setStyleSheet` com cores soltas
espalhadas pelas telas — cada uma com o seu cinza. O resultado era um app
que funciona muito bem e parece um protótipo.

Aqui as decisões ficam explícitas e com significado:

- CIANO (ACCENT) = selecionado, ativo, acontecendo agora. É o verde-água
  dos monitores de forma de onda; num app de vídeo ele lê como "isto está
  ligado" sem precisar de legenda.
- ÂMBAR (TIME) = tempo e quantidade: duração, timecode, contagem de cenas.
  Ter uma cor SÓ pra isso é o que faz a grade ser lida de relance.
- VERDE (OK) e VERMELHO (DANGER) ficam reservados pra estado real (GPU
  ativa, erro) — nunca de enfeite, senão perdem o sentido.

Números sempre em monoespaçada com dígitos de largura fixa: é o que impede
as colunas de duração e confiança de dançarem a cada item.
"""
from __future__ import annotations

# ---------- cores ----------
BG = "#0f1115"          # fundo da janela
SURFACE = "#161a21"     # painéis, barras laterais
SURFACE_2 = "#1d222b"   # cartões, campos, botões
SURFACE_3 = "#252c37"   # hover
LINE = "#272d38"
LINE_SOFT = "#1f242d"

TXT = "#e7ecf3"
TXT_DIM = "#98a2b3"
TXT_FAINT = "#6b7484"

ACCENT = "#4cc9c0"
ACCENT_DARK = "#2a6f68"
ACCENT_INK = "#0c2b29"
TIME = "#e8a15c"
OK = "#6fcf8b"
DANGER = "#e5686f"

# ---------- fontes ----------
MONO = '"Cascadia Mono","Cascadia Code",Consolas,ui-monospace,monospace'
SANS = '"Segoe UI Variable Text","Segoe UI","system-ui",sans-serif'
DISP = '"Segoe UI Variable Display","Segoe UI Semibold","Segoe UI",sans-serif'


def label(kind: str) -> str:
    """Estilos de texto avulso, pra não repetir cor solta nas telas.

    kind: title | subtitle | dim | faint | mono | eyebrow | time | ok |
          warn | danger
    """
    estilos = {
        "title": f"font-family:{DISP};font-size:16px;font-weight:620;color:{TXT};",
        "subtitle": f"font-size:13px;color:{TXT_DIM};",
        "dim": f"color:{TXT_DIM};",
        "faint": f"color:{TXT_FAINT};font-size:12px;",
        "mono": f"font-family:{MONO};font-size:12px;color:{TXT_DIM};",
        "eyebrow": (
            f"font-family:{MONO};font-size:10px;letter-spacing:1.6px;"
            f"color:{TXT_FAINT};"
        ),
        "time": f"font-family:{MONO};font-size:12px;color:{TIME};",
        "ok": f"color:{OK};font-weight:600;",
        "warn": f"color:{TIME};font-weight:600;",
        "danger": f"color:{DANGER};font-weight:600;",
    }
    return estilos.get(kind, estilos["dim"])


def chip(tone: str = "neutral") -> str:
    """Selo arredondado (GPU, contagem, estado)."""
    cores = {
        "neutral": (SURFACE_2, LINE, TXT_DIM),
        "accent": (ACCENT_INK, ACCENT_DARK, ACCENT),
        "ok": ("#10201a", "#1f4a30", OK),
        "time": ("#1e1810", "#4a3a24", TIME),
        "danger": ("#201114", "#4a2429", DANGER),
    }
    bg, br, fg = cores.get(tone, cores["neutral"])
    return (
        f"QLabel{{background:{bg};border:1px solid {br};color:{fg};"
        f"border-radius:999px;padding:4px 11px;font-family:{MONO};"
        f"font-size:11px;font-weight:600;}}"
    )


def button(tone: str = "normal") -> str:
    """normal | primary | ghost | danger"""
    if tone == "primary":
        return (
            f"QPushButton{{background:{ACCENT};color:#06231f;border:none;"
            f"border-radius:6px;padding:9px 16px;font-size:13px;font-weight:650;}}"
            f"QPushButton:hover{{background:#5fd8cf;}}"
            f"QPushButton:pressed{{background:#3fb3ab;}}"
            f"QPushButton:disabled{{background:{SURFACE_2};color:{TXT_FAINT};}}"
        )
    if tone == "ghost":
        return (
            f"QPushButton{{background:transparent;color:{TXT_DIM};border:none;"
            f"border-radius:6px;padding:6px 10px;font-size:12.5px;text-align:left;}}"
            f"QPushButton:hover{{background:{SURFACE_2};color:{TXT};}}"
        )
    if tone == "danger":
        return (
            f"QPushButton{{background:#201517;color:{DANGER};"
            f"border:1px solid #4a2429;border-radius:6px;padding:7px 13px;"
            f"font-size:12.5px;}}"
            f"QPushButton:hover{{background:#2b1a1d;}}"
        )
    return (
        f"QPushButton{{background:{SURFACE_2};color:{TXT_DIM};"
        f"border:1px solid {LINE};border-radius:6px;padding:7px 13px;"
        f"font-size:12.5px;}}"
        f"QPushButton:hover{{background:{SURFACE_3};color:{TXT};"
        f"border-color:{ACCENT_DARK};}}"
        f"QPushButton:disabled{{color:{TXT_FAINT};border-color:{LINE_SOFT};"
        f"background:{SURFACE};}}"
    )


QSS = f"""
QMainWindow, QWidget, QDialog {{
    background: {BG};
    color: {TXT};
    font-family: {SANS};
    font-size: 13px;
}}

/* ---- abas ---- */
QTabWidget::pane {{ border: none; background: {BG}; }}
QTabBar {{ background: #0c0e12; }}
QTabBar::tab {{
    background: transparent; color: {TXT_FAINT};
    padding: 10px 18px; margin: 0; border: none;
    font-size: 13px; font-weight: 500;
}}
QTabBar::tab:hover {{ color: {TXT_DIM}; }}
QTabBar::tab:selected {{
    color: {TXT};
    border-bottom: 2px solid {ACCENT};
}}

/* ---- agrupadores ---- */
QGroupBox {{
    border: 1px solid {LINE}; border-radius: 8px;
    margin-top: 14px; padding: 14px 12px 12px;
    background: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 7px;
    color: {TXT_FAINT}; font-family: {MONO}; font-size: 10px;
    letter-spacing: 1.6px;
}}

/* ---- campos ---- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE_2}; color: {TXT};
    border: 1px solid {LINE}; border-radius: 6px;
    padding: 6px 9px; selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {ACCENT_DARK};
}}
QLineEdit:disabled, QComboBox:disabled {{ color: {TXT_FAINT}; background: {SURFACE}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; color: {TXT}; border: 1px solid {LINE};
    selection-background-color: {ACCENT_INK}; selection-color: {ACCENT};
    outline: none;
}}

/* ---- botões (padrão; os de destaque usam theme.button) ---- */
QPushButton {{
    background: {SURFACE_2}; color: {TXT_DIM};
    border: 1px solid {LINE}; border-radius: 6px;
    padding: 7px 13px; font-size: 12.5px;
}}
QPushButton:hover {{ background: {SURFACE_3}; color: {TXT}; border-color: {ACCENT_DARK}; }}
QPushButton:pressed {{ background: {SURFACE}; }}
QPushButton:disabled {{ color: {TXT_FAINT}; border-color: {LINE_SOFT}; background: {SURFACE}; }}

/* ---- listas e árvores ---- */
QListWidget, QTreeWidget, QTableWidget {{
    background: {SURFACE}; color: {TXT};
    border: 1px solid {LINE}; border-radius: 8px;
    outline: none;
    /* sem isto, o azul-padrão do sistema vaza nas áreas que o QSS não
       cobre (ramo da árvore, margem do item) */
    selection-background-color: {ACCENT_INK};
    selection-color: {ACCENT};
}}
QListWidget::item, QTreeWidget::item {{
    padding: 5px 7px; border-radius: 5px; color: {TXT_DIM};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {SURFACE_2}; color: {TXT};
}}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {ACCENT_INK}; color: {ACCENT};
}}
/* A coluna do "▸" herdava o fundo da seleção e virava um bloco ciano
   saturado grudado na borda — mais berrante que o item selecionado. O Qt
   pinta o ramo pela CLASSE BASE (QTreeView), então estilizar só o
   QTreeWidget não resolvia. */
QTreeView::branch, QTreeWidget::branch,
QTreeView::branch:selected, QTreeWidget::branch:selected,
QTreeView::branch:hover, QTreeWidget::branch:hover,
QTreeView::branch:has-children, QTreeView::branch:has-siblings {{
    background: transparent; border: none;
}}
QHeaderView::section {{
    background: {SURFACE_2}; color: {TXT_FAINT}; border: none;
    border-bottom: 1px solid {LINE}; padding: 6px;
    font-family: {MONO}; font-size: 10px; letter-spacing: 1.2px;
}}

/* ---- caixas e opções ---- */
QCheckBox, QRadioButton {{ color: {TXT_DIM}; spacing: 8px; padding: 2px 0; }}
QCheckBox:hover, QRadioButton:hover {{ color: {TXT}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {LINE}; background: {SURFACE_2};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
    image: none;
}}
QRadioButton::indicator:checked {{
    background: {ACCENT}; border: 4px solid {SURFACE_2};
}}

/* ---- progresso ---- */
QProgressBar {{
    background: {SURFACE_2}; border: 1px solid {LINE};
    border-radius: 6px; height: 8px; text-align: center;
    color: {TXT_DIM}; font-family: {MONO}; font-size: 11px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}

/* ---- separadores e divisórias ---- */
QSplitter::handle {{ background: {LINE_SOFT}; }}
QSplitter::handle:hover {{ background: {ACCENT_DARK}; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {LINE}; }}

/* ---- barras de rolagem ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {SURFACE_3}; border-radius: 5px; min-height: 28px; min-width: 28px;
}}
QScrollBar::handle:hover {{ background: #333c4a; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- menus ---- */
QMenu {{
    background: {SURFACE_2}; color: {TXT};
    border: 1px solid {LINE}; border-radius: 8px; padding: 5px;
}}
QMenu::item {{ padding: 7px 16px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_INK}; color: {ACCENT}; }}
QMenu::item:disabled {{ color: {TXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}

/* ---- dicas ---- */
QToolTip {{
    background: {SURFACE_2}; color: {TXT};
    border: 1px solid {LINE}; border-radius: 6px; padding: 7px 9px;
}}

/* ---- diálogos ---- */
QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TXT}; }}
"""
