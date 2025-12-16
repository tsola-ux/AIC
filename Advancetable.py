# periodic_table_dash.py
import os
import json
import pandas as pd
import dash
from dash import html, dcc, Input, Output, State
from urllib.parse import quote
import logging

# --------------------------
# Helpers
# --------------------------
def to_int_or_none(val):
    try:
        if val is None:
            return None
        s = str(val).strip()
        if s == "":
            return None
        return int(float(s))
    except Exception:
        return None


def _norm_symbol(s):
    # also removes hidden BOM chars
    s = (s or "").replace("\ufeff", "").strip()
    if not s:
        return ""
    s = s.lower()
    return s[0].upper() + s[1:] if len(s) > 1 else s.upper()


def norm(s):
    return (s or "").strip().lower()


# --------------------------
# Load data (Periodic Table)
# --------------------------
CSV_FILE = os.path.join(os.path.dirname(__file__), "elements.csv")
df = pd.read_csv(CSV_FILE, dtype=str).fillna("")

DEF_FILE = os.path.join(os.path.dirname(__file__), "element_definitions.csv")
def_df = (
    pd.read_csv(DEF_FILE, dtype={"atomic_number": int})
    if os.path.exists(DEF_FILE)
    else pd.DataFrame(columns=["atomic_number", "definition"])
)
definitions = {
    str(int(row["atomic_number"])): str(row.get("definition", "") or "")
    for _, row in def_df.iterrows()
}

# CATEGORY COLORS (Unknown removed from legend/filter)
CATEGORY_COLORS = {
    "Alkali metal": "#FFB3BA",
    "Alkaline earth metal": "#FFDFBA",
    "Lanthanide": "#FFFFBA",
    "Actinide": "#BAFFC9",
    "Transition metal": "#BAE1FF",
    "Post-transition metal": "#E2BAFF",
    "Metalloid": "#D3D3D3",
    "Nonmetal": "#BFFCC6",
    "Halogen": "#FFD1DC",
    "Noble gas": "#CBE7FF",
}


def category_color(cat):
    if (cat or "").strip() == "Unknown":
        return "#E0E0E0"
    return CATEGORY_COLORS.get(cat, "#F0F0F0")


MAX_PERIOD = 7
MAX_GROUP = 18

# --------------------------
# Level rules
# --------------------------
def is_unlocked_for_level(level, atno, category=None):
    lvl = level or "Advanced"
    if not atno:
        return False

    if lvl == "Basic":
        return atno <= 20

    if lvl == "Intermediate":
        cat = (category or "").strip()
        if cat in ("Lanthanide", "Actinide"):
            return False
        return atno <= 54  # ✅ Intermediate now first 54 elements only

    return atno <= 118


# --------------------------
# Build element maps (positions + f-block)
# --------------------------
position_map = {}
f_block_elements = []

for _, row in df.iterrows():
    atomic_number = row.get("atomic_number", "") or row.get("atomic no", "") or row.get("Z", "")
    symbol = row.get("symbol", "")
    name = row.get("name", "")
    category = row.get("category", "")
    period_raw = row.get("period", "")
    group_raw = row.get("group", "")
    x_raw = row.get("x", "")
    y_raw = row.get("y", "")

    x = to_int_or_none(x_raw)
    y = to_int_or_none(y_raw)
    period = to_int_or_none(period_raw)
    group = to_int_or_none(group_raw)

    el = {
        "atomic_number": int(float(atomic_number)) if str(atomic_number).strip() != "" else None,
        "symbol": symbol,
        "name": name,
        "category": category,
        "period": period,
        "group": group,
        "x": x,
        "y": y,
        **{
            k: (v if v != "" else None)
            for k, v in row.items()
            if k not in ["atomic_number", "symbol", "name", "category", "period", "group", "x", "y"]
        },
    }

    if x is not None and y is not None and 1 <= x <= MAX_GROUP and 1 <= y <= MAX_PERIOD:
        position_map[(y, x)] = el
    else:
        if category in ["Lanthanide", "Actinide"]:
            f_block_elements.append(el)
        else:
            if period is not None and group is not None and 1 <= period <= MAX_PERIOD and 1 <= group <= MAX_GROUP:
                position_map[(period, group)] = el

f_block_elements = sorted(
    [e for e in f_block_elements if e.get("atomic_number")],
    key=lambda e: e["atomic_number"],
)

# --------------------------
# Fast lookup + symbol mappings
# --------------------------
ELEMENTS_BY_ATNO = {}
SYMBOL_TO_ATNO = {}
ATNO_TO_SYMBOL = {}

for _, r in df.iterrows():
    at = to_int_or_none(r.get("atomic_number", "") or r.get("atomic no", "") or r.get("Z", ""))
    sym = (r.get("symbol") or "").strip()
    if at:
        ELEMENTS_BY_ATNO[at] = {
            "name": (r.get("name") or "").strip(),
            "symbol": sym,
            "category": (r.get("category") or "").strip(),
        }
    if at and sym:
        SYMBOL_TO_ATNO[sym.lower()] = int(at)
        ATNO_TO_SYMBOL[int(at)] = sym


# --------------------------
# Element Combination (loads "combination" OR "combination.csv")
# --------------------------
COMBO_FILE_NOEXT = os.path.join(os.path.dirname(__file__), "combination")
COMBO_FILE_CSV = os.path.join(os.path.dirname(__file__), "combination.csv")
combo_path = COMBO_FILE_NOEXT if os.path.exists(COMBO_FILE_NOEXT) else COMBO_FILE_CSV

