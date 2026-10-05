"""Individual page: one token's price vs buybacks, volume and the market."""
import numpy as np
import plotly.graph_objects as go
from dash import Input, Output, State, callback, clientside_callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate
from scipy import stats

import analytics
import cross_section as xs
import factors
from views import regression
from views.common import (COLORS, INK, INFO, MUTED, TABLE_STYLE, axis, base_layout, card_head,
                          coin_options, left_align, loading, tex_num, token_badge, universe)

TS_SERIES = [
    {"label": "Price (USD)", "value": "price"},
    {"label": "Trading volume (USD)", "value": "volume_usd"},
    {"label": "Cumulative buyback, % of total supply", "value": "buyback_pct_supply"},
    {"label": "Cumulative revenue, % of total supply", "value": "revenue_pct_supply"},
    {"label": "Market index (top n, start of data = 100)", "value": "market"},
    {"label": "Sector index (category peers, start of data = 100)", "value": "sector"},
]
SCATTER_X = [
    {"label": "Buyback, % of total supply (daily)", "value": "buyback_pct_supply"},
    {"label": "Buyback value (USD)", "value": "buyback_usd"},
    {"label": "Revenue, % of total supply (daily)", "value": "revenue_pct_supply"},
    {"label": "Revenue (USD)", "value": "revenue_usd"},
    {"label": "Trading volume (USD)", "value": "volume_usd"},
    {"label": "Market (top n by market cap)", "value": "market"},
]
EVENT_TARGETS = [
    {"label": "None", "value": "none"},
    {"label": "Buyback, % of total supply (daily)", "value": "buyback_pct_supply"},
    {"label": "Buyback value (USD)", "value": "buyback_usd"},
    {"label": "Revenue (USD)", "value": "revenue_usd"},
    {"label": "Trading volume (USD)", "value": "volume_usd"},
    {"label": "Price (USD)", "value": "price"},
    {"label": "Price log return", "value": "price_return"},
    {"label": "Market log return (top n)", "value": "market_return"},
]
KINDS = [{"label": "Level", "value": "level"}, {"label": "Log return", "value": "log"},
         {"label": "Return", "value": "simple"}]
KIND_NAMES = {"level": "Level", "log": "Log return", "simple": "Return"}
BUYBACK_COLS = set(analytics.BUYBACK_COLS)
REVENUE_COLS = set(analytics.REVENUE_COLS)
FLOW_COLS = BUYBACK_COLS | REVENUE_COLS  # NaN for tokens without the data; returns use 1 + x
LABELS = {o["value"]: o["label"] for o in SCATTER_X}

default_token = next((r.id for r in universe.itertuples() if r.has_buybacks), universe["id"][0])


def layout():
    return html.Div([
        html.Div(className="controls page-controls", children=[
            html.Div([html.Label("Token"),
                      dcc.Dropdown(coin_options(list(universe["id"]), note_buybacks=True),
                                   default_token, id="token", clearable=False)],
                     className="control wide"),
        ]),
    html.Section(className="card", children=[
        card_head("Abnormal events", "events-token"),
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
        card_head("Time series", "ts-token"),
        dcc.Checklist(TS_SERIES, ["price", "volume_usd", "buyback_pct_supply", "revenue_pct_supply",
                                  "market", "sector"],
                      id="ts-series",
                      inline=True, className="checklist"),
        loading(dcc.Graph(id="ts-graph", config={"displaylogo": False})),
    ]),
    html.Section(className="card", children=[
        card_head("Scatter", "scatter-token"),
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
        loading(dcc.Markdown(id="scatter-equation", className="equation", mathjax=True),
                html.P(id="scatter-stats", className="stats"),
                dcc.Graph(id="scatter-graph", config={"displaylogo": False})),
    ]),
    html.Section(className="card", children=[
        card_head("Beta ranking", "beta-token"),
        loading(html.P(id="beta-caption", className="stats"), dash_table.DataTable(
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
            **TABLE_STYLE,
            style_cell_conditional=left_align("symbol", "name", "segment", "buybacks"),
        )),
    ]),
        *regression.layout("token-mf", "Multi-factor regression (two steps)", ["market"],
                           ["sector", "volume", "buyback_pct_supply"]),
        html.Footer(id="footer"),
        dcc.Store(id="buyback-toggle-data", data=BUYBACK_TOGGLE_DATA),
    ])


