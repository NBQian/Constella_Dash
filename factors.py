"""Multi-factor regressions for one token: factor construction, OLS and regularized fits
(ridge / lasso / elastic net), multicollinearity (VIF) and regularization paths."""
import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import ElasticNet, Lasso, Ridge
from sklearn.model_selection import TimeSeriesSplit

import analytics
import cross_section as xs

# key: (label, short symbol used in the equation, needs buyback / revenue data)
FACTORS = {
    "market": ("Market (equal-weighted top n)", "MKT", False),
    "sector": ("Sector (category peers, equal-weighted)", "SEC", False),
    "btc": ("Bitcoin", "BTC", False),
    "eth": ("Ethereum", "ETH", False),
    "volume": ("Token trading volume", "VOL", False),
    "buyback_pct_supply": ("Token buyback, % of supply", "BB%", True),
    "buyback_usd": ("Token buyback, USD", "BB$", True),
    "revenue_pct_supply": ("Token revenue, % of supply", "REV%", True),
    "revenue_usd": ("Token revenue, USD", "REV$", True),
}
METHODS = {"ols": "OLS (no regularization)", "ridge": "Ridge (L2)", "lasso": "Lasso (L1)",
           "enet": "Elastic net (L1 + L2)"}
ALPHA_GRID = np.logspace(-4, 1, 51)  # penalty strength, in standardized units
CV_SPLITS = 5


# ---------------------------------------------------------------- factors

def sector_peers(token_id, exclude=()):
    """Other coins in the token's category. The token itself (and any coins in `exclude`, e.g.
    the other leg of a pair) is left out, so the sector factor isn't mechanically correlated
    with the target."""
    cats = xs.categories()
    drop = {token_id, *exclude}
    return [c for c in cats.index[cats == cats[token_id]] if c not in drop]


def sector_returns(token_id, freq, kind="log", exclude=()):
    """Equal-weighted average return of the token's category peers (NaN if none)."""
    peers = sector_peers(token_id, exclude)
    if not peers:
        return pd.Series(dtype=float, name="sector")
    panel = analytics.resample_prices(analytics.price_panel()[peers], freq)
    return panel.apply(analytics.returns, kind=kind).mean(axis=1, skipna=True).rename("sector")


def sector_index(token_id, freq):
    """Rebalanced equal-weighted index of the category peers, 100 at the data start."""
    r = sector_returns(token_id, freq, "simple")
    return 100 * (1 + r.fillna(0)).cumprod() if len(r) else r


# Factors that are the same series for every coin: in a pair regression their A − B
# difference is identically 0, so they enter as the factor itself.
COMMON_FACTORS = {"market", "btc", "eth"}


def factor_series(token_id, f, freq, kind, n, exclude=()):
    """One factor's per-period return series for a token, or (None, reason) if unavailable.
    `exclude`: coins left out of the sector average besides the token itself."""
    if f == "market":
        return analytics.market_returns(n, freq, kind), None
    if f in ("btc", "eth"):
        cid = {"btc": "bitcoin", "eth": "ethereum"}[f]
        panel = analytics.resample_prices(analytics.price_panel()[[cid]], freq)
        return analytics.returns(panel[cid], kind), None
    if f == "sector":
        s = sector_returns(token_id, freq, kind, exclude)
        return (s, None) if not s.empty else (None, "no other coins in its category")
    df = analytics.resample(analytics.load_token(token_id), freq)
    if f == "volume":
        return analytics.returns(df["volume_usd"], kind), None
    if df[f].isna().all():  # buyback / revenue columns: returns of 1 + x
        return None, f"no {'revenue' if f in analytics.REVENUE_COLS else 'buyback'} data"
    return analytics.returns(df[f], kind), None


def _finish(cols, skipped):
    """Align on dates where everything is present; drop constant (inestimable) factors."""
    frame = pd.DataFrame(cols).replace([np.inf, -np.inf], np.nan).dropna()
    for f in list(frame.columns[1:]):
        if frame[f].std() == 0:
            skipped[f] = "constant over the period"
            frame = frame.drop(columns=f)
    return frame, skipped


def factor_frame(token_id, factors, freq, kind, n):
    """Token regression data: column "y" (the token's price return) plus one column per
    usable factor. Returns (frame, skipped) with skipped = {factor: reason}."""
    df = analytics.resample(analytics.load_token(token_id), freq)
    cols, skipped = {"y": analytics.returns(df["price"], kind)}, {}
    for f in factors:
        if f in ("btc", "eth") and {"btc": "bitcoin", "eth": "ethereum"}[f] == token_id:
            skipped[f] = "it is the token itself"
            continue
        s, why = factor_series(token_id, f, freq, kind, n)
        if s is None:
            skipped[f] = why + " for this token"
            continue
        cols[f] = s.reindex(df.index)
    return _finish(cols, skipped)