if os.path.exists(combo_path):
    combo_df = pd.read_csv(
        combo_path,
        dtype=str,
        sep=None,              # auto-detect delimiter (tab/comma)
        engine="python",
        encoding="utf-8-sig",  # handle BOM
    ).fillna("")
    combo_df.columns = [c.strip().replace("\ufeff", "") for c in combo_df.columns]
else:
    combo_df = pd.DataFrame()


def parse_element_token(token):
    t = (token or "").replace("\ufeff", "").strip()
    if not t:
        return None

    # atomic number
    if t.replace(".", "", 1).isdigit():
        at = int(float(t))
        sym = ATNO_TO_SYMBOL.get(at)
        return {"atno": at, "symbol": sym} if sym else None

    # symbol
    sym = _norm_symbol(t)
    at = SYMBOL_TO_ATNO.get(sym.lower())
    return {"atno": at, "symbol": sym} if at else None


# Lookup: store both directions, so H+O and O+H both work
COMBO_LOOKUP = {}
if not combo_df.empty:
    for _, row in combo_df.iterrows():
        a = _norm_symbol(row.get("reactant_a", ""))
        b = _norm_symbol(row.get("reactant_b", ""))
        if not a or not b:
            continue
        d = row.to_dict()
        COMBO_LOOKUP[(a, b)] = d
        COMBO_LOOKUP[(b, a)] = d


# --------------------------
# UI cell builders
# --------------------------
def make_cell(el):
    if not el or not el.get("atomic_number"):
        return html.Div("", className="element-cell empty")

    color = category_color(el.get("category", ""))
    atnum = str(int(el["atomic_number"])) if el.get("atomic_number") is not None else ""

    return html.Button(
        [
            html.Div(atnum, className="atnum"),
            html.Div(el.get("symbol", ""), className="symbol"),
            html.Div(el.get("name", ""), className="ename", style={"color": "#003300"}),
        ],
        id={"type": "element-button", "index": str(int(el["atomic_number"]))},
        n_clicks=0,
        title=f"{el.get('name','')} ({el.get('symbol','')})\nAtomic mass: {el.get('atomic_mass','')}",
        className="element-cell",
        style={"backgroundColor": color},
    )


def make_locked_cell(el, level):
    return html.Div(
        "",
        className="element-cell locked",
        title=f"Locked for {level}",
        style={"backgroundColor": "#FFFFFF", "opacity": "1", "filter": "none"},
    )


def build_grid(search_value=None, categories=None, level=None):
    s = (search_value or "").strip().lower()
    selected_cats = set(categories or [])
    lvl = level or "Advanced"

    rows = []
    for period in range(1, MAX_PERIOD + 1):
        cells = []
        for group in range(1, MAX_GROUP + 1):
            el = position_map.get((period, group))

            if el and el.get("atomic_number"):
                atno = int(el["atomic_number"])
                cat = el.get("category", "")
                if not is_unlocked_for_level(lvl, atno, cat):
                    cells.append(make_locked_cell(el, lvl))
                    continue

            visible = True
            if el:
                sym = (el.get("symbol") or "").lower()
                nm = (el.get("name") or "").lower()
                if s and (s not in sym and s not in nm):
                    visible = False
                if selected_cats and el.get("category") not in selected_cats:
                    visible = False

            if el and not visible:
                style = {
                    "backgroundColor": category_color(el.get("category")),
                    "opacity": "0.18",
                    "filter": "grayscale(0.8)",
                }
                cells.append(html.Div("", className="element-cell empty", style=style))
            else:
                cells.append(make_cell(el) if el else html.Div("", className="element-cell empty"))

        rows.append(html.Div(cells, className="element-row"))

    spacer_cells = [html.Div("", className="element-cell empty") for _ in range(MAX_GROUP)]
    rows.append(html.Div(spacer_cells, className="element-row", style={"marginBottom": "8px"}))

    lanth = sorted([e for e in f_block_elements if e.get("category") == "Lanthanide"], key=lambda e: e["atomic_number"])
    actin = sorted([e for e in f_block_elements if e.get("category") == "Actinide"], key=lambda e: e["atomic_number"])

    def render_frow(elements_list):
        cells = [html.Div("", className="element-cell empty") for _ in range(3)]

        if lvl == "Intermediate":
            for el in elements_list:
                cells.append(make_locked_cell(el, "Intermediate"))
            while len(cells) < MAX_GROUP:
                cells.append(html.Div("", className="element-cell empty"))
            return html.Div(cells, className="element-row", style={"marginTop": "4px"})

        for el in elements_list:
            if el and el.get("atomic_number"):
                atno = int(el["atomic_number"])
                cat = el.get("category", "")
                if not is_unlocked_for_level(lvl, atno, cat):
                    cells.append(make_locked_cell(el, lvl))
                    continue

            visible = True
            if s:
                sym = (el.get("symbol") or "").lower()
                nm = (el.get("name") or "").lower()
                if s not in sym and s not in nm:
                    visible = False
            if selected_cats and el.get("category") not in selected_cats:
                visible = False

            if not visible:
                style = {
                    "backgroundColor": category_color(el.get("category")),
                    "opacity": "0.18",
                    "filter": "grayscale(0.8)",
                }
                cells.append(html.Div("", className="element-cell empty", style=style))
            else:
                cells.append(make_cell(el))

        while len(cells) < MAX_GROUP:
            cells.append(html.Div("", className="element-cell empty"))

        return html.Div(cells, className="element-row", style={"marginTop": "4px"})

    if lanth:
        rows.append(render_frow(lanth))
    if actin:
        rows.append(render_frow(actin))

    return rows


