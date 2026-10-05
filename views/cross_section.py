"""Cross-Section page: how coins move relative to each other.

- Spread: cumulative log-return spread of a pair (A − B, or β-hedged), with per-period
  spread returns underneath.
- Two-step multi-factor regression of that spread (views/regression.py).
- Coin scatter: one dot per coin in the universe, any two metrics over a date window.
- Metrics table and return-correlation heatmap for the same window.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate
from plotly.subplots import make_subplots
from scipy import stats

import analytics
import cross_section as xs
from views import regression
from views.common import (CATEGORICAL, CATEGORY_STYLE, COLORS, DIVERGING, GRID, INFO, INK, MUTED,
                          TABLE_STYLE, axis, base_layout, card_head, coin_options, left_align,
                          loading, tex_num)

LEG_COLORS = CATEGORICAL[:2]  # legs A, B
POS, NEG = "#2a78d6", "#e34948"  # spread return bars by sign
START, END = xs.date_bounds()
N_DAYS = (END - START).days + 1


def date_picker(id_, date):
    """Click to open a calendar; limited to the data's date range."""
    return dcc.DatePickerSingle(id=id_, date=date.date().isoformat(),
                                min_date_allowed=START.date().isoformat(),
                                max_date_allowed=END.date().isoformat(),
                                initial_visible_month=date.date().isoformat(),
                                display_format="YYYY-MM-DD", first_day_of_week=1,
                                clearable=False)


def month_marks():
    months = pd.date_range(START, END, freq="MS")
    marks = {(m - START).days: m.strftime("%b '%y") for m in months[::2]}
    marks[N_DAYS - 1] = END.strftime("%d %b")
    return marks


MAX_LABELS, TOP_LABELS = 30, 15  # coin scatter: label all up to 30 coins, else the 15 most extreme
METRIC_OPTIONS = [{"label": label, "value": key} for key, (label, _) in xs.METRICS.items()]


def layout():
    return html.Div([
        html.Section(className="card", children=[
            card_head("Spread between coins", "spread-coins"),
            html.Div(className="controls", children=[
                html.Div([html.Label("Coin A (long)"), dcc.Dropdown(id="leg-a", clearable=False)],
                         className="control leg"),
                html.Div([html.Label("Coin B (short)"), dcc.Dropdown(id="leg-b", clearable=False)],
                         className="control leg"),
                html.Div([html.Label("Hedge"),
                          dcc.RadioItems([{"label": "1 : 1 (A − B)", "value": "equal"},
                                          {"label": "β-hedged (A − β·B)", "value": "beta"}],
                                         "equal", id="spread-hedge", inline=True, className="radio")],
                         className="control"),
            ]),
            loading(dcc.Markdown(id="spread-equation", className="equation", mathjax=True),
                    html.P(id="spread-stats", className="stats"),
                    dcc.Graph(id="spread-graph", config={"displaylogo": False})),
        ]),
        *regression.layout(
            "pair-mf", "Multi-factor regression on the spread (two steps)", ["market"],
            ["sector", "volume"],
            note=("Target: the spread chosen above (same coins, hedge and frequency). "
                  "Coin-specific factors enter as the difference between the two coins (A − B); "
                  "market, BTC and ETH are identical for both coins, so they enter as they are.")),
        html.Section(className="card", children=[
            card_head("Coin scatter: one dot per coin", "xs-universe"),
            html.Div(className="controls", children=[
                html.Div([html.Label("Start date"), date_picker("xs-start", START)],
                         className="control"),
                html.Div([html.Label("End date"), date_picker("xs-end", END)],
                         className="control"),
                html.Div([html.Button("Full period", id="xs-full", n_clicks=0,
                                      className="btn-secondary")], className="control"),
            ]),
            html.Div(className="controls", children=[
                html.Div([html.Label("Selected period within the available data (set it with the "
                                     "dates above; metrics are computed over this period)"),
                          dcc.RangeSlider(0, N_DAYS - 1, 1, value=[0, N_DAYS - 1], id="xs-window",
                                          marks=month_marks(), disabled=True,
                                          allow_direct_input=False)],
                         className="control full window-bar"),
            ]),
            html.Div(className="controls", children=[
                html.Div([html.Label("X axis"),
                          dcc.Dropdown(METRIC_OPTIONS, "buyback_yield", id="xs-x", clearable=False)],
                         className="control wide"),
                html.Div([html.Label("Y axis"),
                          dcc.Dropdown(METRIC_OPTIONS, "return", id="xs-y", clearable=False)],
                         className="control wide"),
                html.Div([html.Label("Fit line"),
                          dcc.RadioItems([{"label": "All coins", "value": "all"},
                                          {"label": "Per category", "value": "category"},
                                          {"label": "None", "value": "none"}],
                                         "all", id="xs-fit", inline=True, className="radio")],
                         className="control"),
            ]),
            html.P(id="xs-window-text", className="stats"),
            loading(html.P(id="xs-stats", className="stats"),
                    dcc.Graph(id="xs-graph", config={"displaylogo": False})),
        ]),
        html.Section(className="card", children=[
            card_head("Coin metrics", "xs-table-universe"),
            loading(html.P(id="xs-table-caption", className="stats"), dash_table.DataTable(
                id="xs-table",
                columns=[{"name": "Token", "id": "symbol"}, {"name": "Name", "id": "name"},
                         {"name": "Category", "id": "category"}] + [
                    {"name": label, "id": key, "type": "numeric",
                     "format": {"specifier": ".3f" if unit != "pct" else ".2f"}}
                    for key, (label, unit) in xs.METRICS.items()],
                **TABLE_STYLE,
                style_cell_conditional=left_align("symbol", "name", "category"),
                style_header_conditional=[{"if": {"column_id": k}, "whiteSpace": "normal",
                                           "minWidth": "90px"} for k in xs.METRICS],
            )),
        ]),
        html.Section(className="card", children=[
            card_head("Return correlations", "corr-universe"),
            loading(html.P(id="corr-stats", className="stats"),
                    dcc.Graph(id="corr-graph", config={"displaylogo": False})),
        ]),
    ])