def pair_factor_frame(a, b, hedge, factors, freq, kind, n):
    """Pair regression data: "y" is the spread r_A − h·r_B (as in the Spread section);
    coin-specific factors enter as the difference f_A − f_B, common factors (market, BTC,
    ETH) as the factor itself. Returns (frame, skipped, h).

    The sector averages leave out BOTH coins: otherwise A's sector contains B and B's
    contains A, and their difference would be a scaled copy of the spread itself. If both
    coins share a category the two averages coincide, so the sector enters once, as a
    shared factor (like the market)."""
    sp, h, _ = xs.spread([a, b], freq, hedge, kind)
    cols, skipped = {"y": sp["spread"]}, {}
    for f in factors:
        if f in COMMON_FACTORS or (f == "sector" and same_category(a, b)):
            s, why = factor_series(a, f, freq, kind, n, exclude=(b,))
            if s is None:
                skipped[f] = why + " besides the two coins"
                continue
        elif f == "sector":
            sa, why_a = factor_series(a, f, freq, kind, n, exclude=(b,))
            sb, why_b = factor_series(b, f, freq, kind, n, exclude=(a,))
            if sa is None or sb is None:
                skipped[f] = (why_a or why_b) + " for " + ("coin A" if sa is None else "coin B")
                continue
            s = sa - sb
        else:
            sa, why_a = factor_series(a, f, freq, kind, n)
            sb, why_b = factor_series(b, f, freq, kind, n)
            if sa is None or sb is None:
                skipped[f] = (why_a or why_b) + " for " + ("coin A" if sa is None else "coin B")
                continue
            s = sa - sb
        cols[f] = s.reindex(sp.index)
    frame, skipped = _finish(cols, skipped)
    return frame, skipped, h


def same_category(a, b):
    cats = xs.categories()
    return cats[a] == cats[b]


def two_step(frame, step1, step2, method="ols", alpha=0.01, l1_ratio=0.5):
    """Step 1: OLS of y on the factors to eliminate, keeping the residual e1 (y itself when
    step 1 is empty). Step 2: regression of e1 on the factors to study (OLS or regularized),
    with residual eps. Both steps use the same dates."""
    s1 = [f for f in step1 if f in frame.columns]
    s2 = [f for f in step2 if f in frame.columns and f not in s1]
    y = frame["y"]
    res1 = None
    if s1:
        res1 = fit(frame[["y", *s1]], "ols")
        e1 = y - (res1["intercept"] + frame[s1] @ res1["coef"])
    else:
        e1 = y.copy()
    frame2 = pd.concat([e1.rename("y"), frame[s2]], axis=1)
    res2 = fit(frame2, method, alpha, l1_ratio) if s2 else None
    eps = e1 - (res2["intercept"] + frame[s2] @ res2["coef"]) if res2 else e1
    total_r2 = 1 - eps.var(ddof=0) / y.var(ddof=0)
    return {"step1": s1, "step2": s2, "res1": res1, "res2": res2, "e1": e1, "eps": eps,
            "frame2": frame2, "total_r2": total_r2}


# ---------------------------------------------------------------- diagnostics

def vif(X):
    """Variance inflation factor per column: 1 / (1 − R²) of that column on the others."""
    out = {}
    for c in X.columns:
        others = X.drop(columns=c)
        if others.empty:
            out[c] = 1.0
            continue
        A = np.column_stack([np.ones(len(X)), others.values])
        coef, *_ = np.linalg.lstsq(A, X[c].values, rcond=None)
        resid = X[c].values - A @ coef
        r2 = 1 - resid.var() / X[c].var(ddof=0)
        out[c] = 1 / (1 - r2) if r2 < 1 else np.inf
    return pd.Series(out)


# ---------------------------------------------------------------- fitting

def _model(method, alpha, l1_ratio, n_obs):
    # One penalty scale for all methods (sklearn's ElasticNet objective):
    #   1/(2n)·RSS + alpha·l1_ratio·|w|₁ + ½·alpha·(1 − l1_ratio)·|w|₂²
    # Ridge's objective is RSS + a·|w|₂², so the same alpha is a = n·alpha.
    if method == "ridge":
        return Ridge(alpha=n_obs * alpha)
    if method == "lasso":
        return Lasso(alpha=alpha, max_iter=50_000)
    return ElasticNet(alpha=alpha, l1_ratio=l1_ratio, max_iter=50_000)