# --------------------------
# Quiz bank
# --------------------------
QUIZ_BANK = {
    "Basic": [
        {
            "q": "What is the symbol for Hydrogen?",
            "atno": 1,
            "ask": "symbol",
            "hint": "Hydrogen is the most abundant element in the universe — its symbol is just one letter.",
        },
        {
            "q": "What is the name of the element with symbol O?",
            "atno": 8,
            "ask": "name",
            "hint": "It makes up about 21% of Earth’s atmosphere and is essential for respiration.",
        },
        {
            "q": "What is the symbol for the 20th element?",
            "atno": 20,
            "ask": "symbol",
            "hint": "This element helps strengthen bones and teeth — its symbol is two letters.",
        },
    ],
    "Intermediate": [
        {
            "q": "What is the atomic number for Iron?",
            "atno": 26,
            "ask": "atomic_number",
            "hint": "Iron is the main ingredient in steel and is vital in your blood — its atomic number is in the 20s.",
        },
        {
            "q": "What is the symbol for Krypton?",
            "atno": 36,
            "ask": "symbol",
            "hint": "A noble gas used in lighting — its symbol starts with K.",
        },
        {
            "q": "What is the name of the 54th element?",
            "atno": 54,
            "ask": "name",
            "hint": "It’s a noble gas — used in some lamps and anesthesia research.",
        },
    ],
    "Advanced": [
        {
            "q": "What is the symbol for Mercury?",
            "atno": 80,
            "ask": "symbol",
            "hint": "Mercury is a metal that’s liquid at room temperature — its symbol doesn’t match its English name.",
        },
        {
            "q": "What is the atomic number of Plutonium?",
            "atno": 94,
            "ask": "atomic_number",
            "hint": "A radioactive element used in nuclear technology — its atomic number is in the 90s.",
        },
        {
            "q": "What is the symbol for Uranium?",
            "atno": 92,
            "ask": "symbol",
            "hint": "A heavy element used as nuclear fuel — its symbol is a single letter.",
        },
    ],
}


def get_answer_for_main(qobj):
    atno = int(qobj["atno"])
    el = ELEMENTS_BY_ATNO.get(atno, {})
    ask = qobj["ask"]

    if ask == "atomic_number":
        return [str(atno)]
    if ask == "symbol":
        return [norm(el.get("symbol", ""))]
    if ask == "name":
        return [norm(el.get("name", ""))]
    return []


def build_related(qobj, level):
    atno = int(qobj["atno"])
    el = ELEMENTS_BY_ATNO.get(atno, {})
    name = el.get("name") or f"element #{atno}"
    sym = el.get("symbol") or ""
    cat = (el.get("category") or "").strip()

    if qobj["ask"] == "symbol":
        rel_q = f"What is the atomic number of {name}?"
        rel_a = [str(atno)]
    else:
        rel_q = f"What is the symbol for {name}?"
        rel_a = [norm(sym)]

    if not is_unlocked_for_level(level, atno, cat):
        fallback_at = 1 if level == "Basic" else 26 if level == "Intermediate" else 1
        fb = ELEMENTS_BY_ATNO.get(fallback_at, {})
        fb_name = fb.get("name") or f"element #{fallback_at}"
        return {"q": f"What is the atomic number of {fb_name}?", "a": [str(fallback_at)]}

    return {"q": rel_q, "a": [norm(x) for x in rel_a if x]}


# --------------------------
# Dash app
# --------------------------
app = dash.Dash(__name__, title="Interactive Periodic Table", suppress_callback_exceptions=True)
server = app.server

WINE_COLOR = "#4B0012"
WIKI_BLUE = "#003399"
TEST_GREEN = "#003300"
PAGE_GREY = "#f3f6f9"

THIN_HR = html.Hr(style={"margin": "6px 0", "border": "none", "borderTop": "1px solid #d6d6d6", "height": "0"})
THIN_HR_WIDE = html.Hr(style={"margin": "10px 0", "border": "none", "borderTop": "1px solid #d6d6d6", "height": "0"})

# Keep left and right bottom blocks aligned
BOTTOM_BLOCK_HEIGHT = "260px"
BOTTOM_BLOCK_PADDING_TOP = "18px"
BOTTOM_BLOCK_PADDING_BOTTOM = "10px"

# Match Combine button height to the element input boxes
COMBO_INPUT_HEIGHT = "28px"

# Combine button width between previous two sizes
COMBINE_BUTTON_WIDTH = "39%"