# ---------------------------------------------------------------- spread

@callback(Output("leg-a", "options"), Output("leg-b", "options"),
          Output("leg-a", "value"), Output("leg-b", "value"),
          Input("universe-ids", "data"), State("leg-a", "value"), State("leg-b", "value"))
def leg_choices(ids, a, b):
    """Legs can only come from the coin universe; keep current picks that are still allowed,
    and fill the rest with the largest remaining coins (A defaults to the 2nd largest so the
    default pair is e.g. ETH − BTC)."""
    if not ids:
        return [], [], None, None
    opts = coin_options(ids)
    keep = [v if v in ids else None for v in (a, b)]
    defaults = [x for x in dict.fromkeys(([ids[1]] if len(ids) > 1 else []) + ids) if x not in keep]
    picks = [v if v is not None else defaults.pop(0) if defaults else ids[0] for v in keep]
    return opts, opts, *picks


def spread_latex(syms, h):
    r = lambda sym: rf"r^{{{regression.tex_sym(sym)}}}_t"  # noqa: E731
    coef = "" if abs(h - 1) < 1e-12 else rf"{tex_num(h, 3)}\,"
    return rf"$$s_t = {r(syms[0])} - {coef}{r(syms[1])}$$"


@callback(Output("spread-graph", "figure"), Output("spread-equation", "children"),
          Output("spread-stats", "children"), Output("spread-coins", "children"),
          Input("spread-hedge", "value"), Input("leg-a", "value"), Input("leg-b", "value"),
          Input("freq", "value"))