def _standardize(frame):
    mu, sd = frame.mean(), frame.std(ddof=0)
    return (frame - mu) / sd, mu, sd


def cv_r2(frame, method, alpha=None, l1_ratio=0.5):
    """Average out-of-sample R² over expanding-window time-series splits (train on the past,
    test on the next block). Standardization is fitted on each training block only."""
    X, y = frame.drop(columns="y"), frame["y"]
    splits = cv_splits(len(frame), X.shape[1])
    if splits is None:
        return np.nan
    scores = []
    for tr, te in splits.split(X):
        Xtr, Xte, ytr, yte = X.iloc[tr], X.iloc[te], y.iloc[tr], y.iloc[te]
        mu, sd = Xtr.mean(), Xtr.std(ddof=0).replace(0, 1)
        ymu, ysd = ytr.mean(), ytr.std(ddof=0) or 1
        if method == "ols":
            A = np.column_stack([np.ones(len(Xtr)), Xtr.values])
            coef, *_ = np.linalg.lstsq(A, ytr.values, rcond=None)
            pred = np.column_stack([np.ones(len(Xte)), Xte.values]) @ coef
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                m = _model(method, alpha, l1_ratio, len(Xtr)).fit((Xtr - mu) / sd, (ytr - ymu) / ysd)
            pred = m.predict((Xte - mu) / sd) * ysd + ymu
        ss_res = ((yte.values - pred) ** 2).sum()
        ss_tot = ((yte.values - yte.mean()) ** 2).sum()
        scores.append(1 - ss_res / ss_tot if ss_tot > 0 else np.nan)
    scores = [v for v in scores if np.isfinite(v)]
    return float(np.mean(scores)) if scores else np.nan


def cv_splits(n_obs, k):
    """Expanding-window splits with test blocks of >= 3 periods and training sets of >= k + 5
    periods; None when the sample is too short to cross-validate (e.g. monthly data)."""
    test = max(3, n_obs // (CV_SPLITS + 1))
    n_splits = min(CV_SPLITS, (n_obs - (k + 5)) // test)
    return TimeSeriesSplit(n_splits=n_splits, test_size=test) if n_splits >= 2 else None


def choose_alpha(frame, method, l1_ratio):
    """Penalty with the best time-series cross-validated R² on ALPHA_GRID, or (None, scores)
    when the sample is too short to cross-validate."""
    scores = [cv_r2(frame, method, a, l1_ratio) for a in ALPHA_GRID]
    if not np.isfinite(scores).any():
        return None, scores
    return float(ALPHA_GRID[int(np.nanargmax(scores))]), scores


def fit(frame, method="ols", alpha=0.01, l1_ratio=0.5):
    """Fit y on all factor columns. Returns a dict with coefficients in original units
    (`coef`, `intercept`), standardized coefficients (`std_coef`), R², and for OLS
    standard errors, t-stats and p-values."""
    X, y = frame.drop(columns="y"), frame["y"]
    out = {"method": method, "n": len(frame), "k": X.shape[1]}
    if method == "ols":
        res = sm.OLS(y, sm.add_constant(X, has_constant="add")).fit()
        out.update(intercept=res.params["const"], coef=res.params.drop("const"),
                   intercept_se=res.bse["const"], se=res.bse.drop("const"),
                   t=res.tvalues.drop("const"), p=res.pvalues.drop("const"),
                   r2=res.rsquared, adj_r2=res.rsquared_adj)
    else:
        Z, mu, sd = _standardize(frame)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            m = _model(method, alpha, l1_ratio, len(Z)).fit(Z.drop(columns="y"), Z["y"])
        std_coef = pd.Series(m.coef_, index=X.columns)
        coef = std_coef * sd["y"] / sd[X.columns]
        intercept = mu["y"] - (coef * mu[X.columns]).sum()
        resid = y - (intercept + X @ coef)
        r2 = 1 - resid.var(ddof=0) / y.var(ddof=0)
        out.update(intercept=intercept, coef=coef, r2=r2, alpha=alpha, l1_ratio=l1_ratio)
    sd = frame.std(ddof=0)
    out["std_coef"] = out["coef"] * sd[X.columns] / sd["y"]
    return out


def regularization_path(frame, method, l1_ratio=0.5):
    """Standardized coefficients for every alpha in ALPHA_GRID (rows) x factor (columns)."""
    Z, *_ = _standardize(frame)
    X, y = Z.drop(columns="y"), Z["y"]
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        for a in ALPHA_GRID:
            rows.append(_model(method, a, l1_ratio, len(Z)).fit(X, y).coef_)
    return pd.DataFrame(rows, index=ALPHA_GRID, columns=X.columns)