# --------------------------
# Table layout
# --------------------------
table_layout = html.Div(
    [
        html.H2(
            "Interactive Periodic Table",
            style={
                "textAlign": "center",
                "color": "#004d00",
                "fontSize": "35px",
                "margin": "0",
                "paddingTop": "4px",
                "paddingBottom": "14px",
            },
        ),
        html.Div(
            [
                # LEFT column
                html.Div(
                    [
                        html.Div(
                            [
                                html.Label(
                                    "Search (symbol or name)",
                                    style={"fontSize": "14px", "marginBottom": "4px", "fontWeight": "bold", "color": "#003300"},
                                ),
                                dcc.Input(
                                    id="search",
                                    placeholder="e.g., Fe or iron",
                                    type="text",
                                    debounce=True,
                                    style={
                                        "width": "100%",
                                        "boxSizing": "border-box",
                                        "fontSize": "12px",
                                        "height": "20px",
                                        "lineHeight": "20px",
                                        "padding": "0 6px",
                                        "marginBottom": "6px",
                                        "borderRadius": "6px",
                                        "border": "1px solid rgba(0,0,0,0.2)",
                                        "outline": "none",
                                        "background": "white",
                                    },
                                ),
                                html.Label(
                                    "Filter category",
                                    style={"fontSize": "14px", "marginTop": "2px", "marginBottom": "4px", "fontWeight": "bold", "color": "#003300"},
                                ),
                                dcc.Dropdown(
                                    id="category-filter",
                                    options=[{"label": k, "value": k} for k in sorted(CATEGORY_COLORS.keys())],
                                    multi=True,
                                    placeholder="Select categories...",
                                    style={"fontSize": "12px", "marginBottom": "6px", "width": "100%", "boxSizing": "border-box"},
                                ),
                                html.Button(
                                    "Reset",
                                    id="reset-btn",
                                    style={
                                        "fontSize": "12px",
                                        "marginBottom": "6px",
                                        "fontWeight": "bold",
                                        "color": "#003300",
                                        "width": "100%",
                                        "boxSizing": "border-box",
                                        "height": "22px",
                                        "padding": "0 6px",
                                        "borderRadius": "6px",
                                        "border": "1px solid rgba(0,0,0,0.18)",
                                        "background": "#E6E6E6",
                                        "cursor": "pointer",
                                    },
                                ),
                                THIN_HR,
                                html.Div(
                                    id="legend",
                                    children=[
                                        html.Div(
                                            [
                                                html.Div(
                                                    style={
                                                        "display": "inline-block",
                                                        "width": "14px",
                                                        "height": "14px",
                                                        "backgroundColor": color,
                                                        "marginRight": "6px",
                                                        "border": "1px solid #aaa",
                                                    }
                                                ),
                                                html.Span(k, style={"fontSize": "12px"}),
                                            ],
                                            style={"marginBottom": "3px"},
                                        )
                                        for k, color in CATEGORY_COLORS.items()
                                    ],
                                ),
                            ],
                            style={"paddingTop": "8px"},
                        ),
                        html.Div(
                            id="element-details",
                            style={
                                "fontSize": "12px",
                                "lineHeight": "1.35",
                                "marginTop": "auto",
                                "borderTop": "1px solid #ccc",
                                "height": BOTTOM_BLOCK_HEIGHT,
                                "overflowY": "auto",
                                "paddingTop": BOTTOM_BLOCK_PADDING_TOP,
                                "paddingBottom": BOTTOM_BLOCK_PADDING_BOTTOM,
                                "paddingRight": "6px",
                                "color": WINE_COLOR,
                                "boxSizing": "border-box",
                            },
                        ),
                    ],
                    style={
                        "width": "20%",
                        "padding": "12px",
                        "boxSizing": "border-box",
                        "minWidth": "220px",
                        "borderRight": "1px solid #ddd",
                        "display": "flex",
                        "flexDirection": "column",
                        "height": "100%",
                        "backgroundColor": PAGE_GREY,
                        "overflow": "visible",
                    },
                ),

                # CENTER column
                html.Div(
                    [
                        html.Div(
                            id="periodic-grid",
                            children=[],
                            style={"display": "inline-block", "textAlign": "center", "overflow": "hidden"},
                        ),
                    ],
                    style={
                        "width": "60%",
                        "display": "flex",
                        "justifyContent": "center",
                        "alignItems": "flex-start",
                        "padding": "12px",
                        "paddingTop": "26px",
                        "boxSizing": "border-box",
                        "minWidth": "420px",
                        "backgroundColor": PAGE_GREY,
                        "height": "100%",
                    },
                ),

                # RIGHT column
                html.Div(
                    [
                        html.Div(
                            id="definition-area",
                            children="Click an element to see its definition.",
                            style={"flex": "1", "overflowY": "auto", "paddingRight": "6px"},
                        ),
                        html.Div(
                            [
                                html.Div(
                                    "Element Combination",
                                    style={"fontWeight": "800", "color": "#003300", "fontSize": "14px"},
                                ),
                                html.Div(
                                    [
                                        dcc.Input(
                                            id="combo-in-1",
                                            placeholder="Symbol or number (e.g., H or 1)",
                                            type="text",
                                            style={
                                                "width": "44%",
                                                "height": COMBO_INPUT_HEIGHT,
                                                "borderRadius": "10px",
                                                "border": "1px solid rgba(0,0,0,0.18)",
                                                "padding": "0 8px",
                                                "boxSizing": "border-box",
                                                "outline": "none",
                                            },
                                        ),
                                        html.Span(
                                            "+",
                                            style={"display": "inline-block", "width": "8%", "textAlign": "center", "fontWeight": "900"},
                                        ),
                                        dcc.Input(
                                            id="combo-in-2",
                                            placeholder="Symbol or number (e.g., O or 8)",
                                            type="text",
                                            style={
                                                "width": "44%",
                                                "height": COMBO_INPUT_HEIGHT,
                                                "borderRadius": "10px",
                                                "border": "1px solid rgba(0,0,0,0.18)",
                                                "padding": "0 8px",
                                                "boxSizing": "border-box",
                                                "outline": "none",
                                            },
                                        ),
                                    ],
                                    style={"display": "flex", "alignItems": "center", "marginTop": "10px"},
                                ),
                                html.Button(
                                    "Combine",
                                    id="combo-go",
                                    n_clicks=0,
                                    style={
                                        "marginTop": "10px",
                                        "width": COMBINE_BUTTON_WIDTH,
                                        "height": COMBO_INPUT_HEIGHT,
                                        "borderRadius": "10px",
                                        "border": "1px solid rgba(0,0,0,0.18)",
                                        "background": "#E6E6E6",
                                        "cursor": "pointer",
                                        "color": WINE_COLOR,
                                        "fontWeight": "600",
                                        "marginLeft": "auto",
                                        "marginRight": "auto",
                                        "display": "block",
                                    },
                                ),
                                html.Div(
                                    id="combo-result",
                                    style={
                                        "marginTop": "10px",
                                        "whiteSpace": "pre-wrap",
                                        "color": WINE_COLOR,
                                        "flex": "1",
                                        "minHeight": "0",
                                        "overflowY": "auto",
                                        "paddingRight": "2px",
                                    },
                                ),
                            ],
                            style={
                                "borderTop": "1px solid #ddd",
                                "paddingTop": BOTTOM_BLOCK_PADDING_TOP,
                                "paddingBottom": BOTTOM_BLOCK_PADDING_BOTTOM,
                                "backgroundColor": PAGE_GREY,
                                "height": BOTTOM_BLOCK_HEIGHT,
                                "display": "flex",
                                "flexDirection": "column",
                                "boxSizing": "border-box",
                            },
                        ),
                    ],
                    id="right-column",
                    style={
                        "width": "20%",
                        "padding": "12px",
                        "paddingTop": "26px",
                        "boxSizing": "border-box",
                        "minWidth": "220px",
                        "borderLeft": "1px solid #ddd",
                        "fontSize": "12px",
                        "lineHeight": "1.35",
                        "color": WINE_COLOR,
                        "backgroundColor": PAGE_GREY,
                        "height": "100%",
                        "display": "flex",
                        "flexDirection": "column",
                        "overflow": "hidden",
                    },
                ),
            ],
            style={
                "display": "flex",
                "alignItems": "stretch",
                "justifyContent": "space-between",
                "flex": "1",
                "backgroundColor": PAGE_GREY,
                "minHeight": "0",
            },
        ),
        html.Div(id="hidden-json", style={"display": "none"}, children=json.dumps(df.to_dict(orient="records"))),
    ],
    style={
        "height": "100vh",
        "display": "flex",
        "flexDirection": "column",
        "backgroundColor": PAGE_GREY,
        "paddingTop": "22px",
        "boxSizing": "border-box",
    },
)

