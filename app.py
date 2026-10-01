"""Dash app: token price vs buybacks & trading volume.

Run `python fetch_data.py` first, then `python app.py` and open http://127.0.0.1:8050
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dash_table, dcc, html
from scipy import stats

import analytics

COLORS = {"price": "#2a78d6", "volume": "#eb6834", "buyback": "#1baf7a", "market": "#eda100",
          "fit": "#52514e", "event": "#e34948"}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e9e8e4"

TS_SERIES = [
    {"label": "Price (USD)", "value": "price"},
    {"label": "Trading volume (USD)", "value": "volume_usd"},
    {"label": "Cumulative buyback, % of total supply", "value": "buyback_pct_supply"},
    {"label": "Market index (top n, start of data = 100)", "value": "market"},
]
SCATTER_X = [
    {"label": "Buyback, % of total supply (daily)", "value": "buyback_pct_supply"},
    {"label": "Buyback value (USD)", "value": "buyback_usd"},
    {"label": "Trading volume (USD)", "value": "volume_usd"},
    {"label": "Market (top n by market cap)", "value": "market"},
]
EVENT_TARGETS = [
    {"label": "None", "value": "none"},
    {"label": "Buyback, % of total supply (daily)", "value": "buyback_pct_supply"},
    {"label": "Buyback value (USD)", "value": "buyback_usd"},
    {"label": "Trading volume (USD)", "value": "volume_usd"},
    {"label": "Price (USD)", "value": "price"},
    {"label": "Price log return", "value": "price_return"},
    {"label": "Market log return (top n)", "value": "market_return"},
]
KINDS = [{"label": "Level", "value": "level"}, {"label": "Log return", "value": "log"},
         {"label": "Return", "value": "simple"}]
KIND_NAMES = {"level": "Level", "log": "Log return", "simple": "Return"}
BUYBACK_COLS = set(analytics.ZERO_SAFE_COLS)
LABELS = {o["value"]: o["label"] for o in SCATTER_X}

universe, _, _, meta = analytics.load_cache()
token_options = [
    {"label": f"{r.symbol} · {r.name}" + ("" if r.has_buybacks else "  (no buyback data)"),
     "value": r.id}
    for r in universe.itertuples()
]
N_MAX = len(universe)
N_DEFAULT = min(10, N_MAX)
default_token = next((r.id for r in universe.itertuples() if r.has_buybacks), universe["id"][0])


def base_layout(**kw):
    return dict(
        template="plotly_white",
        font=dict(family="Inter, system-ui, sans-serif", size=12, color=INK),
        margin=dict(l=60, r=30, t=30, b=50),
        hoverlabel=dict(bgcolor="white", font_color=INK),
        legend=dict(orientation="h", y=1.08, x=0, font_color=MUTED),
        **kw,
    )


def axis(title, color=MUTED, **kw):
    return dict({"title": dict(text=title, font_color=color), "gridcolor": GRID,
                 "zeroline": False, "tickfont_color": color}, **kw)


app = Dash(__name__, title="Token Factor Dashboard")
server = app.server  # WSGI entry point for gunicorn (e.g. on Render)
app.layout = html.Div(className="page", children=[
    html.Header([
        html.H1("Token price vs buybacks & trading volume"),
        html.P(f"{len(universe)} tokens: top {meta.get('n_top', 50)} by market cap (ex. stablecoins, "
               f"BNB, wrapped/tokenized assets) plus the largest coins with buybacks, "
               f"{int(universe['has_buybacks'].sum())} with buyback data · last {meta['days']} days "
               f"· data fetched {meta['fetched_at_utc']}"),
    ]),
    html.Div(className="controls", children=[
        html.Div([html.Label("Token"),
                  dcc.Dropdown(token_options, default_token, id="token", clearable=False)],
                 className="control wide"),
        html.Div([html.Label("Frequency"),
                  dcc.RadioItems([{"label": "Daily", "value": "D"}, {"label": "Weekly", "value": "W"}],
                                 "D", id="freq", inline=True, className="radio")],
                 className="control"),
        html.Div([html.Label("Market factor: equal-weighted average of top n coins by market cap"),
                  dcc.Slider(1, N_MAX, 1, value=N_DEFAULT, id="market-n",
                             marks={i: str(i) for i in sorted({1, *range(10, N_MAX + 1, 10), N_MAX})},
                             tooltip={"placement": "bottom", "always_visible": True})],
                 className="control slider"),
    ]),
    html.Section(className="card", children=[
        html.H2("Abnormal events"),
        html.Div(className="controls", children=[
            html.Div([html.Label("Target variable (one at a time)"),
                      dcc.RadioItems(EVENT_TARGETS, "none", id="event-target", inline=True,
                                     className="radio")],
                     className="control full"),
            html.Div([html.Label("Percentile threshold (event when value ≥ this percentile)"),
                      dcc.Slider(50, 99, 1, value=95, id="event-pct",
                                 marks={i: str(i) for i in (50, 60, 70, 80, 90, 95, 99)},
                                 tooltip={"placement": "bottom", "always_visible": True})],
                     className="control slider"),
            html.Div([html.Label("Lookback window (days, incl. current day)"),
                      dcc.Slider(10, 300, 1, value=100, id="event-lookback",
                                 marks={i: str(i) for i in (10, 50, 100, 150, 200, 250, 300)},
                                 tooltip={"placement": "bottom", "always_visible": True})],
                     className="control slider"),
        ]),
        html.P(id="event-summary", className="stats"),
    ]),
    html.Section(className="card", children=[
        html.H2("Time series"),
        dcc.Checklist(TS_SERIES, ["price", "volume_usd", "buyback_pct_supply", "market"],
                      id="ts-series",
                      inline=True, className="checklist"),
        dcc.Graph(id="ts-graph", config={"displaylogo": False}),
    ]),
    html.Section(className="card", children=[
        html.H2("Scatter"),
        html.Div(className="controls", children=[
            html.Div([html.Label("X axis"),
                      dcc.Dropdown(SCATTER_X, "volume_usd", id="scatter-x", clearable=False)],
                     className="control wide"),
            html.Div([html.Label("X axis transform"),
                      dcc.RadioItems(KINDS, "log", id="x-kind", inline=True, className="radio")],
                     className="control"),
            html.Div([html.Label("Y axis (price) transform"),
                      dcc.RadioItems(KINDS, "log", id="y-kind", inline=True, className="radio")],
                     className="control"),
        ]),
        dcc.Markdown(id="scatter-equation", className="equation", mathjax=True),
        html.P(id="scatter-stats", className="stats"),
        dcc.Graph(id="scatter-graph", config={"displaylogo": False}),
    ]),
    html.Section(className="card", children=[
        html.H2("Beta ranking"),
        html.P(id="beta-caption", className="stats"),
        dash_table.DataTable(
            id="beta-table",
            columns=[
                {"name": "Rank", "id": "rank", "type": "numeric"},
                {"name": "Token", "id": "symbol"},
                {"name": "Name", "id": "name"},
                {"name": "Universe", "id": "segment"},
                {"name": "Buybacks", "id": "buybacks"},
                {"name": "β", "id": "beta", "type": "numeric", "format": {"specifier": ".4f"}},
                {"name": "β s.e.", "id": "beta_se", "type": "numeric", "format": {"specifier": ".4f"}},
                {"name": "t-stat", "id": "t", "type": "numeric", "format": {"specifier": ".2f"}},
                {"name": "p-value", "id": "p", "type": "numeric", "format": {"specifier": ".3g"}},
                {"name": "α", "id": "alpha", "type": "numeric", "format": {"specifier": ".5f"}},
                {"name": "R²", "id": "r2", "type": "numeric", "format": {"specifier": ".3f"}},
                {"name": "n", "id": "n", "type": "numeric"},
            ],
            sort_action="native",
            page_action="none",
            style_table={"maxHeight": "520px", "overflowY": "auto", "overflowX": "auto"},
            style_header={"backgroundColor": "#f4f4f2", "fontWeight": 600, "color": MUTED,
                          "border": "none", "borderBottom": f"1px solid {GRID}"},
            style_cell={"fontFamily": "Inter, system-ui, sans-serif", "fontSize": 13,
                        "padding": "6px 10px", "border": "none", "borderBottom": f"1px solid {GRID}",
                        "backgroundColor": "#fcfcfb", "color": INK, "textAlign": "right",
                        "fontVariantNumeric": "tabular-nums", "minWidth": "60px"},
            style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "left"}
                                    for c in ("symbol", "name", "segment", "buybacks")],
        ),
    ]),
    html.Footer(id="footer"),
])


@app.callback(
    Output("ts-series", "options"), Output("ts-series", "value"),
    Output("scatter-x", "options"), Output("scatter-x", "value"),
    Output("event-target", "options"), Output("event-target", "value"),
    Input("token", "value"),
    State("ts-series", "value"), State("scatter-x", "value"), State("event-target", "value"),
)
def toggle_buyback_options(token_id, ts_value, sx_value, ev_value):
    has_bb = bool(universe.set_index("id").loc[token_id, "has_buybacks"])
    disable = lambda opts: [{**o, "disabled": o["value"] in BUYBACK_COLS and not has_bb} for o in opts]
    if not has_bb:
        ts_value = [v for v in ts_value if v not in BUYBACK_COLS]
        if sx_value in BUYBACK_COLS:
            sx_value = "volume_usd"
        if ev_value in BUYBACK_COLS:
            ev_value = "none"
    return (disable(TS_SERIES), ts_value, disable(SCATTER_X), sx_value,
            disable(EVENT_TARGETS), ev_value)


def event_target_series(daily, target, n):
    if target == "price_return":
        return analytics.returns(daily["price"], "log")
    if target == "market_return":
        return analytics.market_returns(n, "D", "log").reindex(daily.index)
    return daily[target]


def event_flags(daily, target, n, pct, lookback):
    """Daily boolean event flags for the selected target, or None when off."""
    if target == "none" or (target in BUYBACK_COLS and daily[target].isna().all()):
        return None
    values = event_target_series(daily, target, n)
    return analytics.abnormal_events(values, pct, lookback)[0]


def event_label(target):
    return next(o["label"] for o in EVENT_TARGETS if o["value"] == target)


# A y-axis switches to log scale when its series spans >= LOG_RANGE_RATIO x (e.g. a
# token that rose 100x), so the line keeps a readable shape.
LOG_RANGE_RATIO = 20


def use_log_scale(series):
    s = series.dropna()
    return len(s) > 0 and s.min() > 0 and s.max() / s.min() >= LOG_RANGE_RATIO


def log_ticks(series):
    """1-2-5 tick values covering the series, for readable log-axis labels."""
    lo, hi = series.min(), series.max()
    ticks = [m * 10.0 ** e for e in range(int(np.floor(np.log10(lo))), int(np.ceil(np.log10(hi))) + 1)
             for m in (1, 2, 5)]
    return [t for t in ticks if lo / 2 <= t <= hi * 2]


@app.callback(Output("ts-graph", "figure"), Output("event-summary", "children"),
              Input("token", "value"), Input("freq", "value"), Input("ts-series", "value"),
              Input("market-n", "value"), Input("event-target", "value"),
              Input("event-pct", "value"), Input("event-lookback", "value"))
def time_series(token_id, freq, series, n, target, pct, lookback):
    daily = analytics.load_token(token_id)
    df = analytics.resample(daily, freq)
    sym = universe.set_index("id").loc[token_id, "symbol"]
    fig = go.Figure()
    period = "day" if freq == "D" else "week"

    if "volume_usd" in series:
        fig.add_bar(x=df.index, y=df["volume_usd"], name=f"Trading volume per {period}",
                    marker_color=COLORS["volume"], opacity=0.35, yaxis="y2",
                    hovertemplate="$%{y:,.3s}<extra>Volume</extra>")
    if "price" in series:
        fig.add_scatter(x=df.index, y=df["price"], name=f"{sym} price", mode="lines",
                        line=dict(color=COLORS["price"], width=2), yaxis="y",
                        hovertemplate="$%{y:,.4g}<extra>Price</extra>")
    if "market" in series:
        # Own axis, not rebased: the same date has the same value on every token's chart.
        fig.add_scatter(x=df.index, y=analytics.market_index(n, freq).reindex(df.index),
                        name=f"Market index, top {n} (start = 100)", mode="lines",
                        line=dict(color=COLORS["market"], width=2, dash="dot"), yaxis="y4",
                        hovertemplate="%{y:.1f}<extra>Market</extra>")
    if "buyback_pct_supply" in series and df["buyback_pct_supply"].notna().any():
        fig.add_scatter(x=df.index, y=df["buyback_pct_supply"].cumsum(),
                        name="Cumulative buyback (% of supply)", mode="lines",
                        line=dict(color=COLORS["buyback"], width=2), yaxis="y3",
                        hovertemplate="%{y:.4f}%<extra>Buyback</extra>")

    summary = "Select a target variable to mark abnormal events on both charts."
    flags = event_flags(daily, target, n, pct, lookback)
    if flags is not None:
        values = event_target_series(daily, target, n)
        eligible = max(int(values.notna().sum()) - lookback + 1, 0)
        summary = (f"{int(flags.sum())} abnormal days out of {eligible} eligible: "
                   f"{event_label(target)} ≥ {pct}th percentile of the trailing {lookback} days.")
        for day in flags.index[flags.to_numpy()]:
            fig.add_vline(x=day, line=dict(color=COLORS["event"], width=1), opacity=0.7)
        # Legend entry for the vertical lines (shapes have no legend of their own).
        fig.add_scatter(x=[None], y=[None], mode="lines", name="Abnormal event",
                        line=dict(color=COLORS["event"], width=1), hoverinfo="skip")

    log_price = use_log_scale(df["price"])
    fig.update_layout(base_layout(
        height=460, hovermode="x unified", bargap=0.1,
        xaxis=axis("", domain=[0.08, 0.88]),
        # Each y-axis is colored like its series.
        yaxis=axis("Price (USD, log scale)" if log_price else "Price (USD)", COLORS["price"],
                   tickprefix="$", **(dict(type="log", tickvals=(t := log_ticks(df["price"])),
                                           ticktext=[f"${v:,.10g}" for v in t])
                                      if log_price else {})),
        yaxis2=axis("Volume (USD)", COLORS["volume"], overlaying="y", side="right",
                    tickprefix="$", showgrid=False, anchor="x"),
        yaxis3=axis("Cum. buyback (% supply)", COLORS["buyback"], overlaying="y", side="right",
                    ticksuffix="%", showgrid=False, anchor="free", position=0.97),
        yaxis4=axis("Market index (start = 100)", COLORS["market"], overlaying="y", side="left",
                    showgrid=False, anchor="free", position=0),
    ))
    if not series:
        fig.add_annotation(text="Select at least one series", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5, font_color=MUTED)
    return fig, summary


def tex_num(v, digits=4):
    """Number for LaTeX: plain decimals, or a × 10^k form for very large/small values."""
    m, _, e = f"{v:.{digits}g}".partition("e")
    return rf"{m} \times 10^{{{int(e)}}}" if e else m


def latex_side(var, kind, add_one=False, idx=""):
    """LaTeX for a variable as level, log return or simple return (1+x for buybacks).

    `idx` prefixes the time subscript, e.g. "i," gives P_{i,t}.
    """
    if kind == "level":
        return f"{var}_{{{idx}t}}"
    one = "1 + " if add_one else ""
    frac = rf"\frac{{{one}{var}_{{{idx}t}}}}{{{one}{var}_{{{idx}t-1}}}}"
    return rf"\ln\!\left({frac}\right)" if kind == "log" else rf"\left({frac} - 1\right)"


def latex_equation(sym, x_col, x_kind, y_kind, n, fit):
    """Display-math LaTeX for the fitted regression, standard errors under the coefficients."""
    y = latex_side(rf"P^{{\text{{{sym}}}}}", y_kind)
    if x_col == "market" and x_kind == "level":
        x = rf"I^{{\text{{top }}{n}}}_t"
    elif x_col == "market":
        x = rf"\frac{{1}}{{{n}}}\sum_{{i=1}}^{{{n}}} " + latex_side("P", x_kind, idx="i,")
    else:
        var = {"volume_usd": "V", "buyback_usd": r"B^{\text{USD}}",
               "buyback_pct_supply": r"S^{\%}"}[x_col]
        x = latex_side(var, x_kind, add_one=x_col in BUYBACK_COLS)
    sign = "+" if fit.slope >= 0 else "-"
    alpha = rf"\underset{{({tex_num(fit.intercept_stderr)})}}{{{tex_num(fit.intercept)}}}"
    beta = rf"\underset{{({tex_num(fit.stderr)})}}{{{tex_num(abs(fit.slope))}}}"
    return f"$${y} = {alpha} {sign} {beta}\\,{x}$$"


def scatter_axis(kind, col, sym, n):
    """Title, tick format and hover format for one scatter axis."""
    name = KIND_NAMES[kind]
    label = {"price": f"{sym} price (USD)",
             "market": f"market index, top {n} (start = 100)" if kind == "level"
             else f"market (equal-weighted avg of top {n} coins)"}.get(col, LABELS.get(col))
    if kind != "level" and col in BUYBACK_COLS:
        label = f"1 + {label}"
    if kind == "log":
        return f"{name}: {label}", dict(), ".3f"
    if kind == "simple":
        return f"{name}: {label}", dict(tickformat=".0%"), ".2%"
    usd = col in ("price", "volume_usd", "buyback_usd")
    return (f"{name}: {label}", dict(tickprefix="$") if usd else
            dict(ticksuffix="%") if col == "buyback_pct_supply" else dict(), ",.4~g")


@app.callback(Output("scatter-graph", "figure"), Output("scatter-equation", "children"),
              Output("scatter-stats", "children"),
              Input("token", "value"), Input("freq", "value"), Input("scatter-x", "value"),
              Input("x-kind", "value"), Input("y-kind", "value"), Input("market-n", "value"),
              Input("event-target", "value"), Input("event-pct", "value"),
              Input("event-lookback", "value"))
def scatter(token_id, freq, x_col, x_kind, y_kind, n, target, pct, lookback):
    daily = analytics.load_token(token_id)
    sym = universe.set_index("id").loc[token_id, "symbol"]
    d = analytics.regression_data(daily, x_col, x_kind, y_kind, freq, n)
    x_title, x_ticks, x_fmt = scatter_axis(x_kind, x_col, sym, n)
    y_title, y_ticks, y_fmt = scatter_axis(y_kind, "price", sym, n)

    flags = event_flags(daily, target, n, pct, lookback)
    if flags is None:
        d["event"] = False
    elif freq == "W":  # a week is an event week if it contains any event day
        d["event"] = flags.resample("W-SUN").max().reindex(d.index, fill_value=False)
    else:
        d["event"] = flags.reindex(d.index, fill_value=False)
    d["event"] = d["event"].astype(bool)

    fig = go.Figure()
    hover = f"%{{customdata}}<br>x: %{{x:{x_fmt}}}<br>y: %{{y:{y_fmt}}}"
    normal, ev = d[~d["event"]], d[d["event"]]
    fig.add_scatter(x=normal["x"], y=normal["y"], mode="markers", name="Observations",
                    marker=dict(color=COLORS["price"], size=8, opacity=0.5,
                                line=dict(color="white", width=1)),
                    customdata=normal.index.strftime("%Y-%m-%d"),
                    hovertemplate=hover + "<extra></extra>")
    if len(ev):
        period = "week" if freq == "W" else "day"
        fig.add_scatter(x=ev["x"], y=ev["y"], mode="markers",
                        name=f"Abnormal event {period} ({len(ev)})",
                        marker=dict(color=COLORS["event"], size=11, symbol="diamond",
                                    line=dict(color="white", width=1)),
                        customdata=ev.index.strftime("%Y-%m-%d"),
                        hovertemplate=hover + "<extra>Event</extra>")
    stats_text, equation = "Not enough observations.", ""
    fit = analytics.fit(d)
    if fit is not None:
        xs = np.linspace(d["x"].min(), d["x"].max(), 50)
        fig.add_scatter(x=xs, y=fit.intercept + fit.slope * xs, mode="lines", name="OLS fit",
                        line=dict(color=COLORS["fit"], width=2, dash="dash"), hoverinfo="skip")
        rho = stats.spearmanr(d["x"], d["y"]).statistic
        equation = latex_equation(sym, x_col, x_kind, y_kind, n, fit)
        sign = "+" if fit.slope >= 0 else "−"
        fig.add_annotation(text=f"ŷ = {fit.intercept:.4g} {sign} {abs(fit.slope):.4g}x",
                           xref="paper", yref="paper", x=0.99, y=0.98, xanchor="right",
                           showarrow=False, font=dict(size=13, color=INK),
                           bgcolor="rgba(252,252,251,0.85)")
        stats_text = (f"n = {len(d)} · Pearson r = {fit.rvalue:.3f} (p = {fit.pvalue:.3g}) · "
                      f"Spearman ρ = {rho:.3f} · slope β = {fit.slope:.4g} · R² = {fit.rvalue**2:.3f}")
    fig.update_layout(base_layout(
        height=480, showlegend=True,
        xaxis=axis(x_title, zeroline=x_kind != "level", zerolinecolor="#c3c2b7", **x_ticks),
        yaxis=axis(y_title, zeroline=y_kind != "level", zerolinecolor="#c3c2b7", **y_ticks),
    ))
    return fig, equation, stats_text


@app.callback(Output("beta-table", "data"), Output("beta-caption", "children"),
              Output("beta-table", "style_data_conditional"),
              Input("scatter-x", "value"), Input("x-kind", "value"), Input("y-kind", "value"),
              Input("freq", "value"), Input("market-n", "value"), Input("token", "value"))
def beta_ranking(x_col, x_kind, y_kind, freq, n, token_id):
    table = analytics.beta_table(x_col, x_kind, y_kind, freq, n)
    names = {"level": "level", "log": "log return", "simple": "return"}
    factor = f"market (top {n})" if x_col == "market" else LABELS[x_col].lower()
    caption = (f"{len(table)} tokens ranked by β from regressing each token's price "
               f"{names[y_kind]} on the {factor} {names[x_kind]} "
               f"({'daily' if freq == 'D' else 'weekly'}), same settings as the "
               f"scatter above. Click a column header to re-sort. Selected token highlighted.")
    if x_col in BUYBACK_COLS:
        caption += " Only tokens with buyback data are included."
    highlight = [{"if": {"filter_query": f'{{id}} = "{token_id}"'},
                  "backgroundColor": "#e8f0fb", "fontWeight": 600}]
    return table.to_dict("records"), caption, highlight


@app.callback(Output("footer", "children"), Input("token", "value"))
def footer(token_id):
    r = universe.set_index("id").loc[token_id]
    bb = (f"DeFiLlama holders revenue ({str(r.defillama_slugs).replace(';', ', ')}), used as the "
          f"buyback proxy — may include burns/staking distributions for some protocols."
          if r.has_buybacks else "none tracked by DeFiLlama for this token.")
    return [
        html.P(f"Price & volume source: {r.price_source}"
               + (" (Binance close and USDT quote volume)." if r.price_source == "Binance"
                  else " (daily close and aggregate 24h volume across exchanges).")),
        html.P(f"Buybacks: {bb}"),
        html.P("Buyback tokens = daily USD buyback ÷ daily close. % of supply uses today's total "
               "supply from CoinGecko as the denominator (historical supply not available). "
               "Buyback returns are computed on 1 + x so zero-buyback periods stay defined. All dates are UTC."),
        html.P(["Data: ", html.A("CoinGecko", href="https://www.coingecko.com"), " (market data), ",
                html.A("Binance", href="https://www.binance.com"), " (prices & volume), ",
                html.A("DeFiLlama", href="https://defillama.com"), " (holders revenue)."]),
    ]


if __name__ == "__main__":
    app.run(debug=False)