@callback(Output("token", "options"), Output("token", "value"),
          Input("universe-ids", "data"), State("token", "value"))
def token_choices(ids, current):
    """Only coins in the selected universe can be picked; keep the current one if allowed."""
    if not ids:
        return [], None
    return coin_options(ids, note_buybacks=True), current if current in ids else ids[0]


# Data for the buyback-option toggle. It is passed in through a Store rather than written
# into the JavaScript below: Dash names clientside functions by a hash of their source, so
# baking data in would rename the function whenever an option changes, and a page that
# hot-reloaded mid-session would then call a function it doesn't have.
BUYBACK_TOGGLE_DATA = {
    "withBuybacks": sorted(universe.loc[universe["has_buybacks"], "id"]),
    "buybackKeys": sorted(BUYBACK_COLS),
    "withRevenue": sorted(universe.loc[universe["has_revenue"], "id"]),
    "revenueKeys": sorted(REVENUE_COLS),
    "options": [TS_SERIES, SCATTER_X, EVENT_TARGETS],
}

# Runs in the browser (no server round trip), so the chart callbacks that depend on these
# options start at once and show their loading state immediately after a token change.
clientside_callback(
    """
    function(tokenId, tsValue, sxValue, evValue, d) {
        // Keys this token has no data for: buyback and/or revenue columns.
        const off = [...(d.withBuybacks.includes(tokenId) ? [] : d.buybackKeys),
                     ...(d.withRevenue.includes(tokenId) ? [] : d.revenueKeys)];
        const disable = opts => opts.map(o => ({...o, disabled: off.includes(o.value)}));
        tsValue = tsValue.filter(v => !off.includes(v));
        if (off.includes(sxValue)) sxValue = "volume_usd";
        if (off.includes(evValue)) evValue = "none";
        const [ts, sx, ev] = d.options.map(disable);
        return [ts, tsValue, sx, sxValue, ev, evValue];
    }
    """,
    Output("ts-series", "options"), Output("ts-series", "value"),
    Output("scatter-x", "options"), Output("scatter-x", "value"),
    Output("event-target", "options"), Output("event-target", "value"),
    Input("token", "value"),
    State("ts-series", "value"), State("scatter-x", "value"), State("event-target", "value"),
    State("buyback-toggle-data", "data"),
)


def event_target_series(daily, target, n):
    if target == "price_return":
        return analytics.returns(daily["price"], "log")
    if target == "market_return":
        return analytics.market_returns(n, "D", "log").reindex(daily.index)
    return daily[target]


def event_flags(daily, target, n, pct, lookback):
    """Daily boolean event flags for the selected target, or None when off."""
    if target == "none" or (target in FLOW_COLS and daily[target].isna().all()):
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


@callback(Output("ts-graph", "figure"), Output("event-summary", "children"),
              Output("ts-token", "children"), Output("events-token", "children"),
              Input("token", "value"), Input("freq", "value"), Input("ts-series", "value"),
              Input("market-n", "value"), Input("event-target", "value"),
              Input("event-pct", "value"), Input("event-lookback", "value"))