# ---- Quiz layout
quiz_layout = html.Div(
    [
        html.Div(
            id="level-picker",
            children=[
                html.H2(
                    "Select Your Knowledge",
                    style={"textAlign": "center", "color": TEST_GREEN, "fontSize": "48px", "margin": "0 0 18px 0", "fontWeight": "800"},
                ),
                html.Div(
                    [
                        html.Button("Basic", id="lvl-basic", n_clicks=0, className="lvlbtn"),
                        html.Button("Intermediate", id="lvl-intermediate", n_clicks=0, className="lvlbtn"),
                        html.Button("Advanced", id="lvl-advanced", n_clicks=0, className="lvlbtn"),
                    ],
                    style={"display": "flex", "justifyContent": "center", "gap": "8px", "marginTop": "30px"},
                ),
            ],
            style={"width": "100%", "maxWidth": "780px", "textAlign": "center", "marginLeft": "auto", "marginRight": "auto", "transform": "translateY(-70px)"},
        ),
        html.Div(id="quiz-area", style={"width": "100%", "maxWidth": "780px", "textAlign": "center", "padding": "0 16px"}),
    ],
    style={
        "minHeight": "100vh",
        "width": "100%",
        "display": "flex",
        "flexDirection": "column",
        "alignItems": "center",
        "justifyContent": "center",
        "backgroundColor": "white",
        "boxSizing": "border-box",
        "paddingLeft": "12px",
        "paddingRight": "12px",
    },
)

app.layout = html.Div(
    [
        dcc.Store(id="app-phase", data="quiz"),
        dcc.Store(
            id="quiz-store",
            data={"stage": "level", "level": None, "idx": 0, "score": 0, "mode": "main", "hint_used": False, "feedback": "", "related": None},
        ),
        html.Div(id="quiz-container", children=quiz_layout),
        html.Div(id="table-container", children=table_layout, style={"display": "none"}),
    ],
    style={"fontFamily": "Arial, sans-serif", "padding": "0", "height": "100vh", "overflow": "hidden", "backgroundColor": PAGE_GREY},
)

# --------------------------
# Quiz callbacks
# --------------------------
@app.callback(Output("level-picker", "style"), Input("quiz-store", "data"))
def toggle_level_picker(qs):
    return (
        {"display": "block", "width": "100%", "maxWidth": "780px", "textAlign": "center", "marginLeft": "auto", "marginRight": "auto", "transform": "translateY(-70px)"}
        if (qs or {}).get("stage", "level") == "level"
        else {"display": "none"}
    )


