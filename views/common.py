"""Shared styling, helpers and data used by both pages."""
import analytics
import cross_section as xs
from dash import dcc, html

COLORS = {"price": "#2a78d6", "volume": "#eb6834", "buyback": "#1baf7a", "market": "#eda100",
          "sector": "#4a3aa7", "revenue": "#008300", "fit": "#52514e", "event": "#e34948"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e9e8e4"
# Categorical palette in fixed order; colors follow the category, never the filter.
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7",
               "#e34948"]
# Secondary encoding for categories beyond what color alone can separate.
SYMBOLS = ["circle", "square", "diamond", "triangle-up", "cross", "x", "triangle-down",
           "pentagon", "star", "hexagon"]
DIVERGING = [[0, "#2a78d6"], [0.5, "#f0efec"], [1, "#e34948"]]  # blue <-> gray <-> red

universe, _, _, meta = analytics.load_cache()
INFO = universe.set_index("id")
CATEGORY_ORDER = xs.category_order()
CATEGORY_STYLE = {c: (CATEGORICAL[i % len(CATEGORICAL)], SYMBOLS[i % len(SYMBOLS)])
                  for i, c in enumerate(CATEGORY_ORDER)}


def coin_options(ids, note_buybacks=False):
    """Dropdown options for coins, sorted by market cap (largest first) and numbered 1, 2, 3, …
    within this list, so a filtered list is numbered from 1 again."""
    ids = sorted(ids, key=lambda i: -INFO.at[i, "market_cap"])
    return [{"label": f"{k}. {INFO.at[i, 'symbol']} · {INFO.at[i, 'name']}"
             + ("  (no buyback data)" if note_buybacks and not INFO.at[i, "has_buybacks"] else ""),
             "value": i} for k, i in enumerate(ids, 1)]


def base_layout(**kw):
    """Shared figure layout; any key (e.g. legend, margin) can be overridden."""
    return {**dict(
        template="plotly_white",
        font=dict(family="Inter, system-ui, sans-serif", size=12, color=INK),
        margin=dict(l=60, r=30, t=30, b=50),
        hoverlabel=dict(bgcolor="white", font_color=INK),
        legend=dict(orientation="h", y=1.08, x=0, font_color=MUTED),
    ), **kw}


def axis(title, color=MUTED, **kw):
    return dict({"title": dict(text=title, font_color=color), "gridcolor": GRID,
                 "zeroline": False, "tickfont_color": color}, **kw)


def loading(*children):
    """Keep the previous output visible but dimmed, with an "Updating…" badge, while the
    callback that produces it is running (so stale charts aren't mistaken for new ones)."""
    return dcc.Loading(
        list(children), delay_show=150,
        overlay_style={"visibility": "visible", "opacity": 0.35, "filter": "grayscale(1)"},
        custom_spinner=html.Div([html.Div(className="spinner"), "Updating…"],
                                className="loading-badge"),
    )


def card_head(title, badge_id):
    """Section title plus a badge naming the token the section currently shows."""
    return html.Div([html.H2(title), html.Span(id=badge_id, className="token-badge")],
                    className="card-head")


def token_badge(token_id, prefix=""):
    r = INFO.loc[token_id]
    return [prefix, html.B(r.symbol), f" · {r['name']}"]


def tex_num(v, digits=4):
    """Number for LaTeX: plain decimals, or a × 10^k form for very large/small values."""
    m, _, e = f"{v:.{digits}g}".partition("e")
    return rf"{m} \times 10^{{{int(e)}}}" if e else m


TABLE_STYLE = dict(
    sort_action="native",
    page_action="none",
    style_table={"maxHeight": "520px", "overflowY": "auto", "overflowX": "auto"},
    style_header={"backgroundColor": "#f4f4f2", "fontWeight": 600, "color": MUTED,
                  "border": "none", "borderBottom": f"1px solid {GRID}"},
    style_cell={"fontFamily": "Inter, system-ui, sans-serif", "fontSize": 13,
                "padding": "6px 10px", "border": "none", "borderBottom": f"1px solid {GRID}",
                "backgroundColor": "#fcfcfb", "color": INK, "textAlign": "right",
                "fontVariantNumeric": "tabular-nums", "minWidth": "60px"},
)


def left_align(*cols):
    return [{"if": {"column_id": c}, "textAlign": "left"} for c in cols]