def time_series(token_id, freq, series, n, target, pct, lookback):
    if not token_id:  # empty universe selection
        raise PreventUpdate
    daily = analytics.load_token(token_id)
    df = analytics.resample(daily, freq)
    sym = INFO.at[token_id, "symbol"]
    fig = go.Figure()
    period = analytics.PERIOD_NAMES[freq]

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
    if "sector" in series:
        # Same scale as the market index: equal-weighted category peers (token excluded).
        cat = xs.categories()[token_id]
        peers = factors.sector_peers(token_id)
        if peers:
            fig.add_scatter(x=df.index, y=factors.sector_index(token_id, freq).reindex(df.index),
                            name=f"Sector index: {cat}, {len(peers)} peers (start = 100)",
                            mode="lines", line=dict(color=COLORS["sector"], width=2, dash="dash"),
                            yaxis="y4", hovertemplate="%{y:.1f}<extra>Sector</extra>")
    if "buyback_pct_supply" in series and df["buyback_pct_supply"].notna().any():
        fig.add_scatter(x=df.index, y=df["buyback_pct_supply"].cumsum(),
                        name="Cumulative buyback (% of supply)", mode="lines",
                        line=dict(color=COLORS["buyback"], width=2), yaxis="y3",
                        hovertemplate="%{y:.4f}%<extra>Buyback</extra>")
    if "revenue_pct_supply" in series and df["revenue_pct_supply"].notna().any():
        # Same unit as buybacks: holders revenue (buybacks) is a subset of revenue.
        fig.add_scatter(x=df.index, y=df["revenue_pct_supply"].cumsum(),
                        name="Cumulative revenue (% of supply)", mode="lines",
                        line=dict(color=COLORS["revenue"], width=2, dash="dash"), yaxis="y3",
                        hovertemplate="%{y:.4f}%<extra>Revenue</extra>")

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
    # The index axis is shared by the market and sector indices; color it like its series
    # when only one is shown.
    shown = [k for k in ("market", "sector") if k in series]
    index_title = {("market",): "Market index (start = 100)",
                   ("sector",): "Sector index (start = 100)"}.get(tuple(shown), "Index (start = 100)")
    index_color = COLORS[shown[0]] if len(shown) == 1 else MUTED
    flows = [k for k in ("buyback_pct_supply", "revenue_pct_supply")
             if k in series and df[k].notna().any()]
    flow_title = {("buyback_pct_supply",): "Cum. buyback (% supply)",
                  ("revenue_pct_supply",): "Cum. revenue (% supply)"}.get(tuple(flows),
                                                                         "Cumulative % of supply")
    flow_color = {("buyback_pct_supply",): COLORS["buyback"],
                  ("revenue_pct_supply",): COLORS["revenue"]}.get(tuple(flows), MUTED)
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
        yaxis3=axis(flow_title, flow_color, overlaying="y", side="right",
                    ticksuffix="%", showgrid=False, anchor="free", position=0.97),
        yaxis4=axis(index_title, index_color, overlaying="y", side="left",
                    showgrid=False, anchor="free", position=0),
    ))
    if not series:
        fig.add_annotation(text="Select at least one series", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5, font_color=MUTED)
    return fig, summary, token_badge(token_id), token_badge(token_id)


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
               "buyback_pct_supply": r"S^{\%}", "revenue_usd": r"R^{\text{USD}}",
               "revenue_pct_supply": r"R^{\%}"}[x_col]
        x = latex_side(var, x_kind, add_one=x_col in FLOW_COLS)
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
    if kind != "level" and col in FLOW_COLS:
        label = f"1 + {label}"
    if kind == "log":
        return f"{name}: {label}", dict(), ".3f"
    if kind == "simple":
        return f"{name}: {label}", dict(tickformat=".0%"), ".2%"
    usd = col in ("price", "volume_usd", "buyback_usd", "revenue_usd")
    return (f"{name}: {label}", dict(tickprefix="$") if usd else
            dict(ticksuffix="%") if col in ("buyback_pct_supply", "revenue_pct_supply") else dict(),
            ",.4~g")


@callback(Output("scatter-graph", "figure"), Output("scatter-equation", "children"),
              Output("scatter-stats", "children"), Output("scatter-token", "children"),
              Input("token", "value"), Input("freq", "value"), Input("scatter-x", "value"),
              Input("x-kind", "value"), Input("y-kind", "value"), Input("market-n", "value"),
              Input("event-target", "value"), Input("event-pct", "value"),
              Input("event-lookback", "value"))
