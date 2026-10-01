"""Download token universe, prices, volumes, and buybacks into data/.

Universe = top N_TOKENS coins by market cap, plus the largest-market-cap coins with
DeFiLlama buyback data until N_BUYBACK_TOKENS coins have buybacks.

Usage: python fetch_data.py
"""
import json
import logging

import numpy as np
import pandas as pd

import config
from data_sources import binance, coingecko, defillama

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch")


class PriceFetcher:
    def __init__(self):
        self.binance_bases = binance.usdt_symbols()
        self.today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
        self.start = self.today - pd.Timedelta(days=config.DAYS)

    def _in_window(self, d):
        return d[(d.index >= self.start) & (d.index < self.today)].copy()

    def fetch(self, row):
        """Daily price/volume frame and source name, or (None, None) if unusable."""
        df, source = None, None
        if row.symbol in self.binance_bases:
            try:
                mismatch = abs(binance.live_price(row.symbol) / row.current_price - 1)
                if mismatch > config.MAX_PRICE_MISMATCH:
                    log.warning("%s: Binance price off by %.0f%% vs CoinGecko, likely a "
                                "different token", row.symbol, mismatch * 100)
                else:
                    df, source = self._in_window(binance.daily_klines(row.symbol)), "Binance"
            except Exception as e:
                log.warning("%s: Binance failed (%s)", row.symbol, e)
        if df is None or len(df) < config.MIN_COVERAGE * config.DAYS:
            try:
                cg = self._in_window(coingecko.daily_market_chart(row.id))
                if df is None or len(cg) > len(df):
                    if df is not None:
                        log.info("%s: Binance has only %d days; using CoinGecko", row.symbol, len(df))
                    df, source = cg, "CoinGecko"
            except Exception as e:
                log.error("%s: CoinGecko failed (%s)", row.symbol, e)
        if df is None or len(df) < 30:
            log.error("%s: no usable price data", row.symbol)
            return None, None
        vol = np.log(df["price"]).diff().std() * np.sqrt(365)
        if vol < config.MIN_ANNUAL_VOL:
            log.info("%s: annualized vol %.1f%% — stable-value asset, skipped", row.symbol, vol * 100)
            return None, None
        df["id"] = row.id
        log.info("%-8s %-9s %d days", row.symbol, source, len(df))
        return df.reset_index(), source


def fetch_buyback(gid, info):
    s = defillama.daily_buybacks_usd(info["slugs"])
    if s.empty or s.sum() <= 0:
        return None
    return pd.DataFrame({"date": s.index, "buyback_usd": s.values, "id": gid})


def main():
    config.DATA_DIR.mkdir(exist_ok=True)
    fetcher = PriceFetcher()
    bb_info = defillama.buyback_protocols()
    log.info("%d tokens with >= $%s holders revenue in the last year",
             len(bb_info), f"{config.MIN_BUYBACK_USD_1Y:,.0f}")

    rows, prices, buybacks, sources = [], [], [], {}

    def add(row, segment, df, source, bb):
        prices.append(df)
        sources[row.id] = source
        if bb is not None:
            buybacks.append(bb)
        rows.append({**row._asdict(), "segment": segment, "has_buybacks": bb is not None,
                     "defillama_slugs": ";".join(bb_info[row.id]["slugs"]) if bb is not None else ""})

    # 1) Top N by market cap.
    log.info("building top-%d universe", config.N_TOKENS)
    top = coingecko.top_universe()
    n_top = 0
    for row in top.itertuples(index=False):
        if n_top == config.N_TOKENS:
            break
        df, source = fetcher.fetch(row)
        if df is None:
            continue
        add(row, "top", df, source, fetch_buyback(row.id, bb_info[row.id]) if row.id in bb_info else None)
        n_top += 1
    n_bb = sum(r["has_buybacks"] for r in rows)
    log.info("top %d: %d with buybacks", config.N_TOKENS, n_bb)

    # 2) Add the largest-market-cap buyback coins until N_BUYBACK_TOKENS have buyback data.
    candidates = coingecko.markets_by_ids([g for g in bb_info if g not in set(top["id"])])
    log.info("%d buyback candidates outside the top %d", len(candidates), config.N_TOKENS)
    for row in candidates.itertuples(index=False):
        if n_bb >= config.N_BUYBACK_TOKENS:
            break
        bb = fetch_buyback(row.id, bb_info[row.id])
        if bb is None:
            continue
        df, source = fetcher.fetch(row)
        if df is None:
            continue
        add(row, "buyback", df, source, bb)
        n_bb += 1

    universe = pd.DataFrame(rows).sort_values("market_cap", ascending=False).reset_index(drop=True)
    universe["price_source"] = universe["id"].map(sources)
    universe.to_csv(config.UNIVERSE_FILE, index=False)
    pd.concat(prices, ignore_index=True).to_parquet(config.PRICES_FILE, index=False)
    bb_frame = (pd.concat(buybacks, ignore_index=True) if buybacks
                else pd.DataFrame(columns=["date", "buyback_usd", "id"]))
    bb_frame.to_parquet(config.BUYBACKS_FILE, index=False)
    config.META_FILE.write_text(json.dumps({
        "fetched_at_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "days": config.DAYS,
        "n_top": config.N_TOKENS,
        "min_buyback_usd_1y": config.MIN_BUYBACK_USD_1Y,
    }, indent=2))
    log.info("done: %d tokens (%d top-%d + %d buyback additions), %d with buybacks",
             len(universe), (universe["segment"] == "top").sum(), config.N_TOKENS,
             (universe["segment"] == "buyback").sum(), universe["has_buybacks"].sum())
    log.info("universe: %s", ", ".join(universe["symbol"]))


if __name__ == "__main__":
    main()
