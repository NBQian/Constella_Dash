"""Two-step multi-factor regression section, shared by both pages.

Step 1 removes the factors the user wants to eliminate (OLS of the target on them, keeping
the residual e⁽¹⁾); step 2 regresses that residual on the factors to study (OLS or ridge /
lasso / elastic net), leaving the residual ε. Both steps choose from the same factor list.

`layout(prefix, ...)` builds the section with ids "<prefix>-…" and `register(prefix, ...)`
wires its callbacks to a page-specific data builder:
- Individual page: target = the token's return, factors as they are.
- Cross-Section page: target = the pair spread; coin-specific factors as A − B differences.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, clientside_callback, dash_table, dcc, html
from dash.exceptions import PreventUpdate

import analytics
import factors as F
from views.common import (DIVERGING, INFO, INK, MUTED, TABLE_STYLE, axis, base_layout, card_head,
                          left_align, loading, tex_num, token_badge, universe)

POS, NEG = "#2a78d6", "#e34948"
PATH_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7",
               "#e34948"]
PATH_DASHES = ["solid", "dash"]  # more factors than colors: dash pattern as a second cue
FACTOR_STYLE = {f: (PATH_COLORS[i % len(PATH_COLORS)], PATH_DASHES[i // len(PATH_COLORS)])
                for i, f in enumerate(F.FACTORS)}  # fixed per factor
FACTOR_OPTIONS = [{"label": label, "value": k} for k, (label, _, _) in F.FACTORS.items()]

# Flow factors each coin lacks (no buyback / revenue data); the browser disables them.
MISSING = {r.id: [f for f, (_, _, flow) in F.FACTORS.items() if flow and not (
    r.has_revenue if f in analytics.REVENUE_COLS else r.has_buybacks)]
    for r in universe.itertuples()}


def _ids(p, name):
    return f"{p}-{name}"


def layout(p, title, step1_default, step2_default, note=None):
    i = lambda name: _ids(p, name)  # noqa: E731
    return [
        html.Section(className="card", children=[
            card_head(title, i("badge")),
            html.P(note, className="stats") if note else None,
            html.Div(className="controls", children=[
                html.Div([html.Label("Step 1: factors to eliminate first (OLS; their effect is "
                                     "removed and the residual e⁽¹⁾ is kept)"),
                          dcc.Checklist(FACTOR_OPTIONS, step1_default, id=i("step1"), inline=True,
                                        className="checklist")],
                         className="control full"),
                html.Div([html.Label("Step 2: factors to explain the residual e⁽¹⁾ with "
                                     "(factors used in step 1 are disabled)"),
                          dcc.Checklist(FACTOR_OPTIONS, step2_default, id=i("step2"), inline=True,
                                        className="checklist")],
                         className="control full"),
            ]),
            html.Div(className="controls", children=[
                html.Div([html.Label("Return type"),
                          dcc.RadioItems([{"label": "Log return", "value": "log"},
                                          {"label": "Return", "value": "simple"}],
                                         "log", id=i("kind"), inline=True, className="radio")],
                         className="control"),
                html.Div([html.Label("Step 2 regularization"),
                          dcc.RadioItems([{"label": v, "value": k} for k, v in F.METHODS.items()],
                                         "ols", id=i("method"), inline=True, className="radio")],
                         className="control"),
                html.Div([html.Label("Coefficient charts"),
                          dcc.RadioItems([{"label": "β (original units)", "value": "raw"},
                                          {"label": "Standardized", "value": "std"}],
                                         "raw", id=i("scale"), inline=True, className="radio")],
                         className="control"),
            ]),
            html.Div(id=i("penalty-box"), className="controls", children=[
                html.Div([html.Label("Penalty strength α"),
                          dcc.RadioItems([{"label": "Choose by time-series cross-validation",
                                           "value": "cv"},
                                          {"label": "Set manually", "value": "manual"}],
                                         "cv", id=i("alpha-mode"), inline=True, className="radio")],
                         className="control"),
                html.Div([html.Label("log₁₀ α (manual; larger = stronger shrinkage)"),
                          dcc.Slider(-4, 1, 0.1, value=-2, id=i("log-alpha"),
                                     marks={k: f"10^{k}" for k in range(-4, 2)})],
                         className="control slider"),
                html.Div([html.Label("Elastic-net L1 share (0 = ridge, 1 = lasso)"),
                          dcc.Slider(0.05, 0.95, 0.05, value=0.5, id=i("l1-ratio"),
                                     marks={0.05: "0.05", 0.5: "0.5", 0.95: "0.95"})],
                         className="control slider", id=i("l1-box")),
            ]),
            loading(
                html.Div(className="step-eq", children=[
                    html.Span("Step 1", className="step-tag"),
                    dcc.Markdown(id=i("eq1"), className="equation", mathjax=True)]),
                html.Div(className="step-eq", children=[
                    html.Span("Step 2", className="step-tag"),
                    dcc.Markdown(id=i("eq2"), className="equation", mathjax=True)]),
                html.P(id=i("legend"), className="stats"),
                html.P(id=i("stats"), className="stats"),
                html.Div(className="two-col", children=[
                    dcc.Graph(id=i("coef1"), config={"displaylogo": False}),
                    dcc.Graph(id=i("coef2"), config={"displaylogo": False}),
                ]),
                html.Div(className="two-col", children=[
                    dcc.Graph(id=i("resid"), config={"displaylogo": False}),
                    html.Div(id=i("path-box"),
                             children=dcc.Graph(id=i("path"), config={"displaylogo": False})),
                ]),
                dash_table.DataTable(
                    id=i("table"),
                    columns=[{"name": "Step", "id": "step"}, {"name": "Factor", "id": "factor"},
                             {"name": "β", "id": "coef", "type": "numeric",
                              "format": {"specifier": ".4g"}},
                             {"name": "Standardized β", "id": "std_coef", "type": "numeric",
                              "format": {"specifier": ".3f"}},
                             {"name": "Std. error", "id": "se", "type": "numeric",
                              "format": {"specifier": ".4g"}},
                             {"name": "t-stat", "id": "t", "type": "numeric",
                              "format": {"specifier": ".2f"}},
                             {"name": "p-value", "id": "p", "type": "numeric",
                              "format": {"specifier": ".3g"}},
                             {"name": "VIF (within step)", "id": "vif", "type": "numeric",
                              "format": {"specifier": ".2f"}}],
                    **TABLE_STYLE, style_cell_conditional=left_align("step", "factor"),
                ),
            ),
        ]),
        html.Section(className="card", children=[
            card_head("Factor correlations", i("fc-badge")),
            loading(html.P(id=i("fc-stats"), className="stats"),
                    dcc.Graph(id=i("fc-graph"), config={"displaylogo": False})),
        ]),
        dcc.Store(id=i("avail"), data={"missing": MISSING, "options": FACTOR_OPTIONS}),
    ]


# ---------------------------------------------------------------- equations

def tex_sym(s):
    """Upright symbol for a superscript, with % and $ escaped (\\text{} would print them raw)."""
    return r"\mathrm{" + s.replace("%", r"\%").replace("$", r"\$") + "}"


def mark(tex, note="residual"):
    """Highlight a residual with a labelled underbrace (this app's MathJax has no \color)."""
    return rf"\underbrace{{{tex}}}_{{\text{{{note}}}}}"


def rhs(res, factors, factor_tex, resid_tex):
    """α + Σ β·x + residual, with standard errors under OLS coefficients; terms an L1
    penalty set to 0 are left out."""
    def num(v, se=None):
        body = tex_num(abs(v), 3)
        return rf"\underset{{({tex_num(se, 3)})}}{{{body}}}" if se is not None else body

    ols = res["method"] == "ols"
    a = res["intercept"] if abs(res["intercept"]) > 1e-12 else 0.0  # hide rounding noise
    terms = [("-" if a < 0 else "") + num(a, res.get("intercept_se") if ols else None)]
    for f in factors:
        b = res["coef"][f]
        if b == 0:
            continue
        terms.append(("- " if b < 0 else "+ ") + num(b, res["se"][f] if ols else None)
                     + r"\," + factor_tex(f))
    return " ".join(terms) + " + " + mark(resid_tex)


E1, EPS = r"e^{(1)}_t", r"\varepsilon_t"


# ---------------------------------------------------------------- charts

def empty_fig(text, height=260):
    fig = go.Figure().update_layout(base_layout(height=height))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.add_annotation(text=text, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                       font_color=MUTED)
    return fig


def coef_fig(res, frame, factors, scale, title, label):
    """Bar chart of one step's betas (original units or standardized), 95% CIs for OLS."""
    if res is None:
        return empty_fig(f"{title}: no factors selected")
    vals = res["coef"] if scale == "raw" else res["std_coef"]
    vals = vals.reindex(factors)
    err = None
    if res["method"] == "ols":
        se = res["se"].reindex(factors)
        if scale == "std":
            se = se * frame[factors].std(ddof=0) / frame["y"].std(ddof=0)
        err = dict(type="data", array=(1.96 * se).values, color=MUTED, thickness=1.5, width=4)
    fig = go.Figure(go.Bar(
        x=vals.values, y=[label(f) for f in factors], orientation="h",
        marker_color=[POS if v >= 0 else NEG for v in vals], error_x=err,
        hovertemplate="%{y}<br>β = %{x:.4g}<extra></extra>"))
    fig.update_layout(base_layout(
        height=max(260, 46 * len(factors) + 120), margin=dict(l=230, r=20, t=40, b=50),
        title=dict(text=title + (" (bars: 95% CI)" if err else ""), font=dict(size=13, color=INK),
                   x=0, xanchor="left"),
        xaxis=axis("β (original units)" if scale == "raw"
                   else "Standardized β (SDs of target per 1 SD of factor)",
                   zeroline=True, zerolinecolor=INK),
        yaxis=dict(autorange="reversed", tickfont_color=MUTED)))
    return fig


def resid_fig(frame, out, target_label, period):
    """Cumulative target, residual after step 1 and residual after step 2."""
    fig = go.Figure()
    series = [(frame["y"], target_label, INK, "solid"),
              (out["e1"], "After step 1: residual e⁽¹⁾", POS, "solid"),
              (out["eps"], "After step 2: residual ε", NEG, "dash")]
    for s, name, color, dash in series:
        fig.add_scatter(x=s.index, y=s.cumsum(), name=name, mode="lines",
                        line=dict(color=color, width=2, dash=dash),
                        hovertemplate="%{y:.3f}<extra>" + name + "</extra>")
    fig.update_layout(base_layout(
        height=360, hovermode="x unified", margin=dict(l=60, r=20, t=40, b=40),
        title=dict(text=f"Cumulative sum of per-{period} values", font=dict(size=13, color=INK),
                   x=0, xanchor="left"),
        legend=dict(orientation="h", y=-0.15, x=0, font_color=MUTED),
        yaxis=axis("Cumulative", zeroline=True, zerolinecolor="#c3c2b7")))
    return fig


def path_fig(frame2, factors, method, alpha, l1_ratio, label):
    path = F.regularization_path(frame2, method, l1_ratio)
    fig = go.Figure()
    for f in factors:
        color, dash = FACTOR_STYLE[f]
        fig.add_scatter(x=path.index, y=path[f], mode="lines", name=label(f),
                        line=dict(color=color, dash=dash, width=2),
                        hovertemplate="α %{x:.3g}: %{y:.3f}<extra>" + label(f) + "</extra>")
    fig.add_vline(x=alpha, line=dict(color=INK, width=1, dash="dot"))
    fig.update_layout(base_layout(
        height=360, margin=dict(l=60, r=20, t=40, b=40),
        title=dict(text=f"Step 2 regularization path ({F.METHODS[method]}); dotted = chosen α",
                   font=dict(size=13, color=INK), x=0, xanchor="left"),
        legend=dict(orientation="h", y=-0.2, x=0, font_color=MUTED),
        xaxis=axis("Penalty α (log scale)", type="log"),
        yaxis=axis("Standardized coefficient", zeroline=True, zerolinecolor="#c3c2b7")))
    return fig


def corr_fig(corr, labels):
    k = len(labels)
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=labels, y=labels, zmin=-1, zmax=1, zmid=0, colorscale=DIVERGING,
        texttemplate="%{z:.2f}", hovertemplate="%{y} · %{x}<br>ρ = %{z:.2f}<extra></extra>",
        colorbar=dict(title=dict(text="ρ", font_color=MUTED), tickfont_color=MUTED), xgap=2, ygap=2))
    fig.update_layout(base_layout(height=max(320, 56 * k + 140), margin=dict(l=230, r=30, t=20, b=150)))
    fig.update_xaxes(tickangle=-35, tickfont=dict(color=MUTED), showgrid=False)
    fig.update_yaxes(autorange="reversed", tickfont=dict(color=MUTED), showgrid=False)
    return fig


