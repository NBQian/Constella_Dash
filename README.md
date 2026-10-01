# Token Factor Dashboard

An interactive [Dash](https://dash.plotly.com/) app that shows how **token buybacks**, **trading
volume** and the **overall crypto market** relate to token prices over the last 365 days.

The app covers 89 tokens. They are the top 50 by market cap, excluding stablecoins, BNB and
wrapped / liquid-staking / tokenized real-world assets, plus the largest tokens with buyback data
until 50 tokens have buybacks. A snapshot of the data is included in `data/`, so the app runs
straight away without any API key.

## Quick start

```
pip install -r requirements.txt
python app.py             # open http://127.0.0.1:8050
```

To refresh the data (about 10–15 minutes):

```
echo "COINGECKO_API_KEY=<your free CoinGecko Demo key>" > .env
python fetch_data.py
```

You can get a free Demo key at <https://www.coingecko.com/en/api>. The key is read from `.env`
(git-ignored) or from the `COINGECKO_API_KEY` environment variable. No VPN is needed. Binance
data comes from Binance's public market-data mirror (`data-api.binance.vision`), which is not
geo-blocked in the US.

## Deploying (Render)

The app runs as a Python **Web Service** (not a Static Site). It only reads the saved data in
`data/`, so no API key or environment variables are needed on the server.

| Setting | Value |
|---|---|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:server` |

To update the deployed data, run `fetch_data.py` locally, commit the new `data/` files and push.
Render then redeploys automatically.

## The dashboard

**Global controls** (top of the page)
- **Token**: the coin to analyse. Tokens without buyback data are marked, and buyback options are
  disabled for them.
- **Frequency**: daily or weekly data. Weeks run Monday–Sunday, and partial weeks are dropped.
- **Market factor n**: how many of the largest tokens make up the market index (1 to 89).

**Abnormal events**: flags unusual days for a chosen variable: buyback % of supply, buyback USD,
volume, price, price return or market return. A day is an event when its value is at or above
the *P*-th percentile of the trailing *L* days (the current day included). You choose *P* and *L*
with the sliders. Events show as red vertical lines on the time series and red diamonds on the
scatter.

**Time series**: price, trading volume, cumulative buyback (% of total supply) and the market
index over time. Each series has its own y-axis, colored like its line. The price axis switches to
log scale automatically when the price moves 20× or more over the period.

**Scatter**: the token's price (y) against a factor (x): buyback % of supply, buyback USD, trading
volume or the market. Each axis can be shown as a **level**, **log return** `ln(x_t / x_{t-1})` or
**simple return** `x_t / x_{t-1} − 1`. The fitted OLS line `y = α + β·x` is shown as an equation,
with standard errors in parentheses under each coefficient, plus Pearson / Spearman correlation and
R².

**Beta ranking**: repeats the scatter's regression for every token with the same settings and
ranks them by β, showing standard error, t-stat, p-value, α, R² and the number of observations.

## Data

### Sources

| Series | Source |
|---|---|
| Token universe, market cap, supply | CoinGecko `/coins/markets` (category lists are used for the exclusions) |
| Daily close price & volume | Binance spot `<SYMBOL>USDT` klines (close + USDT quote volume). Falls back to CoinGecko `market_chart` when there is no Binance pair, Binance covers < 95% of the window, or Binance's price differs from CoinGecko's by > 10% (a different token with the same symbol) |
| Daily buybacks (USD) | DeFiLlama `dailyHoldersRevenue`, summed across all of a token's protocols |

### How the universe is built

1. Take the largest tokens by market cap and drop stablecoins, BNB, wrapped, liquid-staking and
   tokenized-asset tokens. Also drop tokens whose annualized volatility is below 10%, which catches
   stable-value assets missing from those categories. Keep the first 50.
2. Find tokens whose DeFiLlama holders revenue was at least $100k over the last year. Add the
   largest of them by market cap until 50 tokens in the universe have buyback data.

### Files in `data/`

| File | Contents |
|---|---|
| `universe.csv` | One row per token: id, symbol, name, market cap and rank, supply, `segment` (`top` = top 50, `buyback` = added for buyback data), `has_buybacks`, DeFiLlama slugs, price source |
| `prices.parquet` | Daily `date`, `price` (USD close), `volume_usd`, `id` |
| `buybacks.parquet` | Daily `date`, `buyback_usd`, `id` (tokens with buyback data only) |
| `meta.json` | When the data was fetched (UTC) and the fetch settings |
| `excluded_ids.json` | Cached list of excluded CoinGecko ids, used if a category request fails |

All dates are UTC. Each row is a completed trading day, and the window is the 365 days before the
fetch.

## Method notes

- **Buybacks**: DeFiLlama's "holders revenue" is the buyback proxy. For some protocols (e.g. SKY,
  LINK staking) it also includes staking distributions or burns. Buyback tokens = USD buyback ÷
  daily close. % of supply uses the current total supply, because historical supply isn't available.
- **Returns on buybacks** use `1 + x` in place of `x`, so days with no buyback stay defined.
- **Market index** = equal-weighted average return of the top *n* tokens by market cap, rebalanced
  every period. Each period averages over the tokens that have data, so tokens listed partway
  through the year join when their history starts. The index is set to 100 at the start of the data
  window and has its own axis, so a given date has the same value on every token's chart. In the
  scatter, "level" uses this index and the return options use the average return.
- **Weekly data**: price = the week's last close; volume and buybacks are summed over the week.
- **Regressions**: OLS that needs at least 10 observations. Regressions of one level on another
  (e.g. price level on market level) can show a relationship that isn't really there, because both
  series drift over time. Prefer the return options for inference.
- **Abnormal events** are always computed on daily data. In weekly mode, a week counts as an event
  week if it contains an event day. The first *L*−1 days can never be events.

## Project structure

```
app.py              Dash layout and callbacks
analytics.py        Loading the cached data, returns, market index, regressions, event detection
fetch_data.py       Downloads the universe, prices and buybacks into data/
config.py           Settings: universe sizes, exclusions, thresholds, API URLs
data_sources/       Small clients for CoinGecko, Binance and DeFiLlama (shared retry logic in http.py)
assets/style.css    Page styling (loaded automatically by Dash)
data/               Cached data snapshot used by the app
```

## Data credits

Market data from [CoinGecko](https://www.coingecko.com), prices and volume from
[Binance](https://www.binance.com), and holders-revenue (buyback) data from
[DeFiLlama](https://defillama.com). Each provider's terms of use apply to its data.
