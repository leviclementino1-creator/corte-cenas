"""O sistema visual do app, num lugar só.

Base: "Mesa de corte, não painel de app" — a tela é uma bancada, a grade de
miniaturas é a única coisa que brilha, o resto recua. Sem sombra, sem
animação, sem gradiente: nada disso existe em QSS, então a hierarquia é
feita por VALOR (o quanto uma superfície é mais clara) e por BORDA.

As três regras que sustentam tudo:

- CIANO (ACCENT) = onde você está. Selecionado, ativo, etapa rodando, ação
  principal. Nunca decoração, e nunca dois focos na mesma tela.
- ÂMBAR (TIME) = número. Duração, timecode, contagem, ETA, valor de campo
  numérico. Nenhum outro elemento pode usar — é ter uma cor SÓ pra isso que
  faz a grade ser lida sem ler.
- VERDE (OK) e VERMELHO (DANGER) = estado real (GPU ativa, erro, destrutivo).
  Gastar em enfeite é perder o sentido.

Duas armadilhas do Qt que estão embutidas aqui, com o porquê:

- `border-radius: 999px` é idioma de navegador. O navegador clampa em metade
  da altura; o Qt NÃO clampa — a borda degenera e a cápsula vira retângulo.
  Todo raio de pílula aqui é metade da altura, escrito à mão.
- cor de fundo em `QWidget` pinta TUDO (etiqueta, caixa de marcar e os
  widgets-embrulho de linha de formulário), tapando o painel de baixo com a
  cor da janela. Só a janela e o diálogo pintam fundo.
"""
from __future__ import annotations

# ---------- superfícies ----------
BG = "#0e1014"          # fundo da janela e da área de grade
SURFACE = "#14181f"     # painéis, barra de abas, cabeçalho de diálogo
SURFACE_2 = "#1b2129"   # cartão, botão secundário, menu — o que se clica
SURFACE_3 = "#232b35"   # SÓ hover; nada nasce nesta cor
WELL = "#14181f"        # fundo de campo: campo é poço, painel é chão
WELL_OFF = "#0f1217"    # campo/etapa desabilitados
PRESSED = "#161b22"     # botão comum apertado

LINE = "#2a323d"        # borda de controle (sobrevive a 150% de escala)
LINE_SOFT = "#1e242c"   # divisor interno, borda de cartão em repouso
LINE_BRIGHT = "#37414e" # borda de menu/diálogo e de controle em hover

# ---------- texto ----------
TXT = "#e6ebf2"
TXT_DIM = "#9aa4b2"
TXT_FAINT = "#6a7382"
TXT_OFF = "#4b5462"     # desabilitado — existe porque o Qt não tem opacity
TXT_GHOST = "#5b6473"   # placeholder, "sem identificação"

# ---------- destaque e estado ----------
ACCENT = "#4cc9c0"
ACCENT_HOVER = "#5fd6cd"
ACCENT_DIM = "#35938c"    # borda de superfície de destaque / principal apertado
ACCENT_LIGHT = "#7ddcd5"  # texto sobre fundo de destaque
ACCENT_INK = "#0c2b29"    # fundo de item selecionado
ACCENT_INK_2 = "#0a2321"  # vazado apertado
ON_ACCENT = "#08201e"     # texto sobre ciano chapado
ACCENT_DARK = ACCENT_DIM  # nome antigo, ainda usado em telas

TIME = "#e8a15c"
TIME_INK = "#20190f"
TIME_LINE = "#5c4a2a"

OK = "#6fcf8b"
OK_INK = "#132019"
OK_LINE = "#2f5c3d"

DANGER = "#e5686f"
DANGER_INK = "#1f1418"
DANGER_INK_2 = "#2b171c"
DANGER_LINE = "#5b2a30"
DANGER_LINE_2 = "#8c3b43"
DANGER_LIGHT = "#f08b91"
ON_DANGER = "#1f0a0d"

# ---------- fontes ----------
MONO = '"Cascadia Mono","Cascadia Code",Consolas,ui-monospace,monospace'
SANS = '"Segoe UI Variable Text","Segoe UI","system-ui",sans-serif'
DISP = '"Segoe UI Variable Display","Segoe UI Semibold","Segoe UI",sans-serif'