def spread_chart(hedge, a, b, freq):
    if not (a and b):
        raise PreventUpdate
    ids = [a, b]
    syms = [INFO.at[i, "symbol"] for i in ids]
    badge = [html.B(" − ".join(syms))]
    if a == b:
        fig = go.Figure().update_layout(base_layout(height=200))
        return fig, "", "Pick two different coins.", badge

    frame, h, beta = xs.spread(ids, freq, hedge)
    period = analytics.PERIOD_NAMES[freq]
    if len(frame) < 2:
        return go.Figure().update_layout(base_layout(height=200)), "", \
            "Not enough overlapping history for these coins.", badge
    cum = frame.cumsum()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32],
                        vertical_spacing=0.06)
    for i, (cid, sym) in enumerate(zip(ids, syms)):
        fig.add_scatter(x=cum.index, y=cum[cid], name=f"{sym} (cumulative log return)", mode="lines",
                        line=dict(color=LEG_COLORS[i], width=1.5), opacity=0.8, row=1, col=1,
                        hovertemplate="%{y:.3f}<extra>" + sym + "</extra>")
    fig.add_scatter(x=cum.index, y=cum["spread"], name="Spread s (cumulative)", mode="lines",
                    line=dict(color=INK, width=2.5), row=1, col=1,
                    hovertemplate="%{y:.3f}<extra>Spread</extra>")
    s = frame["spread"]
    fig.add_bar(x=s.index, y=s, name=f"Spread log return per {period}", row=2, col=1,
                marker_color=np.where(s >= 0, POS, NEG), showlegend=False,
                hovertemplate="%{y:.4f}<extra>Spread return</extra>")
    fig.update_layout(base_layout(height=560, hovermode="x unified", bargap=0.15,
                                  legend=dict(orientation="h", y=1.1, x=0, font_color=MUTED)))
    fig.update_xaxes(gridcolor=GRID, tickfont_color=MUTED)
    fig.update_yaxes(axis("Cumulative log return (start = 0)", zeroline=True,
                          zerolinecolor="#c3c2b7"), row=1, col=1)
    fig.update_yaxes(axis(f"Spread return per {period}", zeroline=True, zerolinecolor="#c3c2b7"),
                     row=2, col=1)

    st = xs.spread_stats(frame, ids, freq)
    parts = [f"n = {st['n']} {period}s ({frame.index[0]:%Y-%m-%d} → {frame.index[-1]:%Y-%m-%d})",
             f"total spread = {st['total']:+.3f} (log)",
             f"annualized spread vol = {st['vol']:.1%}",
             f"mean per {period} = {st['mean']:+.4f} (t = {st['t']:.2f})",
             f"correlation of A and B returns = {st['corr']:.2f}"]
    if beta is not None:
        parts.append(f"hedge ratio β = {beta:.3f} (fitted on the whole period, so in-sample)")
    return fig, spread_latex(syms, h), " · ".join(parts), badge


regression.register("pair-mf", [Input("leg-a", "value"), Input("leg-b", "value"),
                           Input("spread-hedge", "value")], regression.pair_builder)


# ---------------------------------------------------------------- coin scatter

def universe_badge(ids, categories, bb_only):
    label = ", ".join(categories) if categories else "All categories"
    if "bb" in (bb_only or []):
        label += " · buyback coins only"
    if "rev" in (bb_only or []):
        label += " · revenue coins only"
    return [html.B(label), f" · {len(ids)} coins"]


def window_dates(start, end):
    """Picked dates as Timestamps, clamped to the data and put in order.
    Returns (start, end, swapped)."""
    s = min(max(pd.Timestamp(start or START), START), END)
    e = min(max(pd.Timestamp(end or END), START), END)
    return (e, s, True) if s > e else (s, e, False)


@callback(Output("xs-window", "value"), Input("xs-start", "date"), Input("xs-end", "date"))
def window_bar(start, end):
    """The read-only bar shows the picked period against the full data horizon."""
    s, e, _ = window_dates(start, end)
    return [(s - START).days, (e - START).days]


@callback(Output("xs-start", "date"), Output("xs-end", "date"), Input("xs-full", "n_clicks"),
          prevent_initial_call=True)
def full_period(_):
    return START.date().isoformat(), END.date().isoformat()


def fmt_metric(key, v):
    unit = xs.METRICS[key][1]
    return f"{v:.2f}%" if unit == "pct" else f"{v:.3f}"


def axis_fmt(key):
    unit = xs.METRICS[key][1]
    return dict(ticksuffix="%") if unit == "pct" else {}


@callback(Output("xs-graph", "figure"), Output("xs-stats", "children"),
          Output("xs-window-text", "children"), Output("xs-universe", "children"),
          Input("universe-ids", "data"), Input("xs-start", "date"), Input("xs-end", "date"), Input("xs-x", "value"),
          Input("xs-y", "value"), Input("xs-fit", "value"),
          Input("freq", "value"), Input("market-n", "value"),
          State("categories", "value"), State("bb-only", "value"))
