"""Project-wide constants."""
import os
from pathlib import Path


def _load_dotenv(path=Path(__file__).parent / ".env"):
    """Load KEY=VALUE lines from .env into the environment (real env vars take precedence)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

N_TOKENS = 50            # top-N by market cap
N_BUYBACK_TOKENS = 50    # coins with buyback data are added until this many are covered
MIN_BUYBACK_USD_1Y = 100_000  # a token "has buybacks" if holders revenue >= this over 1y
DAYS = 365

DATA_DIR = Path(__file__).parent / "data"
UNIVERSE_FILE = DATA_DIR / "universe.csv"
PRICES_FILE = DATA_DIR / "prices.parquet"
BUYBACKS_FILE = DATA_DIR / "buybacks.parquet"
META_FILE = DATA_DIR / "meta.json"
EXCLUDED_CACHE_FILE = DATA_DIR / "excluded_ids.json"

COINGECKO_URL = "https://api.coingecko.com/api/v3"
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY")  # Demo key, from .env or env var
# Binance's public market-data mirror: same klines as api.binance.com, not geo-blocked.
BINANCE_URL = "https://data-api.binance.vision"
DEFILLAMA_URL = "https://api.llama.fi"

# Explicit exclusions (CoinGecko ids).
EXCLUDED_IDS = {"binancecoin"}

# CoinGecko categories whose members are excluded: stablecoins (incl. yield-bearing
# and non-USD ones), wrapped / liquid-staking tokens that mirror another asset, and
# tokenized off-chain assets (treasuries, money-market funds, credit, commodities).
EXCLUDED_CATEGORIES = [
    "stablecoins",
    "fiat-backed-stablecoin",
    "eur-stablecoin",
    "yield-bearing-stablecoins",
    "wrapped-tokens",
    "liquid-staking-tokens",
    "tokenized-btc",
    "tokenized-gold",
    "tokenized-commodities",
    "tokenized-treasuries",
    "tokenized-t-bills",
    "tokenized-money-market-fund-mmfs",
    "tokenized-credit",
    "tokenized-private-credit",
    "tokenized-stock",
]

# Binance data is rejected (CoinGecko used instead) if its live price deviates from
# CoinGecko's by more than this (guards against symbol collisions between unrelated
# tokens), or if it covers less than MIN_COVERAGE of the window (recent listings).
MAX_PRICE_MISMATCH = 0.1
MIN_COVERAGE = 0.95

# Catch-all for stable-value assets missing from the categories above (e.g. tokenized
# funds): tokens whose annualized volatility of daily log returns is below this are
# dropped and replaced by the next-ranked token.
MIN_ANNUAL_VOL = 0.10
# Extra ranked candidates fetched so dropped tokens can be backfilled.
CANDIDATE_BUFFER = 15