# ---------------------------------------------------------------- callbacks

# Factor availability runs in the browser. The JavaScript is fixed (data comes from the
# Store), so its hash-based name never changes between reloads.
AVAILABILITY_JS = """
function(...args) {
    const d = args[args.length - 1], v2 = args[args.length - 2] || [];
    let v1 = args[args.length - 3] || [];
    const sources = args.slice(0, args.length - 3);
    const miss = new Set(sources.flatMap(s => d.missing[s] || []));
    if (sources.length === 1) {  // a token can't be its own BTC / ETH factor
        if (sources[0] === "bitcoin") miss.add("btc");
        if (sources[0] === "ethereum") miss.add("eth");
    }
    v1 = v1.filter(v => !miss.has(v));
    const v2out = v2.filter(v => !miss.has(v) && !v1.includes(v));
    const o1 = d.options.map(o => ({...o, disabled: miss.has(o.value)}));
    const o2 = d.options.map(o => ({...o, disabled: miss.has(o.value) || v1.includes(o.value)}));
    return [o1, v1, o2, v2out];
}
"""


def register(p, sources, build):
    """Wire the section with prefix `p`.

    sources: Input()s that identify the target (token, or the pair's legs and hedge); the
    first one or two (coin ids) also drive factor availability.
    build(*source_values, factors, freq, kind, n) -> dict with frame, skipped, target_tex,
    target_label, badge, factor_tex(f), factor_label(f), legend.
    """
    i = lambda name: _ids(p, name)  # noqa: E731
    coin_inputs = [s for s in sources if s.component_property == "value"
                   and s.component_id in ("token", "leg-a", "leg-b")]

    clientside_callback(
        AVAILABILITY_JS,
        Output(i("step1"), "options"), Output(i("step1"), "value"),
        Output(i("step2"), "options"), Output(i("step2"), "value"),
        *coin_inputs, Input(i("step1"), "value"), State(i("step2"), "value"),
        State(i("avail"), "data"),
    )

    @callback(Output(i("penalty-box"), "style"), Output(i("l1-box"), "style"),
              Input(i("method"), "value"))
    def penalty_controls(method):
        hidden = {"display": "none"}
        return (hidden if method == "ols" else {}), ({} if method == "enet" else hidden)

    @callback(Output(i("eq1"), "children"), Output(i("eq2"), "children"),
              Output(i("legend"), "children"), Output(i("stats"), "children"),
              Output(i("coef1"), "figure"), Output(i("coef2"), "figure"),
              Output(i("resid"), "figure"), Output(i("path"), "figure"),
              Output(i("path-box"), "style"), Output(i("table"), "data"),
              Output(i("fc-graph"), "figure"), Output(i("fc-stats"), "children"),
              Output(i("badge"), "children"), Output(i("fc-badge"), "children"),
              *sources, Input("freq", "value"), Input("market-n", "value"),
              Input(i("step1"), "value"), Input(i("step2"), "value"), Input(i("kind"), "value"),
              Input(i("method"), "value"), Input(i("alpha-mode"), "value"),
              Input(i("log-alpha"), "value"), Input(i("l1-ratio"), "value"),
              Input(i("scale"), "value"))
    def run(*args):
        src = args[:len(sources)]
        freq, n, step1, step2, kind, method, alpha_mode, log_alpha, l1_ratio, scale = \
            args[len(sources):]
        if not all(v is not None for v in src[:len(coin_inputs)]):
            raise PreventUpdate
        step1 = step1 or []
        step2 = [f for f in (step2 or []) if f not in step1]
        b = build(*src, factors=step1 + step2, freq=freq, kind=kind, n=n)
        return render(b, step1, step2, freq, kind, method, alpha_mode, log_alpha, l1_ratio, scale)