def coin_scatter(ids, start_d, end_d, x_key, y_key, fit_mode, freq, n, categories, bb_only):
    if ids is None:
        raise PreventUpdate
    start, end, swapped = window_dates(start_d, end_d)
    badge = universe_badge(ids or [], categories, bb_only)
    window_text = (f"Window: {start:%Y-%m-%d} → {end:%Y-%m-%d} ({(end - start).days + 1} days), "
                   f"{analytics.FREQ_NAMES[freq]} returns for volatility / beta / correlation.")
    if swapped:
        window_text += " (The start date was after the end date, so the two were swapped.)"
    metrics = xs.coin_metrics(start, end, freq, n).loc[ids]
    x, y = metrics[x_key], metrics[y_key]

    d = pd.DataFrame({"x": x, "y": y, "category": xs.categories().reindex(ids)}).dropna()
    missing = len(ids) - len(d)
    x_label, y_label = xs.METRICS[x_key][0], xs.METRICS[y_key][0]

    # Label every coin when there are few; otherwise only the most extreme ones (hover
    # still identifies any dot), so labels don't pile up in dense clusters.
    if len(d) <= MAX_LABELS:
        labeled = set(d.index)
    else:
        z = (d[["x", "y"]] - d[["x", "y"]].median()) / d[["x", "y"]].std().replace(0, 1)
        labeled = set((z ** 2).sum(axis=1).nlargest(TOP_LABELS).index)

    fig = go.Figure()
    for cat in CATEGORY_STYLE:
        g = d[d["category"] == cat]
        if g.empty:
            continue
        color, symbol = CATEGORY_STYLE[cat]
        syms = INFO.loc[g.index, "symbol"]
        fig.add_scatter(
            x=g["x"], y=g["y"], mode="markers+text", name=cat, hovertext=syms,
            text=[sym if cid in labeled else "" for cid, sym in syms.items()], cliponaxis=False,
            textposition="top center", textfont=dict(size=10, color=MUTED),
            marker=dict(color=color, symbol=symbol, size=11, line=dict(color="white", width=1.5)),
            customdata=np.stack([INFO.loc[g.index, "name"], [fmt_metric(x_key, v) for v in g["x"]],
                                 [fmt_metric(y_key, v) for v in g["y"]]], axis=-1),
            hovertemplate=(f"<b>%{{hovertext}}</b> · %{{customdata[0]}}<br>{cat}<br>"
                           f"x: %{{customdata[1]}}<br>y: %{{customdata[2]}}<extra></extra>"))

    stats_text = f"{len(d)} coins shown"
    if missing:
        stats_text += f" ({missing} without data for these metrics, e.g. no buyback data, not shown)"
    if (end - start).days + 1 < analytics.MIN_OBS * {"D": 1, "W": 7, "M": 30}[freq]:
        stats_text += (f" · The window is short for {analytics.FREQ_NAMES[freq]} data: beta, "
                       f"alpha and correlation need at least {analytics.MIN_OBS} periods.")
    groups = [("All coins", d)] if fit_mode == "all" else \
        list(d.groupby("category", sort=False)) if fit_mode == "category" else []
    fits = []
    for name, g in groups:
        if len(g) < 3 or g["x"].std() == 0:
            continue
        f = stats.linregress(g["x"], g["y"])
        xs_line = np.linspace(g["x"].min(), g["x"].max(), 20)
        color = INK if name == "All coins" else CATEGORY_STYLE[name][0]
        fig.add_scatter(x=xs_line, y=f.intercept + f.slope * xs_line, mode="lines",
                        line=dict(color=color, width=2, dash="dash"), hoverinfo="skip",
                        name=f"OLS fit: {name}", showlegend=name == "All coins")
        fits.append(f"{name}: slope {f.slope:.3g} (p = {f.pvalue:.2g}, n = {len(g)})")
    if len(d) > MAX_LABELS:
        stats_text += f"; labels on the {TOP_LABELS} most extreme, hover for the rest"
    if len(d) >= 3:
        rho = stats.spearmanr(d["x"], d["y"]).statistic
        stats_text += f" · Spearman ρ (all) = {rho:.2f}"
    if fits:
        stats_text += " · " + " · ".join(fits)

    fig.update_layout(base_layout(
        height=560, hovermode="closest", margin=dict(l=60, r=20, t=20, b=50),
        legend=dict(orientation="v", y=1, x=1.02, yanchor="top", font_color=MUTED,
                    title=dict(text="Category", font_color=MUTED)),
        xaxis=axis(x_label, zeroline=True, zerolinecolor="#c3c2b7", **axis_fmt(x_key)),
        yaxis=axis(y_label, zeroline=True, zerolinecolor="#c3c2b7", **axis_fmt(y_key)),
    ))
    if d.empty:
        fig.add_annotation(text="No coins with data for these metrics in this universe",
                           showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                           font_color=MUTED)
    return fig, stats_text, window_text, badge