# ---------- medidas ----------
# Espaçamento em passo de 4: 4 dentro de um controle · 8 entre irmãos ·
# 12 grade de cartões · 16 padding de painel · 24 entre blocos · 32 entre
# seções numeradas.
R_XS, R_S, R_M, R_L = 3, 4, 6, 8     # marcar · campo/botão/aba · cartão · menu
H_ROW, H_CTRL, H_PILL, H_TAB, H_PRIMARY = 30, 32, 34, 36, 38
W_ACERVO, W_CENA = 248, 320          # colunas fixas da Biblioteca
CARD_MIN, CARD_MAX = 196, 300        # o cartão é o único elástico
CARD_COLS_MAX = 5                    # teto de colunas da grade


def label(kind: str) -> str:
    """Estilos de texto avulso, pra não repetir cor solta nas telas.

    kind: title | section | subtitle | dim | faint | mono | eyebrow | time |
          ok | warn | danger
    """
    estilos = {
        "title": f"font-family:{DISP};font-size:22px;font-weight:600;color:{TXT};",
        "section": f"font-family:{DISP};font-size:17px;font-weight:600;color:{TXT};",
        "subtitle": f"font-size:13px;color:{TXT_DIM};",
        "dim": f"font-size:13px;color:{TXT_DIM};",
        "faint": f"font-size:12px;color:{TXT_FAINT};",
        "mono": f"font-family:{MONO};font-size:13px;color:{TXT_DIM};",
        # Rótulo de grupo: 11/600 com letra espaçada — é etiqueta, não título.
        "eyebrow": (
            f"font-family:{MONO};font-size:11px;font-weight:600;"
            f"letter-spacing:1.2px;color:{TXT_FAINT};"
        ),
        "time": f"font-family:{MONO};font-size:13px;color:{TIME};",
        "ok": f"color:{OK};font-weight:600;",
        "warn": f"color:{TIME};font-weight:600;",
        "danger": f"color:{DANGER};font-weight:600;",
    }
    return estilos.get(kind, estilos["dim"])


def chip(tone: str = "neutral") -> str:
    """Selo h26 — raio 13, metade da altura, escrito à mão (ver o cabeçalho
    sobre o 999px). Use com `chip_dot()` no texto: o ponto pintado substitui
    o emoji, que não aceita cor de token e fica sujo a 150% de escala."""
    cores = {
        "neutral": (SURFACE_2, LINE, TXT_DIM),
        "accent": (ACCENT_INK, ACCENT_DIM, ACCENT),
        "ok": (OK_INK, OK_LINE, OK),
        "time": (TIME_INK, TIME_LINE, TIME),
        "danger": (DANGER_INK, DANGER_LINE, DANGER),
    }
    bg, br, fg = cores.get(tone, cores["neutral"])
    return (
        f"QLabel{{background:{bg};border:1px solid {br};color:{fg};"
        f"border-radius:13px;padding:0 12px;min-height:24px;max-height:24px;"
        f"font-family:{MONO};font-size:11px;font-weight:600;}}"
    )


_DOT = {"ok": OK, "time": TIME, "accent": ACCENT, "danger": DANGER, "neutral": TXT_FAINT}


def chip_dot(tone: str, texto: str) -> str:
    """Texto do selo com o ponto de estado na frente, em rich text."""
    return (
        f"<span style='color:{_DOT.get(tone, TXT_FAINT)};font-size:15px'>●</span>"
        f"&nbsp;&nbsp;{texto}"
    )