def render(b, step1, step2, freq, kind, method, alpha_mode, log_alpha, l1_ratio, scale):
    frame, skipped, label, ftex = b["frame"], b["skipped"], b["factor_label"], b["factor_tex"]
    badge, period = b["badge"], analytics.PERIOD_NAMES[freq]
    skip_text = "; ".join(f"{label(f)} left out: {why}" for f, why in skipped.items())
    s1 = [f for f in step1 if f in frame.columns]
    s2 = [f for f in step2 if f in frame.columns]
    k = len(s1) + len(s2)
    if b.get("error") or not k or len(frame) < k + 5:
        msg = b.get("error") or ("Select factors for step 1 and/or step 2." if not k else
                                 f"Not enough {period}s ({len(frame)}) for {k} factors.")
        msg = " ".join(filter(None, [msg, skip_text]))
        blank = empty_fig(msg)
        return ("", "", "", msg, blank, blank, blank, blank, {"display": "none"}, [], blank, msg,
                badge, badge)

    # Step-2 penalty (step 1 is always OLS so eliminated factors are fully removed).
    alpha, notes = 0.0, []
    if method != "ols" and s2:
        e1_frame = F.two_step(frame, s1, [])["e1"].rename("y").to_frame().join(frame[s2])
        if alpha_mode == "cv":
            chosen, _ = F.choose_alpha(e1_frame, method, l1_ratio)
            if chosen is not None:
                alpha = chosen
                notes.append(f"α = {alpha:.3g} chosen by time-series cross-validation")
        if not notes:
            alpha = 10.0 ** log_alpha
            notes.append(f"α = {alpha:.3g} (manual)" + (
                "; too few periods to cross-validate, so the manual α is used"
                if alpha_mode == "cv" else ""))
    out = F.two_step(frame, s1, s2, method, alpha, l1_ratio)
    res1, res2 = out["res1"], out["res2"]

    # Equations: residuals highlighted; step 2's target is step 1's residual.
    y = b["target_tex"]
    lhs2 = mark(E1, "residual from step 1")
    eq1 = (rf"$${y} = {rhs(res1, s1, ftex, E1)}$$" if res1 else
           rf"$${mark(E1)} = {y}\quad\text{{(no factors eliminated)}}$$")
    eq2 = (rf"$${lhs2} = {rhs(res2, s2, ftex, EPS)}$$" if res2 else
           rf"$${mark(EPS)} = {lhs2}\quad\text{{(no step-2 factors)}}$$")
    names = {"log": "log return", "simple": "simple return"}[kind]
    legend = (b["legend"].format(period=period, names=names)
              + " Underbraced terms are residuals: e⁽¹⁾ is what is left of the target after step 1 "
                "(and the target of step 2), ε what is left after step 2. "
              + ("Standard errors in parentheses." if method == "ols" else
                 "Step 1 is OLS (standard errors in parentheses); step 2 is penalized, so it has "
                 "no standard errors and its coefficients are shrunk toward 0."))

    parts = [f"n = {len(frame)} {period}s"]
    if res1:
        parts.append(f"step 1 R² = {res1['r2']:.3f} (share of the target explained)")
    if res2:
        parts.append(f"step 2 R² = {res2['r2']:.3f} (share of e⁽¹⁾ explained)")
        if method == "ols":
            parts.append(f"step 2 adjusted R² = {res2['adj_r2']:.3f}")
        cv_o = F.cv_r2(out["frame2"], "ols")
        cv_m = F.cv_r2(out["frame2"], method, alpha, l1_ratio) if method != "ols" else None
        fmt = lambda v: f"{v:.3f}" if v is not None and np.isfinite(v) else "n/a (too few periods)"  # noqa: E731
        parts.append(f"step 2 out-of-sample R² (time-series CV): OLS {fmt(cv_o)}"
                     + (f", {F.METHODS[method].split(' ')[0]} {fmt(cv_m)}" if cv_m is not None else ""))
        dropped = [label(f) for f in s2 if res2["coef"][f] == 0]
        if dropped:
            notes.append("set to 0 by the L1 penalty: " + ", ".join(dropped))
    parts.append(f"total R² = {out['total_r2']:.3f}")
    parts += notes
    if skip_text:
        parts.append(skip_text)

    c1 = coef_fig(res1, frame[["y", *s1]], s1, scale, "Step 1 betas (eliminated factors)", label)
    c2 = coef_fig(res2, out["frame2"], s2, scale, "Step 2 betas (on residual e⁽¹⁾)", label)
    rf = resid_fig(frame, out, b["target_label"], period)
    if method != "ols" and res2:
        pf, pstyle = path_fig(out["frame2"], s2, method, alpha, l1_ratio, label), {}
    else:
        pf, pstyle = empty_fig(""), {"display": "none"}

    rows = []
    for step, res, fs in (("1", res1, s1), ("2", res2, s2)):
        if not res:
            continue
        vif = F.vif(frame[fs]) if fs else {}
        target = "target" if step == "1" else "e⁽¹⁾"
        rows.append({"step": step, "factor": f"Intercept (α), on {target}", "coef": res["intercept"],
                     "se": res.get("intercept_se")})
        ols = res["method"] == "ols"
        for f in fs:
            rows.append({"step": step, "factor": label(f), "coef": res["coef"][f],
                         "std_coef": res["std_coef"][f],
                         "se": res["se"][f] if ols else None, "t": res["t"][f] if ols else None,
                         "p": res["p"][f] if ols else None, "vif": vif[f]})

    corr = frame[["y", *s1, *s2]].corr()
    labels = [b["target_label"]] + [f"{label(f)} (step {'1' if f in s1 else '2'})" for f in s1 + s2]
    vif_all = F.vif(frame[s1 + s2])
    high = [f"{label(f)} ({vif_all[f]:.1f})" for f in s1 + s2 if vif_all[f] > 5]
    fc = (f"Correlations of per-{period} {names}s, {frame.index[0]:%Y-%m-%d} → "
          f"{frame.index[-1]:%Y-%m-%d}. VIF (variance inflation, across all chosen factors) "
          f"shows how much a factor is explained by the others: above ~5 means strong overlap, "
          f"so OLS coefficients are unstable; eliminating one of them in step 1 or regularizing "
          f"step 2 helps. ")
    fc += ("High VIF: " + ", ".join(high) + ".") if high else "No factor has VIF above 5."
    return (eq1, eq2, legend, " · ".join(parts), c1, c2, rf, pf, pstyle, rows,
            corr_fig(corr, labels), fc, badge, badge)


