"""Load cached data and compute per-token series and log returns."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy import stats

import config

# Flow columns: NaN for tokens without that data, 0 on days with none for tokens that have it.
BUYBACK_COLS = {"buyback_usd", "buyback_tokens", "buyback_pct_supply"}
REVENUE_COLS = {"revenue_usd", "revenue_pct_supply"}
# Columns that may contain zeros (no flow that day): returns use 1 + x.
ZERO_SAFE_COLS = BUYBACK_COLS | REVENUE_COLS


@lru_cache(maxsize=1)
def load_cache():
    universe = pd.read_csv(config.UNIVERSE_FILE)
    prices = pd.read_parquet(config.PRICES_FILE)
    buybacks = pd.read_parquet(config.BUYBACKS_FILE)
    meta = json.loads(config.META_FILE.read_text())
    universe["has_revenue"] = universe["id"].isin(load_revenue()["id"].unique())
    return universe, prices, buybacks, meta


@lru_cache(maxsize=1)
def revenue_sources():
    """{coin id: {"slugs": [...], "kind": "protocol" | "chain", "usd_1y": float}}."""
    f = config.REVENUE_SOURCES_FILE
    return json.loads(f.read_text()) if f.exists() else {}


@lru_cache(maxsize=1)
def load_revenue():
    """Long frame (date, revenue_usd, id) of DeFiLlama revenue; empty if not fetched yet."""
    if not config.REVENUE_FILE.exists():
        return pd.DataFrame(columns=["date", "revenue_usd", "id"])
    return pd.read_parquet(config.REVENUE_FILE)


def load_token(token_id):
    """Daily frame: price, volume_usd, buyback_usd, buyback_tokens, buyback_pct_supply,
    revenue_usd, revenue_pct_supply.

    Buyback / revenue columns are NaN for tokens without that data, and 0 on days with
    none for tokens that have it. "% of supply" = USD ÷ that day's price ÷ current total
    supply, so buybacks (holders revenue) and revenue are in the same unit.
    """
    universe, prices, buybacks, _ = load_cache()
    info = universe.set_index("id").loc[token_id]
    df = prices[prices["id"] == token_id].set_index("date")[["price", "volume_usd"]].sort_index()

    if info["has_buybacks"]:
        bb = buybacks[buybacks["id"] == token_id].set_index("date")["buyback_usd"]
        df["buyback_usd"] = bb.reindex(df.index).fillna(0.0)
        df["buyback_tokens"] = df["buyback_usd"] / df["price"]
        df["buyback_pct_supply"] = df["buyback_tokens"] / info["supply"] * 100
    else:
        df[["buyback_usd", "buyback_tokens", "buyback_pct_supply"]] = np.nan

    if info["has_revenue"]:
        rev = load_revenue()
        rev = rev[rev["id"] == token_id].set_index("date")["revenue_usd"]
        df["revenue_usd"] = rev.reindex(df.index).fillna(0.0)
        df["revenue_pct_supply"] = df["revenue_usd"] / df["price"] / info["supply"] * 100
    else:
        df[["revenue_usd", "revenue_pct_supply"]] = np.nan
    return df


# Calendar periods: weeks end on Sunday, months at month end. Only complete periods are
# kept (a partial first/last week or month would bias flows and returns).
FREQ_RULES = {"W": "W-SUN", "M": "ME"}
FREQ_NAMES = {"D": "daily", "W": "weekly", "M": "monthly"}
PERIOD_NAMES = {"D": "day", "W": "week", "M": "month"}
PERIODS_PER_YEAR = {"D": 365, "W": 52, "M": 12}


def _complete(g, freq):
    """Boolean mask over resampled periods: True when every calendar day has a row."""
    size = g.size()
    full = 7 if freq == "W" else size.index.days_in_month
    return size == full


def resample(df, freq):
    """freq 'D' (unchanged), 'W' (weeks ending Sunday) or 'M' (calendar months);
    partial periods dropped. Price = last close, flows summed."""
    if freq == "D":
        return df
    agg = {"price": "last", "volume_usd": "sum", **{c: "sum" for c in ZERO_SAFE_COLS}}
    g = df.resample(FREQ_RULES[freq])
    out = g.agg(agg)[_complete(g, freq)]
    # sum() turns all-NaN periods into 0; restore NaN for tokens without that data.
    for cols in (BUYBACK_COLS, REVENUE_COLS):
        if df[list(cols)].isna().all().all():
            out[list(cols)] = np.nan
    return out


def resample_prices(panel, freq):
    """Wide price panel at freq: last close of each complete period."""
    if freq == "D":
        return panel
    g = panel.resample(FREQ_RULES[freq])
    return g.last()[_complete(g, freq)]


def resample_flags(flags, freq):
    """Daily boolean flags to freq: a period is flagged if any of its days is."""
    return flags if freq == "D" else flags.resample(FREQ_RULES[freq]).max()


@lru_cache(maxsize=1)
def price_panel():
    """Wide daily close prices (date x token id), columns ordered by market cap rank."""
    universe, prices, _, _ = load_cache()
    ids = universe.sort_values("market_cap", ascending=False)["id"]
    return prices.pivot(index="date", columns="id", values="price").sort_index()[ids]


def market_returns(n, freq="D", kind="log"):
    """Market factor: equal-weighted average return of the top-n tokens by market cap.

    Each period averages over the tokens that have data for it (tokens listed
    mid-window join when their history starts).
    """
    panel = resample_prices(price_panel().iloc[:, :n], freq)
    r = panel.apply(returns, kind=kind)
    return r.mean(axis=1, skipna=True).rename("market")


def market_index(n, freq="D"):
    """Equal-weighted top-n index (rebalanced each period), 100 on the first period of the
    data window. Independent of any token, so every chart shows the same values per date."""
    r = market_returns(n, freq, kind="simple").fillna(0)
    return 100 * (1 + r).cumprod()


def abnormal_events(series, percentile, lookback):
    """True on days where the value is at or above the rolling `percentile` of the
    trailing `lookback` days (window includes the current day).

    The first lookback-1 days have no full window and can never be events.
    """
    s = series.dropna()
    threshold = s.rolling(lookback, min_periods=lookback).quantile(percentile / 100)
    return (s >= threshold).reindex(series.index, fill_value=False), threshold.reindex(series.index)


def returns(series, kind="log"):
    """Period-over-period change of a series.

    kind="log":    ln(x_t / x_{t-1})
    kind="simple": x_t / x_{t-1} - 1
    Buyback columns use 1+x in place of x so zero-buyback periods stay defined.
    """
    # Flows use 1 + x; values <= 0 (e.g. a rare negative revenue day) give no return.
    x = series + 1 if series.name in ZERO_SAFE_COLS else series
    x = x.where(x > 0)
    r = np.log(x).diff() if kind == "log" else x.pct_change(fill_method=None)
    return r.replace([np.inf, -np.inf], np.nan)


MIN_OBS = 10


def transform(series, kind):
    """kind "level" (unchanged), "log" or "simple" (see `returns`)."""
    return series if kind == "level" else returns(series, kind)


def market_factor(n, freq, kind):
    """Market series for the scatter: index level (start = 100) or average return."""
    return market_index(n, freq) if kind == "level" else market_returns(n, freq, kind)


def regression_data(daily, x_col, x_kind, y_kind, freq, n, market=None):
    """Aligned x/y for one token: y = price, x = factor, each as level / log / simple return.

    `market` may be passed in (precomputed market_factor) to avoid recomputing it.
    """
    df = resample(daily, freq)
    if x_col == "market":
        m = market if market is not None else market_factor(n, freq, x_kind)
        x = m.reindex(df.index)
    else:
        x = transform(df[x_col], x_kind)
    return pd.DataFrame({"x": x, "y": transform(df["price"], y_kind)}).dropna()


def fit(d):
    """OLS y = alpha + beta * x, or None when there is too little data."""
    if len(d) < MIN_OBS or d["x"].std() == 0:
        return None
    return stats.linregress(d["x"], d["y"])


def beta_table(x_col, x_kind, y_kind, freq, n, ids=None):
    """Regression of each token's price on the factor (scatter settings), ranked by beta.
    `ids` limits it to those coins (all when None)."""
    universe, *_ = load_cache()
    if ids is not None:
        universe = universe[universe["id"].isin(ids)]
    market = market_factor(n, freq, x_kind) if x_col == "market" else None
    rows = []
    for r in universe.itertuples():
        if (x_col in BUYBACK_COLS and not r.has_buybacks) or \
                (x_col in REVENUE_COLS and not r.has_revenue):
            continue
        d = regression_data(load_token(r.id), x_col, x_kind, y_kind, freq, n, market)
        f = fit(d)
        if f is None:
            continue
        rows.append({"id": r.id, "symbol": r.symbol, "name": r.name,
                     "segment": "Top 50" if getattr(r, "segment", "top") == "top" else "Buyback addition",
                     "buybacks": "Yes" if r.has_buybacks else "No",
                     "beta": f.slope, "beta_se": f.stderr, "t": f.slope / f.stderr if f.stderr else np.nan,
                     "p": f.pvalue, "alpha": f.intercept, "r2": f.rvalue ** 2, "n": len(d)})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values("beta", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", out.index + 1)
    return out
