#!/usr/bin/env python3
"""Refresh Indian index constituents from the official NSE (niftyindices.com) CSVs.

Writes src/tradingview_mcp/core/data/india_indices.json, which india_indices.py
loads as an override of its bundled defaults. Run after an NSE index rebalance.

Usage:
    python scripts/refresh_india_indices.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import ssl
import urllib.request

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _CTX = ssl.create_default_context()

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# index key → official niftyindices constituent CSV
_SOURCES = {
    "NIFTY50": "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "NIFTYBANK": "https://niftyindices.com/IndexConstituent/ind_niftybanklist.csv",
    "NIFTYNEXT50": "https://niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
}

_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "tradingview_mcp", "core", "data", "india_indices.json",
)


def _to_tv_symbol(nse_symbol: str) -> str:
    """NSE 'Symbol' (e.g. 'M&M', 'BAJAJ-AUTO') → TradingView form ('M_M', 'BAJAJ_AUTO')."""
    return re.sub(r"[^A-Z0-9]", "_", nse_symbol.strip().upper())


def fetch(url: str) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    raw = urllib.request.urlopen(req, timeout=20, context=_CTX).read().decode("utf-8-sig")
    rows = csv.DictReader(io.StringIO(raw))
    return [_to_tv_symbol(r["Symbol"]) for r in rows if r.get("Symbol")]


def main() -> None:
    out = {}
    for key, url in _SOURCES.items():
        try:
            syms = fetch(url)
            if syms:
                out[key] = syms
                print(f"{key}: {len(syms)} constituents")
            else:
                print(f"{key}: empty — skipped")
        except Exception as exc:
            print(f"{key}: FAILED ({exc}) — keeping previous/bundled list")

    if not out:
        print("No data fetched; nothing written.")
        return

    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {_OUT}")


if __name__ == "__main__":
    main()