@callback(Output("xs-table", "data"), Output("xs-table-caption", "children"),
          Output("xs-table-universe", "children"),
          Input("universe-ids", "data"), Input("xs-start", "date"), Input("xs-end", "date"), Input("freq", "value"),
          Input("market-n", "value"), State("categories", "value"), State("bb-only", "value"))
def metrics_table(ids, start_d, end_d, freq, n, categories, bb_only):
    if ids is None:
        raise PreventUpdate
    start, end, swapped = window_dates(start_d, end_d)
    t = xs.coin_metrics(start, end, freq, n).loc[ids].reset_index()
    caption = (f"Window {start:%Y-%m-%d} → {end:%Y-%m-%d}, {analytics.FREQ_NAMES[freq]} returns, "
               f"market = top {n}. Percent columns are in %. Market cap and supply are today's "
               f"values. Click a header to sort.")
    return t.round(6).to_dict("records"), caption, universe_badge(ids, categories, bb_only)


# ---------------------------------------------------------------- correlations

@callback(Output("corr-graph", "figure"), Output("corr-stats", "children"),
          Output("corr-universe", "children"),
          Input("universe-ids", "data"), Input("xs-start", "date"), Input("xs-end", "date"), Input("freq", "value"),
          State("categories", "value"), State("bb-only", "value"))
def correlations(ids, start_d, end_d, freq, categories, bb_only):
    if ids is None:
        raise PreventUpdate
    badge = universe_badge(ids, categories, bb_only)
    if len(ids) < 2:
        return go.Figure().update_layout(base_layout(height=200)), \
            "Select at least two coins.", badge
    start, end, swapped = window_dates(start_d, end_d)
    cats = xs.categories()
    # Group coins by category (config order), market cap within a category.
    order = sorted(ids, key=lambda i: (xs.category_order().index(cats[i]), ids.index(i)))
    c = xs.correlation_matrix(order, start, end, freq)
    syms = list(INFO.loc[order, "symbol"])
    cat_of = [cats[i] for i in order]
    text = [[f"{syms[i]} · {syms[j]}<br>{cat_of[i]} · {cat_of[j]}" for j in range(len(order))]
            for i in range(len(order))]
    fig = go.Figure(go.Heatmap(
        z=c.values, x=syms, y=syms, zmin=-1, zmax=1, zmid=0, colorscale=DIVERGING,
        text=text, hovertemplate="%{text}<br>ρ = %{z:.2f}<extra></extra>",
        colorbar=dict(title=dict(text="ρ", font_color=MUTED), tickfont_color=MUTED),
        texttemplate="%{z:.2f}" if len(order) <= 20 else None, xgap=1, ygap=1))
    # Thin lines between category blocks.
    edges = [i for i in range(1, len(order)) if cat_of[i] != cat_of[i - 1]]
    for e in edges:
        for kw in (dict(x0=e - 0.5, x1=e - 0.5, y0=-0.5, y1=len(order) - 0.5),
                   dict(y0=e - 0.5, y1=e - 0.5, x0=-0.5, x1=len(order) - 0.5)):
            fig.add_shape(type="line", line=dict(color=INK, width=1), **kw)
    size = min(max(380, 18 * len(order) + 160), 1100)
    fig.update_layout(base_layout(height=size, margin=dict(l=70, r=30, t=20, b=70)))
    fig.update_xaxes(tickangle=-90, tickfont=dict(size=10, color=MUTED), showgrid=False)
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10, color=MUTED), showgrid=False)

    # Average correlation within the same category vs across categories.
    vals = c.values
    same, diff = [], []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            if np.isfinite(vals[i, j]):
                (same if cat_of[i] == cat_of[j] else diff).append(vals[i, j])
    parts = [f"{len(order)} coins, {analytics.FREQ_NAMES[freq]} log returns, "
             f"{start:%Y-%m-%d} → {end:%Y-%m-%d}, grouped by category (lines mark the groups)"]
    if same:
        parts.append(f"average ρ within a category = {np.mean(same):.2f}")
    if diff:
        parts.append(f"across categories = {np.mean(diff):.2f}")
    return fig, " · ".join(parts), badge
