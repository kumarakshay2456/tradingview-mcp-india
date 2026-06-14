#!/usr/bin/env python3
"""Regenerate bundled NSE/BSE symbol lists from live TradingView data.

Writes the top ~1,000 most-liquid tickers per exchange into
src/tradingview_mcp/coinlist/{nse,bse}.txt in `EXCHANGE:SYMBOL` format.

Usage:
    python scripts/refresh_india_symbols.py
"""
from __future__ import annotations

import os

from tradingview_screener import Query
from tradingview_screener.column import Column

LIMIT = 1000
COINLIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "tradingview_mcp", "coinlist",
)


def refresh(exchange: str, market: str = "india") -> int:
    q = (
        Query()
        .set_markets(market)
        .select("name", "volume")
        .where(Column("exchange") == exchange)
        .order_by("volume", ascending=False)
        .limit(LIMIT)
    )
    _, df = q.get_scanner_data()
    symbols = [t for t in df["ticker"].tolist() if isinstance(t, str)]
    path = os.path.join(COINLIST_DIR, f"{exchange.lower()}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(symbols) + "\n")
    print(f"{exchange}: wrote {len(symbols)} symbols -> {path}")
    return len(symbols)


if __name__ == "__main__":
    for ex in ("NSE", "BSE"):
        refresh(ex)