@app.callback(
    Output("quiz-store", "data"),
    Input("lvl-basic", "n_clicks"),
    Input("lvl-intermediate", "n_clicks"),
    Input("lvl-advanced", "n_clicks"),
    State("quiz-store", "data"),
    prevent_initial_call=True,
)
def pick_level(nb, ni, na, qs):
    ctx = dash.callback_context
    if not ctx.triggered:
        return qs
    trig = ctx.triggered[0]["prop_id"].split(".")[0]
    level = {"lvl-basic": "Basic", "lvl-intermediate": "Intermediate", "lvl-advanced": "Advanced"}.get(trig)
    if not level:
        return qs
    return {"stage": "questions", "level": level, "idx": 0, "score": 0, "mode": "main", "hint_used": False, "feedback": "", "related": None}


@app.callback(Output("quiz-area", "children"), Input("quiz-store", "data"))
def render_quiz(qs):
    stage = (qs or {}).get("stage", "level")
    level = (qs or {}).get("level")
    idx = int((qs or {}).get("idx", 0))
    mode = (qs or {}).get("mode", "main")
    related = (qs or {}).get("related")
    feedback = (qs or {}).get("feedback", "")

    if stage == "level":
        return html.Div("")

    bank = QUIZ_BANK.get(level, QUIZ_BANK["Basic"])
    main_qobj = bank[idx]
    q_text = related.get("q", "") if (mode == "related" and related) else main_qobj["q"]

    return html.Div(
        [
            html.H3("Test Your Knowledge", style={"color": TEST_GREEN, "margin": "0 0 14px 0", "fontSize": "48px"}),
            html.Div(q_text, style={"fontSize": "30px", "marginTop": "8px", "color": "#000000"}),
            dcc.Input(
                id="quiz-answer",
                type="text",
                value="",
                autoFocus=True,
                style={
                    "width": "320px",
                    "fontSize": "16px",
                    "marginTop": "28px",
                    "marginBottom": "10px",
                    "padding": "6px 8px",
                    "height": "30px",
                    "lineHeight": "18px",
                    "display": "block",
                    "marginLeft": "auto",
                    "marginRight": "auto",
                    "borderRadius": "12px",
                    "border": "1px solid rgba(0,0,0,0.12)",
                    "outline": "none",
                    "boxSizing": "border-box",
                },
            ),
            html.Div(id="quiz-feedback", children=feedback, style={"marginTop": "0px", "color": WINE_COLOR, "fontSize": "18px", "minHeight": "18px"}),
            html.Button("Next", id="quiz-next", n_clicks=0, className="lvlbtn nextbtn", style={"marginTop": "0px"}),
        ],
        style={"width": "100%", "maxWidth": "780px", "textAlign": "center"},
    )


def _advance_question(qs):
    idx = int(qs.get("idx", 0))
    score = int(qs.get("score", 0)) + 1
    if idx >= 2:
        return {**qs, "stage": "done", "score": score}, "table"
    return ({**qs, "idx": idx + 1, "score": score, "mode": "main", "hint_used": False, "feedback": "", "related": None}, dash.no_update)


@app.callback(
    Output("quiz-store", "data", allow_duplicate=True),
    Output("app-phase", "data"),
    Input("quiz-next", "n_clicks"),
    Input("quiz-answer", "n_submit"),
    State("quiz-answer", "value"),
    State("quiz-store", "data"),
    prevent_initial_call=True,
)
def next_question(n_clicks, n_submit, ans, qs):
    qs = qs or {}
    if qs.get("stage") != "questions":
        return dash.no_update, dash.no_update

    level = qs.get("level") or "Basic"
    idx = int(qs.get("idx", 0))
    mode = qs.get("mode", "main")
    user = norm(ans)

    bank = QUIZ_BANK.get(level, QUIZ_BANK["Basic"])
    main_qobj = bank[idx]

    if mode == "related" and qs.get("related"):
        rel = qs["related"]
        rel_answers = [norm(a) for a in (rel.get("a") or [])]
        if user in rel_answers:
            return _advance_question(qs)
        return ({**qs, "mode": "main"}, dash.no_update)

    acceptable = [norm(a) for a in get_answer_for_main(main_qobj)]
    if user in acceptable:
        return _advance_question(qs)

    if not qs.get("hint_used", False):
        hint_txt = main_qobj.get("hint", "")
        hint_msg = f"Hint: {hint_txt}" if hint_txt else ""
        return ({**qs, "hint_used": True, "feedback": hint_msg, "mode": "main"}, dash.no_update)

    rel = qs.get("related") or build_related(main_qobj, level)
    return ({**qs, "mode": "related", "related": rel}, dash.no_update)


@app.callback(Output("quiz-container", "style"), Output("table-container", "style"), Input("app-phase", "data"))
def toggle_pages(phase):
    if phase == "table":
        return {"display": "none"}, {"display": "block", "backgroundColor": PAGE_GREY, "height": "100vh"}
    return {"display": "block"}, {"display": "none"}


