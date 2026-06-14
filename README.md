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

### Stock suggestion engine (NSE/BSE)

AI-assisted LONG trade ideas — each with entry (CMP), stop-loss, two targets,
risk/reward, a 0–100 conviction score, and a plain-English rationale:

```
india_swing_picks(exchange="NSE", top_n=5)                 # 2-7 day swing ideas (daily TF)
india_swing_picks(direction="short")                       # bearish setups
india_swing_picks(index_filter="NIFTY50")                  # restrict to an index universe
india_intraday_signals(exchange="NSE", top_n=5)            # same-session ideas (15m, VWAP-aware)
india_trade_plan("AXISBANK", mode="swing", direction="auto")  # full plan for one stock
india_backtest("TCS", period="2y")                         # validate vs history (6-strategy leaderboard)
india_swing_picks(capital=200000, risk_pct=1.5)            # add position sizing (qty + ₹ P&L)
```

- **Position sizing**: pass `capital` (INR) and `risk_pct` to any idea tool. Each idea then
  carries `position_sizing`: share `quantity` sized so a stop-out loses ~`risk_pct` of capital
  (capped by capital), plus `position_value`, rupee `loss_at_stop`, and `profit_at_t1/t2`.
  Short sizing is notional (real shorting needs margin).

- **direction**: `"auto"` (long uptrends / short downtrends), `"long"`, or `"short"`. Short
  setups use a dedicated bearish momentum/quality scorer (the shared engine is long-biased)
  and inverted levels (stop above entry, targets below).
- **index_filter**: `"NIFTY50"`, `"NIFTYBANK"`, `"NIFTYNEXT50"` — constituents bundled in
  `core/data/india_indices.py` (refresh on NSE rebalance).
- **india_backtest**: maps the symbol to Yahoo (`.NS`/`.BO`) and runs all 6 strategies as a
  robustness check on whether the name respects technical setups.

How it works: scans the most-liquid NSE/BSE stocks, scores momentum
(`compute_stock_score`) and setup tradability (`compute_trade_quality`), builds
levels via `compute_trade_setup`, then layers on a conviction blend, a directional
gate (long-only; downtrends filtered out), and a generated rationale. ATR and
average volume are backfilled from `tradingview_screener` because `tradingview_ta`
omits them — without this the trade-setup engine produced no levels at all (this
gap also silently affected the upstream EGX setup path). Stops are floored
(≥0.5% swing / ≥0.6% intraday) so signals aren't stopped out by noise.
Educational analysis only — not investment advice.

The `egx_*` tools are Egypt-specific and do not apply to India.

## Refreshing the symbol lists

```bash
python scripts/refresh_india_symbols.py    # NSE/BSE liquid universe (coinlist/*.txt)
python scripts/refresh_india_indices.py    # Nifty 50 / Bank Nifty / Next 50 constituents (official NSE CSVs)
```

`refresh_india_indices.py` writes `core/data/india_indices.json`, which overrides the
bundled constituent lists. Run it after an NSE index rebalance.

## Run the MCP server

```bash
tradingview-mcp            # stdio transport (default)
tradingview-mcp streamable-http --host 127.0.0.1 --port 8000
```
