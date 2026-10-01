"""Binance spot daily klines."""
import pandas as pd

import config
from data_sources.http import get_json


def usdt_symbols():
    """Set of base assets with an actively trading <BASE>USDT spot pair."""
    info = get_json(f"{config.BINANCE_URL}/api/v3/exchangeInfo", params={"permissions": "SPOT"})
    return {
        s["baseAsset"]
        for s in info["symbols"]
        if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
    }


def live_price(base):
    return float(get_json(f"{config.BINANCE_URL}/api/v3/ticker/price",
                          params={"symbol": f"{base}USDT"})["price"])


def daily_klines(base, days=config.DAYS):
    """Daily close and quote-asset (USDT) volume for completed UTC days."""
    today = pd.Timestamp.now(tz="UTC").normalize()
    start = today - pd.Timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(today.timestamp() * 1000) - 1  # exclude today's incomplete candle
    rows = []
    while start_ms <= end_ms:
        batch = get_json(
            f"{config.BINANCE_URL}/api/v3/klines",
            params=dict(symbol=f"{base}USDT", interval="1d", startTime=start_ms,
                        endTime=end_ms, limit=1000),
        )
        if not batch:
            break
        rows.extend(batch)
        start_ms = batch[-1][0] + 86_400_000
    df = pd.DataFrame(rows).iloc[:, [0, 4, 7]]
    df.columns = ["open_time", "price", "volume_usd"]
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df[["price", "volume_usd"]] = df[["price", "volume_usd"]].astype(float)
    return df.drop(columns="open_time").set_index("date")