# --------------------------
# Periodic table callbacks
# --------------------------
@app.callback(
    Output("element-details", "children"),
    Input({"type": "element-button", "index": dash.dependencies.ALL}, "n_clicks"),
    State("hidden-json", "children"),
    State("quiz-store", "data"),
    prevent_initial_call=False,
)
def show_element(n_clicks_list, json_data, qs):
    try:
        records = json.loads(json_data)
    except Exception:
        records = []
    elements = {str(int(float(e["atomic_number"]))): e for e in records if e.get("atomic_number")}

    level = (qs or {}).get("level") or "Advanced"

    ctx = dash.callback_context
    if not ctx.triggered:
        el = elements.get("1")
    else:
        triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]
        try:
            triggered = json.loads(triggered_id.replace("'", '"'))
            el_idx = triggered["index"]
            el = elements.get(el_idx)
        except Exception:
            return dash.no_update

    if not el:
        return dash.no_update

    atno = int(float(el["atomic_number"]))
    cat = el.get("category", "")
    if not is_unlocked_for_level(level, atno, cat):
        msg = (
            "This level shows only the first 20 elements."
            if level == "Basic"
            else "Intermediate shows only the first 54 elements (H–Xe). Lanthanides/Actinides are locked."
            if level == "Intermediate"
            else "Locked for this level."
        )
        return [html.H3("Locked element", style={"fontSize": "14px", "margin": "0 0 2px 0", "color": "#003300"}), html.P(msg, style={"margin": "2px 0"})]

    return [
        # ✅ margin-top removed so spacing matches right block
        html.H3([html.Span(f"{el.get('name','')} ({el.get('symbol','')})", style={"color": "#003300"})], style={"fontSize": "14px", "margin": "0 0 2px 0"}),
        html.P(f"Atomic number: {atno}", style={"margin": "2px 0"}),
        html.P(f"Atomic mass: {el.get('atomic_mass','')}", style={"margin": "2px 0"}),
        html.P(f"Category: {el.get('category','')}", style={"margin": "2px 0"}),
        html.P(f"Group: {el.get('group','')}  Period: {el.get('period','')}", style={"margin": "2px 0"}),
        html.P(f"Electronic configuration: {el.get('electronic_configuration','')}", style={"margin": "2px 0"}),
        html.P(f"Occurrence: {el.get('occurrence','')}", style={"margin": "2px 0"}),
        THIN_HR_WIDE,
        html.H5("Quick stats", style={"fontSize": "12px", "margin": "6px 0 4px 0"}),
        html.Ul(
            [
                html.Li(f"Atomic number: {atno}", style={"margin": "2px 0"}),
                html.Li(f"Symbol: {el.get('symbol','')}", style={"margin": "2px 0"}),
                html.Li(f"Atomic mass: {el.get('atomic_mass','')}", style={"margin": "2px 0"}),
            ],
            style={"paddingLeft": "16px", "margin": "6px 0 0 0"},
        ),
    ]


@app.callback(
    Output("periodic-grid", "children"),
    Input("search", "value"),
    Input("category-filter", "value"),
    Input("quiz-store", "data"),
    Input("app-phase", "data"),
)
def update_grid(search_value, categories, qs, phase):
    level = (qs or {}).get("level") or "Advanced"
    return build_grid(search_value, categories, level)


@app.callback(
    Output("search", "value"),
    Output("category-filter", "value"),
    Input("reset-btn", "n_clicks"),
    prevent_initial_call=True,
)
def reset_filters(n):
    return "", []


@app.callback(
    Output("definition-area", "children"),
    Input({"type": "element-button", "index": dash.dependencies.ALL}, "n_clicks"),
    State("hidden-json", "children"),
    State("quiz-store", "data"),
    prevent_initial_call=False,
)
def show_definition(n_clicks_list, json_data, qs):
    try:
        records = json.loads(json_data)
    except Exception:
        records = []
    elements = {str(int(float(e["atomic_number"]))): e for e in records if e.get("atomic_number")}

    level = (qs or {}).get("level") or "Advanced"

    ctx = dash.callback_context
    if not ctx.triggered:
        return "Click an element to see its definition."

    triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        triggered = json.loads(triggered_id.replace("'", '"'))
        el_idx = triggered["index"]
        el = elements.get(el_idx)
    except Exception:
        return dash.no_update

    if not el:
        return dash.no_update

    atno = int(float(el["atomic_number"]))
    cat = el.get("category", "")
    if not is_unlocked_for_level(level, atno, cat):
        msg = (
            "This level shows only the first 20 elements."
            if level == "Basic"
            else "Intermediate shows only the first 54 elements (H–Xe). Lanthanides/Actinides are locked."
            if level == "Intermediate"
            else "Locked for this level."
        )
        return html.Div([html.H3("Locked element", style={"color": "#003300", "margin": "4px 0"}), html.P(msg, style={"margin": "2px 0"})], style={"color": WINE_COLOR})

    definition_text = definitions.get(el_idx, "No definition available.")
    el_name = (el.get("name") or "").strip()
    wiki_url = f"https://en.wikipedia.org/wiki/{quote(el_name.replace(' ', '_'))}" if el_name else "https://en.wikipedia.org/"

    return html.Div(
        [
            html.H3(f"{el.get('name','')} ({el.get('symbol','')})", style={"color": "#003300", "margin": "4px 0"}),
            html.P(definition_text or "No definition available.", style={"margin": "2px 0"}),
            html.Div(
                [
                    html.Span("Learn more: ", style={"fontWeight": "700"}),
                    html.A("Wikipedia", href=wiki_url, target="_blank", style={"color": WIKI_BLUE, "textDecoration": "underline"}),
                ],
                style={"marginTop": "10px"},
            ),
        ],
        style={"color": WINE_COLOR},
    )


