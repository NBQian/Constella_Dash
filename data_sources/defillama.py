"""DeFiLlama: daily token buybacks (USD).

DeFiLlama's free API has no standalone buyback series. We use "Holders Revenue",
which DeFiLlama defines as value returned to token holders — for most tokens this
is buybacks (and burns), though for some it also includes staking distributions.
"""
import logging

import pandas as pd

import config
from data_sources.http import get_json

log = logging.getLogger(__name__)


def _holders_revenue_overview():
    return get_json(
        f"{config.DEFILLAMA_URL}/overview/fees",
        params=dict(dataType="dailyHoldersRevenue", excludeTotalDataChart="true",
                    excludeTotalDataChartBreakdown="true"),
    )["protocols"]


def buyback_protocols(min_usd_1y=config.MIN_BUYBACK_USD_1Y):
    """All tokens with DeFiLlama holders revenue, keyed by CoinGecko id.

    Returns {gecko_id: {"slugs": [...], "usd_1y": float}} for tokens whose protocols
    returned at least `min_usd_1y` to holders over the last year.
    """
    lite = get_json(f"{config.DEFILLAMA_URL}/lite/protocols2")
    parents = {p["id"]: p["gecko_id"] for p in lite["parentProtocols"] if p.get("gecko_id")}
    singles = {str(p["defillamaId"]): p["geckoId"] for p in lite["protocols"] if p.get("geckoId")}

    tokens = {}
    for p in _holders_revenue_overview():
        usd = p.get("total1y") or 0
        if usd <= 0:
            continue
        gid = parents.get(p.get("parentProtocol")) or singles.get(str(p.get("defillamaId")))
        if gid:
            t = tokens.setdefault(gid, {"slugs": [], "usd_1y": 0.0})
            t["slugs"].append(p["slug"])
            t["usd_1y"] += usd
    return {gid: t for gid, t in tokens.items() if t["usd_1y"] >= min_usd_1y}


def daily_holders_revenue(slug):
    data = get_json(f"{config.DEFILLAMA_URL}/summary/fees/{slug}",
                    params=dict(dataType="dailyHoldersRevenue"))
    chart = data.get("totalDataChart") or []
    s = pd.Series({pd.to_datetime(ts, unit="s"): float(v) for ts, v in chart}, dtype=float)
    s.index.name = "date"
    return s


def daily_buybacks_usd(slugs):
    """Sum daily holders revenue across all protocols belonging to one token."""
    parts = []
    for slug in slugs:
        try:
            parts.append(daily_holders_revenue(slug))
        except Exception as e:
            log.warning("defillama %s failed: %s", slug, e)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).fillna(0).sum(axis=1).sort_index()