def button(tone: str = "normal") -> str:
    """normal | primary | accent-outline | ghost | danger

    Foco de teclado = borda de 2px SUBSTITUINDO a de 1px (o padding cai 1px
    pra o texto não pular). Nunca `outline`: o Qt desenha fora do widget e o
    corte do pai come o traço.
    """
    if tone == "primary":
        return (
            f"QPushButton{{background:{ACCENT};color:{ON_ACCENT};"
            f"border:1px solid {ACCENT};border-radius:{R_S}px;"
            f"min-height:{H_PRIMARY - 2}px;padding:0 16px;"
            f"font-size:14px;font-weight:600;}}"
            f"QPushButton:hover{{background:{ACCENT_HOVER};border-color:{ACCENT_HOVER};}}"
            f"QPushButton:pressed{{background:{ACCENT_DIM};border-color:{ACCENT_DIM};}}"
            f"QPushButton:focus{{border:2px solid {TXT};padding:0 15px;}}"
            f"QPushButton:disabled{{background:{SURFACE_2};border-color:{SURFACE_3};"
            f"color:{TXT_OFF};}}"
        )
    if tone == "accent-outline":
        # Irmã da ação principal, não rival dela: mesmo destaque, vazada.
        return (
            f"QPushButton{{background:{BG};color:{ACCENT};"
            f"border:1px solid {ACCENT_DIM};border-radius:{R_S}px;"
            f"min-height:{H_PRIMARY - 2}px;padding:0 16px;"
            f"font-size:14px;font-weight:600;}}"
            f"QPushButton:hover{{background:{ACCENT_INK};border-color:{ACCENT};"
            f"color:{ACCENT_LIGHT};}}"
            f"QPushButton:pressed{{background:{ACCENT_INK_2};border-color:{ACCENT_DIM};"
            f"color:{ACCENT};}}"
            f"QPushButton:focus{{background:{ACCENT_INK};border:2px solid {ACCENT};"
            f"padding:0 15px;color:{ACCENT_LIGHT};}}"
            f"QPushButton:disabled{{background:{BG};border-color:{LINE_SOFT};"
            f"color:{TXT_OFF};}}"
        )
    if tone == "ghost":
        # Lista de ações (barra lateral): em repouso é só texto; o fundo
        # aparece no hover. Alinhado à esquerda, como um item de lista.
        return (
            f"QPushButton{{background:transparent;color:{TXT_DIM};"
            f"border:1px solid transparent;border-radius:{R_S}px;"
            f"min-height:{H_CTRL - 2}px;padding:0 10px;"
            f"font-size:13px;text-align:left;}}"
            f"QPushButton:hover{{background:{SURFACE_2};border-color:{SURFACE_3};"
            f"color:{TXT};}}"
            f"QPushButton:pressed{{background:{SURFACE};border-color:{SURFACE_3};}}"
            f"QPushButton:focus{{background:{SURFACE};border:2px solid {ACCENT};"
            f"padding:0 9px;color:{TXT};}}"
            f"QPushButton:disabled{{background:transparent;border-color:transparent;"
            f"color:{TXT_OFF};}}"
        )
    if tone == "alto":
        # Secundário da MESMA altura da ação principal: é uma variação dela
        # (analisar com IA), não um destaque concorrente. Cinza de propósito
        # — duas bordas cianas lado a lado fazem o olho escolher no par ou
        # ímpar.
        return (
            f"QPushButton{{background:{SURFACE_2};color:{TXT};"
            f"border:1px solid {LINE};border-radius:{R_S}px;"
            f"min-height:{H_PRIMARY - 2}px;padding:0 16px;"
            f"font-size:14px;font-weight:600;}}"
            f"QPushButton:hover{{background:{SURFACE_3};border-color:{LINE_BRIGHT};}}"
            f"QPushButton:pressed{{background:{PRESSED};color:{TXT_DIM};}}"
            f"QPushButton:focus{{border:2px solid {ACCENT};padding:0 15px;}}"
            f"QPushButton:disabled{{background:{PRESSED};border-color:{LINE_SOFT};"
            f"color:{TXT_OFF};}}"
        )
    if tone in ("linha", "linha-danger"):
        # Ação secundária do painel da cena: lê como LINHA DE MENU — ícone à
        # esquerda, texto alinhado, altura menor. Centralizado, ele competia
        # com a ação principal logo acima.
        perigo = tone.endswith("danger")
        return (
            f"QPushButton{{background:{DANGER_INK if perigo else SURFACE_2};"
            f"color:{DANGER if perigo else TXT};"
            f"border:1px solid {DANGER_LINE if perigo else LINE};"
            f"border-radius:{R_S}px;min-height:{H_CTRL - 2}px;"
            f"padding:0 12px;font-size:13px;font-weight:500;text-align:left;}}"
            f"QPushButton:hover{{background:{DANGER_INK_2 if perigo else SURFACE_3};"
            f"border-color:{DANGER_LINE_2 if perigo else LINE_BRIGHT};"
            f"color:{DANGER_LIGHT if perigo else TXT};}}"
            f"QPushButton:pressed{{background:{DANGER if perigo else PRESSED};"
            f"color:{ON_DANGER if perigo else TXT_DIM};}}"
            f"QPushButton:disabled{{background:{PRESSED};border-color:{LINE_SOFT};"
            f"color:{TXT_OFF};}}"
        )
    if tone == "danger":
        # Nasce apagado (fundo escuro, texto vermelho) e só fica CHAPADO ao
        # ser apertado: destrutivo não deve gritar antes da hora.
        return (
            f"QPushButton{{background:{DANGER_INK};color:{DANGER};"
            f"border:1px solid {DANGER_LINE};border-radius:{R_S}px;"
            f"min-height:{H_CTRL - 2}px;padding:0 16px;"
            f"font-size:14px;font-weight:600;}}"
            f"QPushButton:hover{{background:{DANGER_INK_2};border-color:{DANGER_LINE_2};"
            f"color:{DANGER_LIGHT};}}"
            f"QPushButton:pressed{{background:{DANGER};border-color:{DANGER};"
            f"color:{ON_DANGER};}}"
            f"QPushButton:focus{{background:{DANGER_INK_2};border:2px solid {DANGER};"
            f"padding:0 15px;color:{DANGER_LIGHT};}}"
            f"QPushButton:disabled{{background:{PRESSED};border-color:{LINE_SOFT};"
            f"color:{TXT_OFF};}}"
        )
    return (
        f"QPushButton{{background:{SURFACE_2};color:{TXT};"
        f"border:1px solid {LINE};border-radius:{R_S}px;"
        f"min-height:{H_CTRL - 2}px;padding:0 16px;font-size:14px;font-weight:600;}}"
        f"QPushButton:hover{{background:{SURFACE_3};border-color:{LINE_BRIGHT};}}"
        f"QPushButton:pressed{{background:{PRESSED};color:{TXT_DIM};}}"
        f"QPushButton:focus{{border:2px solid {ACCENT};padding:0 15px;}}"
        f"QPushButton:disabled{{background:{PRESSED};border-color:{LINE_SOFT};"
        f"color:{TXT_OFF};}}"
    )