def scatter(token_id, freq, x_col, x_kind, y_kind, n, target, pct, lookback):
    if not token_id:  # empty universe selection
        raise PreventUpdate
    daily = analytics.load_token(token_id)
    sym = INFO.at[token_id, "symbol"]
    d = analytics.regression_data(daily, x_col, x_kind, y_kind, freq, n)
    x_title, x_ticks, x_fmt = scatter_axis(x_kind, x_col, sym, n)
    y_title, y_ticks, y_fmt = scatter_axis(y_kind, "price", sym, n)

    flags = event_flags(daily, target, n, pct, lookback)
    if flags is None:
        d["event"] = False
    else:  # a week / month is an event period if it contains any event day
        d["event"] = analytics.resample_flags(flags, freq).reindex(d.index, fill_value=False)
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
        period = analytics.PERIOD_NAMES[freq]
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
    return fig, equation, stats_text, token_badge(token_id)


@callback(Output("beta-table", "data"), Output("beta-caption", "children"),
              Output("beta-table", "style_data_conditional"), Output("beta-token", "children"),
              Input("scatter-x", "value"), Input("x-kind", "value"), Input("y-kind", "value"),
              Input("freq", "value"), Input("market-n", "value"), Input("token", "value"),
              Input("universe-ids", "data"))
def beta_ranking(x_col, x_kind, y_kind, freq, n, token_id, ids):
    if not token_id:  # empty universe selection
        raise PreventUpdate
    table = analytics.beta_table(x_col, x_kind, y_kind, freq, n, ids)
    names = {"level": "level", "log": "log return", "simple": "return"}
    factor = f"market (top {n})" if x_col == "market" else LABELS[x_col].lower()
    caption = (f"{len(table)} tokens in the selected universe ranked by β from regressing each token's price "
               f"{names[y_kind]} on the {factor} {names[x_kind]} "
               f"({analytics.FREQ_NAMES[freq]}), same settings as the "
               f"scatter above. Click a column header to re-sort. Selected token highlighted.")
    if x_col in BUYBACK_COLS:
        caption += " Only tokens with buyback data are included."
    elif x_col in REVENUE_COLS:
        caption += " Only tokens with revenue data are included."
    highlight = [{"if": {"filter_query": f'{{id}} = "{token_id}"'},
                  "backgroundColor": "#e8f0fb", "fontWeight": 600}]
    return table.to_dict("records"), caption, highlight, token_badge(token_id, "Highlighted: ")


@callback(Output("footer", "children"), Input("token", "value"))
def footer(token_id):
    if not token_id:  # empty universe selection
        raise PreventUpdate
    r = INFO.loc[token_id]
    bb = (f"DeFiLlama holders revenue ({str(r.defillama_slugs).replace(';', ', ')}), used as the "
          f"buyback proxy — may include burns/staking distributions for some protocols."
          if r.has_buybacks else "none tracked by DeFiLlama for this token.")
    src = analytics.revenue_sources().get(token_id)
    rev = (f"DeFiLlama {src['kind']} revenue ({', '.join(src['slugs'])}). Revenue is what the "
           f"{'protocol' if src['kind'] == 'protocol' else 'chain'} keeps from fees; holders "
           f"revenue (buybacks) is the part of it paid to token holders."
           if src else "none tracked by DeFiLlama for this token.")
    return [
        html.P(f"Price & volume source: {r.price_source}"
               + (" (Binance close and USDT quote volume)." if r.price_source == "Binance"
                  else " (daily close and aggregate 24h volume across exchanges).")),
        html.P(f"Buybacks: {bb}"),
        html.P(f"Revenue: {rev}"),
        html.P("Buyback (and revenue) tokens = daily USD amount ÷ daily close. % of supply uses "
               "today's total supply from CoinGecko as the denominator (historical supply not "
               "available). Buyback and revenue returns are computed on 1 + x so zero periods stay "
               "defined. All dates are UTC."),
    ]


regression.register("token-mf", [Input("token", "value")], regression.token_builder)
