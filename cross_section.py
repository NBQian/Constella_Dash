"""Cross-sectional analytics: coin categories, pair spreads, per-coin metrics over a date
window, and return correlations."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd

import analytics
import config
from analytics import MIN_OBS, PERIODS_PER_YEAR


# ---------------------------------------------------------------- categories

def categorize(tags):
    """First business-type category whose rule matches one of the coin's CoinGecko tags."""
    for category, needles in config.CATEGORY_RULES:
        if any(n.lower() in t.lower() for t in tags for n in needles):
            return category
    return config.OTHER_CATEGORY


@lru_cache(maxsize=1)
def categories():
    """Series: coin id -> business-type category (config.OTHER_CATEGORY if no rule matches
    or data/categories.json is missing). Manual overrides in config win."""
    universe, *_ = analytics.load_cache()
    raw = json.loads(config.CATEGORIES_FILE.read_text()) if config.CATEGORIES_FILE.exists() else {}
    cats = {cid: config.CATEGORY_OVERRIDES.get(cid) or categorize(raw.get(cid, []))
            for cid in universe["id"]}
    return pd.Series(cats, name="category")


def category_order():
    """Categories in config order (fixed, so colors never change with the filter)."""
    present = set(categories())
    names = [c for c, _ in config.CATEGORY_RULES] + [config.OTHER_CATEGORY]
    return [c for c in dict.fromkeys(names) if c in present]


def filter_ids(selected_categories=None, buybacks_only=False, revenue_only=False):
    """Coin ids (market-cap order) in the selected categories (all when none selected),
    optionally only coins with buyback and/or revenue data."""
    universe, *_ = analytics.load_cache()
    u = universe.sort_values("market_cap", ascending=False)
    keep = pd.Series(True, index=u.index)
    if selected_categories:
        keep &= u["id"].map(categories()).isin(selected_categories)
    if buybacks_only:
        keep &= u["has_buybacks"].astype(bool)
    if revenue_only:
        keep &= u["has_revenue"].astype(bool)
    return list(u.loc[keep, "id"])


# ---------------------------------------------------------------- pair spreads

def spread(ids, freq, hedge="equal", kind="log"):
    """Per-period returns of the two legs and of the pair spread, on dates both legs share.

    spread = r_A − h·r_B, with h = 1 (hedge="equal") or the OLS hedge ratio
    β = Cov(r_A, r_B) / Var(r_B) fitted on the whole period (hedge="beta").
    Returns (frame, h, beta): `frame` has columns A, B (ids) and "spread"; beta is None for 1:1.
    """
    a, b = ids
    panel = analytics.resample_prices(analytics.price_panel()[[a, b]], freq)
    r = panel.apply(analytics.returns, kind=kind).dropna()
    beta = None
    if hedge == "beta" and len(r) >= MIN_OBS and r[b].var() > 0:
        beta = float(np.cov(r[a], r[b])[0, 1] / r[b].var())
    h = beta if beta is not None else 1.0
    out = r.copy()
    out["spread"] = r[a] - h * r[b]
    return out, h, beta


def spread_stats(frame, ids, freq):
    s = frame["spread"]
    ppy = PERIODS_PER_YEAR[freq]
    stats = {"n": len(s), "total": s.sum(), "vol": s.std() * np.sqrt(ppy),
             "mean": s.mean(), "t": s.mean() / (s.std() / np.sqrt(len(s))) if len(s) > 1 else np.nan}
    if len(ids) >= 2:
        stats["corr"] = frame[ids[0]].corr(frame[ids[1]])
    return stats


# ---------------------------------------------------------------- per-coin metrics

# key: (label, unit) — unit drives axis/table formatting.
METRICS = {
    "return": ("Log return over window", "log"),
    "vol": ("Annualized volatility", "pct"),
    "sharpe": ("Return / risk (annualized mean ÷ vol)", "num"),
    "beta": ("Market beta", "num"),
    "alpha": ("Alpha vs market (annualized)", "pct"),
    "corr": ("Correlation with market", "num"),
    "max_dd": ("Max drawdown", "pct"),
    "buyback_yield": ("Buyback yield (annualized, % of market cap)", "pct"),
    "buyback_supply": ("Buyback over window (% of supply)", "pct"),
    "revenue_yield": ("Revenue yield (annualized, % of market cap)", "pct"),
    "payout_ratio": ("Payout ratio (holders revenue ÷ revenue)", "pct"),
    "turnover": ("Turnover (avg daily volume ÷ market cap)", "pct"),
    "log_mcap": ("Size: log₁₀ market cap (USD)", "num"),
}