# ---------------------------------------------------------------- page builders

def token_builder(token_id, factors, freq, kind, n):
    """Individual page: target = the token's own return."""
    frame, skipped = F.factor_frame(token_id, factors, freq, kind, n)
    sym = INFO.at[token_id, "symbol"]
    syms = {f: F.FACTORS[f][1] for f in F.FACTORS}
    return {
        "frame": frame, "skipped": skipped, "badge": token_badge(token_id),
        "target_tex": rf"r^{{{tex_sym(sym)}}}_t", "target_label": f"{sym} return",
        "factor_tex": lambda f: rf"r^{{{tex_sym(syms[f])}}}_t",
        "factor_label": lambda f: F.FACTORS[f][0],
        "legend": "r = {names} per {period}; " + ", ".join(
            f"{s} = {F.FACTORS[f][0]}" for f, s in syms.items()
            if f in frame.columns) + ". Buyback and revenue factors use 1 + x.",
    }


def pair_builder(a, b, hedge, factors, freq, kind, n):
    """Cross-Section page: target = the pair spread s = r_A − h·r_B; coin-specific factors
    as A − B differences, common factors (market, BTC, ETH) as they are."""
    sa, sb = INFO.at[a, "symbol"], INFO.at[b, "symbol"]
    badge = [html.B(f"{sa} − {sb}"), " spread"]
    if a == b:
        return {"frame": pd.DataFrame(columns=["y"]), "skipped": {}, "badge": badge,
                "error": "Pick two different coins in the Spread section.",
                "factor_label": lambda f: F.FACTORS[f][0], "factor_tex": None}
    frame, skipped, h = F.pair_factor_frame(a, b, hedge, factors, freq, kind, n)
    syms = {f: F.FACTORS[f][1] for f in F.FACTORS}

    shared = F.COMMON_FACTORS | ({"sector"} if F.same_category(a, b) else set())

    def ftex(f):
        sym = tex_sym(syms[f])
        return rf"r^{{{sym}}}_t" if f in shared else rf"\Delta r^{{{sym}}}_t"

    def flabel(f):
        if f == "sector":
            return ("Sector, shared (category peers excl. both coins)" if f in shared
                    else "Sector (A − B; peers excl. both coins)")
        return F.FACTORS[f][0] + ("" if f in shared else " (A − B)")

    used = [f for f in syms if f in frame.columns]
    return {
        "frame": frame, "skipped": skipped, "badge": badge,
        "target_tex": r"s_t", "target_label": f"Spread {sa} − {sb}",
        "factor_tex": ftex, "factor_label": flabel,
        "legend": (rf"s = r^{sa} − {'' if hedge != 'beta' else f'{h:.3f}·'}r^{sb}, the spread from the "
                   "Spread section ({names} per {period}). Δr^X = r^X of "
                   f"{sa} minus r^X of {sb} (coin-specific factors); market, BTC and ETH are the "
                   "same for both coins (their difference is always 0), so they enter as they are "
                   "and measure the spread's remaining exposure to them. Sector averages leave out "
                   "both coins; "
                   + ("both are in the same category, so the sector is a shared factor too. "
                      if "sector" in shared else "they are in different categories, so the sector "
                      "enters as A − B. ")
                   + ", ".join(f"{syms[f]} = {F.FACTORS[f][0]}" for f in used) + "."),
    }
