"""DeFiLlama: daily token buybacks and revenue (USD).

DeFiLlama's free API has no standalone buyback series. We use "Holders Revenue",
which DeFiLlama defines as value returned to token holders — for most tokens this
is buybacks (and burns), though for some it also includes staking distributions.

"Revenue" is the part of fees the protocol keeps (treasury, holders, …); holders
revenue is a subset of it. Chains (L1/L2) report revenue too (e.g. burned fees).
"""
import logging

import pandas as pd

import config
from data_sources.http import get_json

log = logging.getLogger(__name__)


def _overview(data_type="dailyHoldersRevenue"):
    return get_json(
        f"{config.DEFILLAMA_URL}/overview/fees",
        params=dict(dataType=data_type, excludeTotalDataChart="true",
                    excludeTotalDataChartBreakdown="true"),
    )["protocols"]


def _gecko_maps():
    """DeFiLlama parent-protocol id -> CoinGecko id, and protocol defillamaId -> CoinGecko id."""
    lite = get_json(f"{config.DEFILLAMA_URL}/lite/protocols2")
    parents = {p["id"]: p["gecko_id"] for p in lite["parentProtocols"] if p.get("gecko_id")}
    singles = {str(p["defillamaId"]): p["geckoId"] for p in lite["protocols"] if p.get("geckoId")}
    return parents, singles


def buyback_protocols(min_usd_1y=config.MIN_BUYBACK_USD_1Y):
    """All tokens with DeFiLlama holders revenue, keyed by CoinGecko id.

    Returns {gecko_id: {"slugs": [...], "usd_1y": float}} for tokens whose protocols
    returned at least `min_usd_1y` to holders over the last year.
    """
    parents, singles = _gecko_maps()

    tokens = {}
    for p in _overview("dailyHoldersRevenue"):
        usd = p.get("total1y") or 0
        if usd <= 0:
            continue
        gid = parents.get(p.get("parentProtocol")) or singles.get(str(p.get("defillamaId")))
        if gid:
            t = tokens.setdefault(gid, {"slugs": [], "usd_1y": 0.0})
            t["slugs"].append(p["slug"])
            t["usd_1y"] += usd
    return {gid: t for gid, t in tokens.items() if t["usd_1y"] >= min_usd_1y}


def revenue_sources(ids, min_usd_1y=config.MIN_REVENUE_USD_1Y):
    """Revenue sources for the given CoinGecko ids: {gecko_id: {"slugs", "kind", "usd_1y"}}.

    Protocol revenue is used when DeFiLlama tracks a protocol for the coin; otherwise chain
    revenue (L1/L2 fees kept or burned), matched to the coin via DeFiLlama's chain list.
    Sources below `min_usd_1y` over the last year are ignored.
    """
    ids = set(ids)
    parents, singles = _gecko_maps()
    chain_gecko = {c["name"].lower(): c.get("gecko_id")
                   for c in get_json(f"{config.DEFILLAMA_URL}/v2/chains")}
    protocol, chain = {}, {}
    for p in _overview("dailyRevenue"):
        usd = p.get("total1y") or 0
        if usd <= 0:
            continue
        if p.get("protocolType") == "chain":
            gid = chain_gecko.get(p["name"].lower()) or chain_gecko.get((p.get("displayName") or "").lower())
            target = chain
        else:
            gid = parents.get(p.get("parentProtocol")) or singles.get(str(p.get("defillamaId")))
            target = protocol
        if gid in ids:
            t = target.setdefault(gid, {"slugs": [], "usd_1y": 0.0})
            t["slugs"].append(p["slug"])
            t["usd_1y"] += usd
    out = {gid: {**t, "kind": "protocol"} for gid, t in protocol.items()}
    out.update({gid: {**t, "kind": "chain"} for gid, t in chain.items() if gid not in out})
    return {gid: t for gid, t in out.items() if t["usd_1y"] >= min_usd_1y}


def daily_holders_revenue(slug, data_type="dailyHoldersRevenue"):
    data = get_json(f"{config.DEFILLAMA_URL}/summary/fees/{slug}",
                    params=dict(dataType=data_type))
    chart = data.get("totalDataChart") or []
    s = pd.Series({pd.to_datetime(ts, unit="s"): float(v) for ts, v in chart}, dtype=float)
    s.index.name = "date"
    return s


def daily_buybacks_usd(slugs, data_type="dailyHoldersRevenue"):
    """Sum a daily DeFiLlama series (holders revenue by default) across all protocols
    belonging to one token."""
    parts = []
    for slug in slugs:
        try:
            parts.append(daily_holders_revenue(slug, data_type))
        except Exception as e:
            log.warning("defillama %s failed: %s", slug, e)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).fillna(0).sum(axis=1).sort_index()
