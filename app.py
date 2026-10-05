"""Dash app: token prices vs buybacks, volume and the market, with two pages.

- Individual (/): one token's time series, return scatter and beta ranking.
- Cross-Section (/cross-section): spreads between coins, coin-level scatter, metrics table,
  correlations.

The sticky top bar holds the global controls shared by both pages: the coin universe
(category filter), frequency and the market factor n.

Run `python fetch_data.py` first, then `python app.py` and open http://127.0.0.1:8050
"""
from dash import Dash, Input, Output, callback, clientside_callback, dcc, html

import cross_section as xs
from views import cross_section, individual
from views.common import CATEGORY_ORDER, meta, universe

N_MAX = len(universe)
N_DEFAULT = min(10, N_MAX)
CATEGORY_COUNTS = xs.categories().value_counts()
PAGES = [("Individual", "/"), ("Cross-Section", "/cross-section")]

app = Dash(__name__, title="Token Factor Dashboard")
server = app.server  # WSGI entry point for gunicorn (e.g. on Render)

app.layout = html.Div([
    dcc.Location(id="url"),
    dcc.Store(id="universe-ids"),
    html.Div(className="topbar", children=[html.Div(className="topbar-inner", children=[
        html.Div(className="brand-row", children=[
            html.H1("Token Factor Dashboard"),
            html.Nav([dcc.Link(name, href=href, id=f"nav-{i}", className="nav-link")
                      for i, (name, href) in enumerate(PAGES)], className="nav"),
        ]),
        html.Div(className="controls global-controls", children=[
            html.Div([html.Label("Coin universe: categories (none selected = all)"),
                      dcc.Dropdown([{"label": f"{c} ({CATEGORY_COUNTS[c]})", "value": c}
                                    for c in CATEGORY_ORDER],
                                   [], id="categories", multi=True,
                                   placeholder=f"All categories ({N_MAX} coins)")],
                     className="control wide universe"),
            html.Div([dcc.Checklist([{"label": "Buyback data only", "value": "bb"},
                                     {"label": "Revenue data only", "value": "rev"}], [],
                                    id="bb-only", className="checklist"),
                      html.Div(id="universe-count", className="universe-count")],
                     className="control"),
            html.Div([html.Label("Frequency"),
                      dcc.RadioItems([{"label": "Daily", "value": "D"},
                                      {"label": "Weekly", "value": "W"},
                                      {"label": "Monthly", "value": "M"}],
                                     "D", id="freq", inline=True, className="radio")],
                     className="control"),
            html.Div([html.Label("Market factor: equal-weighted average of top n coins"),
                      dcc.Slider(1, N_MAX, 1, value=N_DEFAULT, id="market-n",
                                 marks={i: str(i) for i in sorted({1, *range(10, N_MAX + 1, 10), N_MAX})})],
                     className="control slider"),
        ]),
    ])]),
    html.Div(className="page", children=[
        html.P(className="subtitle", children=(
            f"{N_MAX} tokens: top {meta.get('n_top', 50)} by market cap (ex. stablecoins, BNB, "
            f"wrapped/tokenized assets) plus the largest coins with buybacks, "
            f"{int(universe['has_buybacks'].sum())} with buyback data · last {meta['days']} days "
            f"· data fetched {meta['fetched_at_utc']}")),
        # Both pages stay mounted (only hidden), so each keeps its settings when you switch.
        html.Div(individual.layout(), id="page-individual"),
        html.Div(cross_section.layout(), id="page-cross-section", style={"display": "none"}),
        html.Footer(className="credits", children=html.P([
            "Data: ", html.A("CoinGecko", href="https://www.coingecko.com"),
            " (market data, categories), ", html.A("Binance", href="https://www.binance.com"),
            " (prices & volume), ", html.A("DeFiLlama", href="https://defillama.com"),
            " (holders revenue). All dates are UTC."])),
    ]),
])

# Page switching runs in the browser: show one page, mark its nav link active.
clientside_callback(
    """
    function(path) {
        const cross = (path || "").replace(/\\/$/, "").endsWith("/cross-section");
        const show = {}, hide = {display: "none"};
        return [cross ? hide : show, cross ? show : hide,
                cross ? "nav-link" : "nav-link active", cross ? "nav-link active" : "nav-link"];
    }
    """,
    Output("page-individual", "style"), Output("page-cross-section", "style"),
    Output("nav-0", "className"), Output("nav-1", "className"),
    Input("url", "pathname"),
)


@callback(Output("universe-ids", "data"), Output("universe-count", "children"),
          Input("categories", "value"), Input("bb-only", "value"))
def coin_universe(categories, bb_only):
    """Coin ids (market-cap order) allowed by the global filter; both pages read this."""
    ids = xs.filter_ids(categories, "bb" in (bb_only or []), "rev" in (bb_only or []))
    return ids, f"{len(ids)} of {N_MAX} coins selected"


if __name__ == "__main__":
    app.run(debug=False)