# --------------------------
# Element Combination callback (Common misconception + Confidence hidden)
# --------------------------
@app.callback(
    Output("combo-result", "children"),
    Input("combo-go", "n_clicks"),
    Input("combo-in-1", "n_submit"),
    Input("combo-in-2", "n_submit"),
    State("combo-in-1", "value"),
    State("combo-in-2", "value"),
    State("quiz-store", "data"),
    prevent_initial_call=True,
)
def run_combination(n_clicks, s1_submit, s2_submit, v1, v2, qs):
    level = (qs or {}).get("level") or "Advanced"

    p1 = parse_element_token(v1)
    p2 = parse_element_token(v2)
    if not p1 or not p2:
        return "Enter two valid elements (symbol like Fe, or atomic number like 26)."

    if not is_unlocked_for_level(level, p1["atno"]) or not is_unlocked_for_level(level, p2["atno"]):
        return f"One or both elements are locked for {level}. Try unlocked elements."

    row = COMBO_LOOKUP.get((p1["symbol"], p2["symbol"]))
    if not row:
        return f"No record found in combination file for: {p1['symbol']} + {p2['symbol']}"

    combo_type = (row.get("combination_type", "") or "").strip()
    formula = (row.get("primary_product_formula", "") or "").strip()
    pname = (row.get("primary_product_name", "") or "").strip()
    eqn = (row.get("balanced_equation", "") or "").strip()
    state = (row.get("state_at_stp", "") or "").strip()
    cond = (row.get("conditions", "") or "").strip()
    facts = (row.get("facts", "") or "").strip()

    lines = [f"{p1['symbol']} + {p2['symbol']}"]
    if combo_type:
        lines.append(f"Type: {combo_type}")

    if pname or formula:
        if pname and formula:
            lines.append(f"Primary product: {pname} ({formula})")
        elif formula:
            lines.append(f"Primary product: {formula}")
        else:
            lines.append(f"Primary product: {pname}")

    if eqn:
        lines.append(f"Balanced equation: {eqn}")
    if state:
        lines.append(f"State at STP: {state}")
    if cond:
        lines.append(f"Conditions: {cond}")
    if facts:
        lines.append(f"Facts: {facts}")

    return "\n".join(lines)


# --------------------------
# CSS
# --------------------------
app.index_string = f"""
<!DOCTYPE html>
<html>
  <head>
    {{%metas%}}
    <title>{{%title%}}</title>
    {{%favicon%}}
    {{%css%}}
    <style>
      html, body {{
        background: {PAGE_GREY};
        margin: 0;
        padding: 0;
        font-family: Arial, sans-serif;
        height: 100vh;
        overflow: hidden;
      }}

      #periodic-grid {{ display:inline-block; text-align:center; padding:6px; overflow:hidden; max-width:100%; }}
      .element-row {{ display:grid; grid-template-columns:repeat(18, minmax(35px,1fr)); gap:4px; margin-bottom:4px; justify-items:center; }}
      .element-cell {{ border-radius:6px; border:1px solid rgba(0,0,0,0.08); min-width:35px; min-height:50px; display:flex; flex-direction:column; align-items:center; justify-content:center; cursor:pointer; transition: transform .08s, box-shadow .08s; box-shadow:0 1px 1px rgba(0,0,0,0.03); font-size:11px; padding:3px; text-align:center; }}
      .element-cell.empty {{ background:transparent; border:none; box-shadow:none; cursor:default; }}
      .element-cell:hover {{ transform:translateY(-2px); box-shadow:0 6px 18px rgba(0,0,0,0.06); }}

      .element-cell.locked {{
        background: #ffffff !important;
        cursor: default;
      }}
      .element-cell.locked:hover {{
        transform: none !important;
        box-shadow: 0 1px 1px rgba(0,0,0,0.03) !important;
      }}

      .atnum {{ font-size:9px; opacity:0.7; }}
      .symbol {{ font-weight:700; font-size:16px; margin-top:1px; margin-bottom:1px; }}
      .ename {{ font-size:7px; opacity:0.9; text-align:center; color:#003300; }}

      .lvlbtn {{
        padding: 8px 22px;
        font-size: 20px;
        border-radius: 12px;
        border: 1px solid rgba(0,0,0,0.12);
        cursor: pointer;
        background: #E6E6E6;
        color: {WINE_COLOR};
        font-weight: 400;
      }}
      .lvlbtn:hover {{ box-shadow: 0 8px 18px rgba(0,0,0,0.06); transform: translateY(-1px); transition: .08s; }}

      .nextbtn {{
        padding: 9px 26px;
        font-size: 21px;
      }}

      /* Filter category dropdown styling */
      #category-filter .Select-control {{
        height: 20px !important;
        min-height: 20px !important;
        border-radius: 6px !important;
        border: 1px solid rgba(0,0,0,0.2) !important;
        overflow: hidden !important;
        background: #ffffff !important;
      }}
      #category-filter .Select-placeholder,
      #category-filter .Select-value-label {{
        line-height: 20px !important;
      }}
      #category-filter .Select-input {{
        height: 20px !important;
      }}
      #category-filter .Select-input > input {{
        padding: 0 6px !important;
        line-height: 20px !important;
      }}
      #category-filter .Select-arrow-zone,
      #category-filter .Select-clear-zone {{
        padding-top: 0 !important;
        padding-bottom: 0 !important;
      }}
      #category-filter .Select-menu-outer {{
        z-index: 9999 !important;
        border-radius: 6px !important;
        overflow: hidden !important;
      }}
      #category-filter .Select-menu {{
        max-height: 220px !important;
      }}
    </style>
  </head>
  <body>
    {{%app_entry%}}
    <footer>
      {{%config%}}
      {{%scripts%}}
      {{%renderer%}}
    </footer>
  </body>
</html>
"""

if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(debug=False)
