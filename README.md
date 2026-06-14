# tradingview-mcp-india

A fork of [`tradingview-mcp-server`](https://github.com/atilaahmettaner/tradingview-mcp)
by Atila Ahmettaner (MIT), with **Indian stock-market (NSE / BSE) support added**.

## What this fork adds

- Registered `NSE` and `BSE` as stock exchanges mapped to TradingView's `india` market
  (`src/tradingview_mcp/core/utils/validators.py`).
- Bundled symbol lists `coinlist/nse.txt` and `coinlist/bse.txt`
  (top ~1,000 most-liquid tickers each), so symbol-iterating tools work too.
- **Indian news**: new `india` RSS category (Economic Times, Moneycontrol, LiveMint,
  Hindu BusinessLine) and a dedicated `india_news` MCP tool.
- **Indian sentiment**: new `india` Reddit group (r/IndianStockMarket, r/IndianStreetBets,
  r/DalalStreetTalks, r/StockMarketIndia, r/IndiaInvestments).
- **Indian indices in `market_snapshot`**: Nifty 50 (`^NSEI`), Sensex (`^BSESN`),
  Bank Nifty (`^NSEBANK`), plus USDINR FX.
- **`combined_analysis` routing**: NSE/BSE now pull Indian news + Indian sentiment
  (previously fell through to Reuters/US subreddits and returned nothing).
- **SSL reliability fix (important)**: all outbound HTTPS (Yahoo Finance, Reddit, RSS) now
  uses a certifi-backed SSL context — via `proxy_manager._https_handler()` for the shared
  opener and a dedicated fetch in `news_service`, plus browser User-Agent and manual HTTP 308
  redirect following. Without this, the macOS `CERTIFICATE_VERIFY_FAILED` error silently broke
  *every* network tool (Yahoo price, snapshot, backtest, sentiment, news) — not just India.

Everything else is unchanged from upstream v0.7.1.

## Install (editable)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -e .
```

Editable means your local edits to `src/` are live immediately — no reinstall, and
a `pip install --upgrade` of the original PyPI package can never overwrite this fork.

## Usage for Indian markets

Screener / technical-analysis tools — pass `exchange="NSE"` (or `"BSE"`), use `1D`/`1W` timeframes:

```
top_gainers(exchange="NSE", timeframe="1D")
coin_analysis(symbol="RELIANCE", exchange="NSE", timeframe="1D")
multi_agent_analysis(symbol="INFY", exchange="NSE", timeframe="1D")
```

Yahoo / backtest tools — use the `.NS` (NSE) or `.BO` (BSE) suffix:

```
backtest_strategy("TCS.NS", "rsi", "1y")
compare_strategies("INFY.NS")
```

Indian news:

```
india_news(limit=10)                 # all India market headlines
india_news(symbol="RELIANCE")        # only headlines mentioning RELIANCE
financial_news(category="india")     # same feeds via the generic tool
```

The `egx_*` tools are Egypt-specific and do not apply to India.

## Refreshing the symbol lists

```bash
python scripts/refresh_india_symbols.py
```

## Run the MCP server

```bash
tradingview-mcp            # stdio transport (default)
tradingview-mcp streamable-http --host 127.0.0.1 --port 8000
```
