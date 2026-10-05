"""CoinGecko: token universe, supply, and fallback daily price/volume."""
import json
import logging
import time
from functools import lru_cache

import pandas as pd

import config
from data_sources.http import get_json

log = logging.getLogger(__name__)

# Free tier allows roughly 5-30 calls/min; stay well under it.
_MIN_INTERVAL = 2.5 if config.COINGECKO_API_KEY else 6.0
_last_call = 0.0


def _get(path, **params):
    global _last_call
    wait = _MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    headers = {"x-cg-demo-api-key": config.COINGECKO_API_KEY} if config.COINGECKO_API_KEY else None
    try:
        return get_json(f"{config.COINGECKO_URL}{path}", params=params, headers=headers)
    finally:
        _last_call = time.time()


def _markets(pages=1, category=None):
    rows = []
    for page in range(1, pages + 1):
        params = dict(vs_currency="usd", order="market_cap_desc", per_page=250, page=page)
        if category:
            params["category"] = category
        batch = _get("/coins/markets", **params)
        rows.extend(batch)
        if len(batch) < 250:
            break
    return rows


@lru_cache(maxsize=1)
def excluded_ids():
    """Stablecoins, BNB, wrapped / liquid-staking / tokenized-asset tokens.

    The list is saved to disk after a successful fetch. If any category can't be
    fetched, the saved list is used instead; with no saved list we stop rather than
    risk letting stablecoins into the universe.
    """
    excluded, failed = set(config.EXCLUDED_IDS), []
    for cat in config.EXCLUDED_CATEGORIES:
        try:
            ids = {c["id"] for c in _markets(pages=2, category=cat)}
            log.info("category %s: %d tokens excluded", cat, len(ids))
            excluded |= ids
        except Exception as e:
            log.warning("could not load category %s: %s", cat, e)
            failed.append(cat)
    cache = config.EXCLUDED_CACHE_FILE
    if not failed:
        cache.write_text(json.dumps(sorted(excluded)))
        return frozenset(excluded)
    if cache.exists():
        saved = set(json.loads(cache.read_text()))
        log.warning("using saved exclusion list (%d ids) for failed categories: %s",
                    len(saved), ", ".join(failed))
        return frozenset(excluded | saved)
    raise RuntimeError(f"could not load excluded categories {failed} and no saved list exists; "
                       "retry later or set COINGECKO_API_KEY")


def _to_frame(coins):
    df = pd.DataFrame(coins)
    df = df[["id", "symbol", "name", "market_cap", "market_cap_rank", "current_price",
             "circulating_supply", "total_supply", "max_supply"]].copy()
    df["symbol"] = df["symbol"].str.upper()
    # Denominator for "buyback as % of supply": total supply, else max, else circulating.
    df["supply"] = df["total_supply"].fillna(df["max_supply"]).fillna(df["circulating_supply"])
    return df.reset_index(drop=True)


def top_universe(n=config.N_TOKENS + config.CANDIDATE_BUFFER):
    """Top-n coins by market cap, excluding stablecoins, BNB, and derivative tokens."""
    excluded = excluded_ids()
    return _to_frame([c for c in _markets(pages=1) if c["id"] not in excluded][:n])


def markets_by_ids(ids):
    """Market data for specific coins, excluded categories removed, sorted by market cap."""
    excluded = excluded_ids()
    ids = [i for i in ids if i not in excluded]
    coins = []
    for i in range(0, len(ids), 250):
        coins += _get("/coins/markets", vs_currency="usd", ids=",".join(ids[i:i + 250]),
                      per_page=250)
    coins = [c for c in coins if c.get("market_cap")]
    coins.sort(key=lambda c: -c["market_cap"])
    return _to_frame(coins)


def coin_categories(coin_id):
    """CoinGecko's category tags for one coin (e.g. "Lending/Borrowing Protocols")."""
    data = _get(f"/coins/{coin_id}", localization="false", tickers="false", market_data="false",
                community_data="false", developer_data="false", sparkline="false")
    return data.get("categories") or []


def daily_market_chart(coin_id, days=config.DAYS):
    """Daily close price and 24h volume (USD), indexed by UTC date of the trading day.

    CoinGecko's daily point at 00:00 UTC on day D is the close / trailing-24h volume
    of day D-1, so dates are shifted back one day to match Binance kline dates.
    """
    data = _get(f"/coins/{coin_id}/market_chart", vs_currency="usd", days=days, interval="daily")
    prices = pd.DataFrame(data["prices"], columns=["ts", "price"])
    vols = pd.DataFrame(data["total_volumes"], columns=["ts", "volume_usd"])
    df = prices.merge(vols, on="ts")
    ts = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df[(ts.dt.hour == 0) & (ts.dt.minute == 0)]  # drop the intraday "now" point
    df["date"] = (pd.to_datetime(df["ts"], unit="ms").dt.normalize() - pd.Timedelta(days=1))
    return df.drop(columns="ts").drop_duplicates("date", keep="last").set_index("date")