def pill(selected: bool = False) -> str:
    """Pílula de filtro h34 / raio 17 (metade da altura)."""
    if selected:
        return (
            f"background:{ACCENT_INK};border:1px solid {ACCENT};"
            f"border-radius:17px;color:{ACCENT};"
        )
    return (
        f"background:{SURFACE_2};border:1px solid {LINE};"
        f"border-radius:17px;color:{TXT_DIM};"
    )


QSS = f"""
/* Só a janela e o diálogo pintam fundo — ver a armadilha do QWidget no
   cabeçalho deste arquivo. */
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{
    color: {TXT};
    font-family: {SANS};
    font-size: 13px;
}}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}

/* ---- abas ---- */
/* A aba ativa se FUNDE com o conteúdo: mesmo fundo, borda de baixo apagada
   e um traço ciano de 2px no topo. Sem sublinhado por baixo — o QTabBar não
   deixa desenhar sob o conteúdo de forma confiável. */
/* Barra de 44 no total: 8 de respiro em cima + aba de 36 encostada embaixo.
   Sem esses 8, a aba ativa encosta no topo da janela e a barra deixa de ter
   linha de base. O recuo de 12 é o mesmo padding lateral do conteúdo. */
QTabWidget::pane {{ border: none; background: {BG}; }}
QTabWidget::tab-bar {{ left: 12px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {TXT_DIM};
    min-height: {H_TAB - 2}px; max-height: {H_TAB - 2}px;
    padding: 0 16px; margin: 8px 1px 0 1px;
    border: 1px solid transparent;
    border-top-left-radius: {R_S}px; border-top-right-radius: {R_S}px;
    font-size: 14px; font-weight: 500;
    outline: none;
}}
QTabBar::tab:focus {{ outline: none; }}
QTabBar::tab:hover {{ background: {SURFACE_2}; color: {TXT}; }}
QTabBar::tab:selected {{
    background: {BG}; color: {TXT}; font-weight: 600;
    border: 1px solid {LINE}; border-bottom-color: {BG};
    border-top: 2px solid {ACCENT};
}}

/* ---- agrupadores ---- */
/* O título fica ACIMA do cartão, não montado na borda: encaixado na linha
   ele precisa de um retalho de fundo pra furar a borda, e o retalho nunca
   casa com o painel de trás. O margin-top reserva essa faixa. */
QGroupBox {{
    border: 1px solid {LINE_SOFT}; border-radius: {R_M}px;
    margin-top: 28px; padding: 16px;
    background: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 2px; top: 0px; padding: 0 2px;
    color: {TXT}; font-family: {DISP}; font-size: 17px; font-weight: 600;
    background: transparent;
}}

/* ---- campos ---- */
/* Campo é mais ESCURO que o painel (poço) e botão é mais CLARO (relevo):
   sem sombra, essa é a única pista de "clicável" que sobra. */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {WELL}; color: {TXT};
    border: 1px solid {LINE}; border-radius: {R_S}px;
    padding: 0 10px; min-height: {H_CTRL - 2}px;
    selection-background-color: {ACCENT_DIM}; selection-color: {ON_ACCENT};
}}
QPlainTextEdit, QTextEdit {{ min-height: 60px; padding: 6px 10px; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    background: #171c24; border-color: {LINE_BRIGHT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    background: {BG}; border: 2px solid {ACCENT}; padding: 0 9px;
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled {{
    background: {WELL_OFF}; border-color: {LINE_SOFT}; color: {TXT_OFF};
}}
/* Valor numérico é NÚMERO: mono e âmbar, como toda medida no app. */
QSpinBox, QDoubleSpinBox {{
    font-family: {MONO}; color: {TIME}; padding-right: 2px;
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; color: {TXT}; border: 1px solid {LINE_BRIGHT};
    border-radius: {R_L}px; padding: 4px;
    selection-background-color: {ACCENT_INK}; selection-color: {ACCENT};
    outline: none;
}}
/* BOTÕES DO SPIN: NÃO ESTILIZAR. Já foi tentado duas vezes.
   (1) desenhar o triângulo com o truque de bordas (largura 0 + borda grossa)
       vira um QUADRADO BRANCO — sem `image:` o Qt não tem o que pintar;
   (2) estilizar só a CAIXINHA (fundo/borda) apaga a seta nativa junto: ao
       assumir o subcontrole, o Qt para de desenhar a primitiva dele e o
       resultado são duas caixas vazias.
   Sem um arquivo de imagem pra apontar — e o app é um exe sem recursos
   gráficos —, a seta nativa é a única que existe de verdade. O campo em
   volta continua tematizado pelas regras de QLineEdit/QSpinBox acima. */

/* ---- botões (padrão; os de destaque usam theme.button) ---- */
QPushButton {{
    background: {SURFACE_2}; color: {TXT};
    border: 1px solid {LINE}; border-radius: {R_S}px;
    min-height: {H_CTRL - 2}px; padding: 0 16px;
    font-size: 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {LINE_BRIGHT}; }}
QPushButton:pressed {{ background: {PRESSED}; color: {TXT_DIM}; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; padding: 0 15px; }}
QPushButton:disabled {{
    background: {PRESSED}; border-color: {LINE_SOFT}; color: {TXT_OFF};
}}

/* ---- listas e árvores ---- */
QListWidget, QTreeWidget, QTableWidget {{
    background: {SURFACE}; color: {TXT};
    border: 1px solid {LINE_SOFT}; border-radius: {R_M}px;
    outline: none;
    selection-background-color: {ACCENT_INK};
    selection-color: {ACCENT};
}}
QListWidget::item, QTreeWidget::item {{
    min-height: {H_ROW - 8}px; padding: 4px 10px;
    border: 1px solid transparent; border-radius: {R_S}px;
    color: {TXT_DIM};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {SURFACE_2}; border-color: {SURFACE_3}; color: {TXT};
}}
/* Selecionado leva uma BARRA de 3px à esquerda: sem sombra, é a barra que
   sobrevive — cor de fundo sozinha some contra a superfície vizinha. */
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {ACCENT_INK}; color: {ACCENT};
    border: 1px solid {ACCENT_DIM}; border-left: 3px solid {ACCENT};
}}
/* A área do ramo herdava o fundo da seleção e o Qt desenhava ali as guias da
   hierarquia na cor de destaque — três barras cianas coladas no item. O Qt
   pinta isso pela CLASSE BASE, então estilizar só QTreeWidget não resolve. */
QTreeView::branch, QTreeWidget::branch {{ background: transparent; border: none; }}
QTreeView::branch:has-siblings:!adjoins-item,
QTreeView::branch:has-siblings:adjoins-item,
QTreeView::branch:!has-children:!has-siblings:adjoins-item,
QTreeView::branch:has-children:closed,
QTreeView::branch:has-children:open {{
    border-image: none; image: none; background: transparent;
}}
QTreeView {{ show-decoration-selected: 0; }}
QHeaderView::section {{
    background: {SURFACE_2}; color: {TXT_FAINT}; border: none;
    border-bottom: 1px solid {LINE}; padding: 6px;
    font-family: {MONO}; font-size: 11px; font-weight: 600; letter-spacing: 1.2px;
}}

/* ---- caixas e opções ---- */
QCheckBox, QRadioButton {{ color: {TXT_DIM}; spacing: 10px; padding: 4px 0; }}
QCheckBox:hover, QRadioButton:hover {{ color: {TXT}; }}
QCheckBox:checked, QRadioButton:checked {{ color: {TXT}; font-weight: 600; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {TXT_OFF}; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {LINE_BRIGHT}; background: {WELL};
}}
QCheckBox::indicator {{ border-radius: {R_XS}px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT_DIM}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
/* Anel: miolo cheio com folga. Círculo chapado, pequeno, lê como quadrado. */
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {ACCENT}, stop:0.38 {ACCENT},
        stop:0.42 {ACCENT_INK}, stop:1 {ACCENT_INK});
    border: 2px solid {ACCENT};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {WELL_OFF}; border-color: {LINE_SOFT};
}}

/* ---- progresso ---- */
/* Sem animação: a barra é atualizada por valor e o tempo decorrido em mono
   é o que prova que o app está vivo. Indeterminado = barra vazia + texto,
   nunca um bloco quicando falso. */
QProgressBar {{
    background: {WELL}; border: 1px solid {LINE};
    border-radius: 5px; max-height: 10px; min-height: 10px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

/* ---- separadores e divisórias ---- */
QSplitter::handle {{ background: {LINE_SOFT}; }}
QSplitter::handle:hover {{ background: {ACCENT_DIM}; }}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{ color: {LINE_SOFT}; }}

/* ---- barras de rolagem ---- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {SURFACE_3}; border-radius: 5px; min-height: 32px; min-width: 32px;
}}
QScrollBar::handle:hover {{ background: {LINE_BRIGHT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- menus ---- */
/* Sem sombra, a borda CLARA é o que separa o menu do fundo. */
QMenu {{
    background: {SURFACE_2}; color: {TXT};
    border: 1px solid {LINE_BRIGHT}; border-radius: {R_L}px; padding: 6px;
}}
QMenu::item {{
    padding: 6px 12px; border-radius: {R_S}px; min-height: 18px;
}}
QMenu::item:selected {{ background: {SURFACE_3}; color: {TXT}; }}
QMenu::item:disabled {{ color: {TXT_OFF}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}

/* ---- dicas ---- */
QToolTip {{
    background: {SURFACE_2}; color: {TXT};
    border: 1px solid {LINE_BRIGHT}; border-radius: {R_M}px; padding: 7px 10px;
}}

/* ---- diálogos ---- */
QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TXT}; }}
QDialogButtonBox {{ button-layout: 3; }}
"""
