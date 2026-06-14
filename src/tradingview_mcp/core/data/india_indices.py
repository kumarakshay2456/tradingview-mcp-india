"""
Indian index constituents — for filtering the suggestion engine to a known universe
(e.g. Nifty 50 only). Symbols are NSE tickers WITHOUT the exchange prefix.

NOTE: index membership changes on periodic rebalances (NSE reviews semi-annually).
These lists reflect a recent snapshot; treat them as a curated universe, not a
real-time index feed. Refresh when NSE announces reconstitution.
"""
from __future__ import annotations

from typing import Dict, List

NIFTY_50: List[str] = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ_AUTO",
    "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY",
    "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY", "ITC", "JSWSTEEL",
    "JIOFIN", "KOTAKBANK", "LT", "M_M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TCS",
    "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TECHM", "TITAN", "TRENT", "ULTRACEMCO",
    "WIPRO",
]

NIFTY_BANK: List[str] = [
    "AXISBANK", "AUBANK", "BANKBARODA", "CANBK", "FEDERALBNK", "HDFCBANK", "ICICIBANK",
    "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK", "PNB", "SBIN",
]

# Nifty Next 50 (the 50 names just below Nifty 50; high-quality large/mid caps).
NIFTY_NEXT_50: List[str] = [
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "BAJAJHLDNG",
    "BANKBARODA", "BERGEPAINT", "BPCL", "BOSCHLTD", "BRITANNIA", "CGPOWER", "CHOLAFIN",
    "COLPAL", "DABUR", "DIVISLAB", "DLF", "DMART", "GAIL", "GODREJCP", "HAVELLS",
    "HAL", "ICICIGI", "ICICIPRULI", "INDHOTEL", "IOC", "IRFC", "JSWENERGY", "LTIM",
    "LODHA", "MARICO", "MOTHERSON", "NAUKRI", "PIDILITIND", "PFC", "PNB", "RECLTD",
    "SIEMENS", "SHREECEM", "TATAPOWER", "TORNTPHARM", "TVSMOTOR", "UNITDSPR", "VBL",
    "VEDL", "ZYDUSLIFE",
]

# index_filter key → constituent list. Keys are case-insensitive in the resolver.
INDEX_CONSTITUENTS: Dict[str, List[str]] = {
    "NIFTY50": NIFTY_50,
    "NIFTYBANK": NIFTY_BANK,
    "BANKNIFTY": NIFTY_BANK,
    "NIFTYNEXT50": NIFTY_NEXT_50,
}

# Live override: if scripts/refresh_india_indices.py has written a fresh snapshot to
# india_indices.json (next to this module), use it instead of the bundled defaults.
def _load_override() -> None:
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "india_indices.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return
    for key, syms in data.items():
        if isinstance(syms, list) and syms:
            k = key.strip().upper()
            INDEX_CONSTITUENTS[k] = syms
            if k == "NIFTYBANK":
                INDEX_CONSTITUENTS["BANKNIFTY"] = syms


_load_override()


def get_index_symbols(index_name: str, exchange_code: str = "NSE") -> List[str]:
    """Return exchange-prefixed tickers for an index, or [] if unknown."""
    key = (index_name or "").strip().upper().replace(" ", "").replace("_", "")
    names = INDEX_CONSTITUENTS.get(key)
    if not names:
        return []
    return [f"{exchange_code}:{s}" for s in names]


def available_indices() -> List[str]:
    return sorted(set(INDEX_CONSTITUENTS.keys()))