@lru_cache(maxsize=1)
def daily_panels():
    """Wide daily panels (date x id): price, volume (USD), buyback and revenue (USD, NaN =
    no data for that coin)."""
    universe, prices, buybacks, _ = analytics.load_cache()
    price = analytics.price_panel()
    volume = prices.pivot(index="date", columns="id", values="volume_usd").reindex_like(price)
    bb = buybacks.pivot(index="date", columns="id", values="buyback_usd").reindex(price.index)
    has_bb = universe.set_index("id")["has_buybacks"].astype(bool)
    bb = bb.reindex(columns=price.columns)
    bb.loc[:, has_bb[has_bb].index] = bb.loc[:, has_bb[has_bb].index].fillna(0.0)
    rev = analytics.load_revenue().pivot(index="date", columns="id", values="revenue_usd")
    rev = rev.reindex(index=price.index, columns=price.columns)
    has_rev = universe.set_index("id")["has_revenue"].astype(bool)
    rev.loc[:, has_rev[has_rev].index] = rev.loc[:, has_rev[has_rev].index].fillna(0.0)
    return price, volume, bb, rev


def date_bounds():
    price = analytics.price_panel()
    return price.index.min(), price.index.max()


def coin_metrics(start, end, freq, n):
    """One row per coin with every METRICS column, computed over [start, end] (dates).

    Returns use `freq`; buybacks, volume and drawdown use daily data. Market cap and supply
    are today's values (historical ones aren't in the data).
    """
    universe, *_ = analytics.load_cache()
    price, volume, bb, rev = daily_panels()
    win = (price.index >= start) & (price.index <= end)
    p, v, b, rv = price[win], volume[win], bb[win], rev[win]
    days = max(win.sum(), 1)
    ppy = PERIODS_PER_YEAR[freq]

    r = np.log(analytics.resample_prices(p, freq)).diff()
    m = analytics.market_returns(n, freq, "log").reindex(r.index)
    info = universe.set_index("id")

    rows = {}
    for cid in p.columns:
        px = p[cid].dropna()
        ri = r[cid]
        ok = ri.notna() & m.notna()
        x, y = m[ok], ri[ok]
        row = {"return": np.log(px.iloc[-1] / px.iloc[0]) if len(px) > 1 else np.nan,
               "vol": y.std() * np.sqrt(ppy) * 100 if len(y) > 1 else np.nan,
               "max_dd": ((px / px.cummax()).min() - 1) * 100 if len(px) > 1 else np.nan}
        row["sharpe"] = (y.mean() * ppy) / (row["vol"] / 100) if row["vol"] else np.nan
        if len(y) >= MIN_OBS and x.var() > 0:
            beta = np.cov(x, y)[0, 1] / x.var()
            row.update(beta=beta, alpha=(y.mean() - beta * x.mean()) * ppy * 100, corr=x.corr(y))
        else:
            row.update(beta=np.nan, alpha=np.nan, corr=np.nan)
        mcap, supply = info.at[cid, "market_cap"], info.at[cid, "supply"]
        bsum = b[cid].sum(min_count=1)
        row["buyback_yield"] = bsum / mcap * 365 / days * 100 if pd.notna(bsum) else np.nan
        bb_tokens = (b[cid] / p[cid]).sum(min_count=1)
        row["buyback_supply"] = bb_tokens / supply * 100 if pd.notna(bb_tokens) else np.nan
        rsum = rv[cid].sum(min_count=1)
        row["revenue_yield"] = rsum / mcap * 365 / days * 100 if pd.notna(rsum) else np.nan
        # Share of revenue paid to holders; needs both series and positive revenue.
        row["payout_ratio"] = bsum / rsum * 100 if pd.notna(bsum) and pd.notna(rsum) and rsum > 0 \
            else np.nan
        row["turnover"] = v[cid].mean() / mcap * 100
        row["log_mcap"] = np.log10(mcap)
        rows[cid] = row

    out = pd.DataFrame.from_dict(rows, orient="index")
    out.insert(0, "category", categories().reindex(out.index))
    out.insert(0, "name", info["name"].reindex(out.index))
    out.insert(0, "symbol", info["symbol"].reindex(out.index))
    out["has_buybacks"] = info["has_buybacks"].reindex(out.index).astype(bool)
    out.index.name = "id"
    return out


# ---------------------------------------------------------------- correlations

def correlation_matrix(ids, start, end, freq):
    """Pairwise correlation of log returns over the window (pairs need MIN_OBS overlap)."""
    price = analytics.price_panel()[ids]
    p = price[(price.index >= start) & (price.index <= end)]
    r = np.log(analytics.resample_prices(p, freq)).diff()
    return r.corr(min_periods=MIN_OBS)


