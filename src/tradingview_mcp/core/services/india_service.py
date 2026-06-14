"""
India Suggestion Service — AI-assisted stock suggestion engine for NSE / BSE.

Generates actionable LONG trade ideas in two modes:
  - "swing":    2-7 trading-day holds, analysed on the daily (1D) timeframe.
  - "intraday": same-session ideas, analysed on an intraday timeframe (default 15m).

Each idea carries: entry price (+ alternate entry), stop-loss, two targets,
risk/reward, a 0-100 conviction score with a label, and a plain-English rationale.

It reuses the shared technical engine in `indicators.py`
(compute_stock_score / compute_trade_setup / compute_trade_quality) — the same
machinery the EGX tools use — and adds the conviction + rationale layer plus the
intraday/swing framing on top.

All functions return plain dicts/lists and are independently testable.

NOTE: Educational/informational analysis only — NOT investment advice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from tradingview_mcp.core.services.coinlist import load_symbols
from tradingview_mcp.core.services.indicators import (
    compute_metrics,
    compute_stock_score,
    compute_trade_setup,
    compute_trade_quality,
    analyze_timeframe_context,
)
from tradingview_mcp.core.utils.validators import EXCHANGE_SCREENER

try:
    from tradingview_ta import get_multiple_analysis
    _TA_AVAILABLE = True
except ImportError:
    _TA_AVAILABLE = False

try:
    from tradingview_screener import Query
    from tradingview_screener.column import Column
    _SCREENER_AVAILABLE = True
except ImportError:
    _SCREENER_AVAILABLE = False

_DISCLAIMER = (
    "Educational/informational analysis only — NOT investment advice. "
    "Markets carry risk; verify independently and manage position size."
)

# Mode → default analysis timeframe + holding guidance.
_MODE_DEFAULTS = {
    "swing": {"timeframe": "1D", "hold": "2-7 trading days"},
    "intraday": {"timeframe": "15m", "hold": "Intraday — square off before close (15:30 IST)"},
}

_BATCH = 200

# tradingview_ta does NOT return ATR or average volume, but compute_trade_setup
# needs ATR and the scoring/quality needs an average-volume baseline. We backfill
# both from tradingview_screener (which does expose them).
_ATR_COLUMN = {
    "1D": "ATR", "1W": "ATR|1W", "1M": "ATR|1M",
    "4h": "ATR|240", "1h": "ATR|60", "15m": "ATR|15", "5m": "ATR|5",
}
# Approx number of intraday bars per NSE session (~375 trading minutes) — used to
# convert a daily average volume into a per-bar baseline for intraday volume ratios.
_BARS_PER_DAY = {"15m": 25, "5m": 75, "1h": 6, "4h": 2}


# ─── Data fetch ─────────────────────────────────────────────────────────────

def _exchange_meta(exchange: str) -> Tuple[str, str]:
    """Return (EXCHANGE_CODE, screener) for NSE/BSE. Defaults to NSE."""
    ex = (exchange or "NSE").strip().upper()
    if ex not in ("NSE", "BSE"):
        ex = "NSE"
    screener = EXCHANGE_SCREENER.get(ex.lower(), "india")
    return ex, screener


def _fetch_batch(symbols: List[str], screener: str, interval: str) -> Dict[str, Any]:
    """Batch-fetch TradingView TA indicators keyed by full ticker."""
    out: Dict[str, Any] = {}
    for i in range(0, len(symbols), _BATCH):
        batch = symbols[i : i + _BATCH]
        try:
            analysis = get_multiple_analysis(screener=screener, interval=interval, symbols=batch)
        except Exception:
            continue
        for sym, data in (analysis or {}).items():
            if data is not None:
                out[sym] = data.indicators
    return out


def _enrich_atr_volume(indicators_by_symbol: Dict[str, Any], timeframe: str) -> None:
    """In-place backfill of ATR and a volume baseline (volume.SMA20) from the
    screener, since tradingview_ta omits them. Per-bar scaling for intraday."""
    if not _SCREENER_AVAILABLE or not indicators_by_symbol:
        return
    atr_col = _ATR_COLUMN.get(timeframe, "ATR")
    bars = _BARS_PER_DAY.get(timeframe, 1)
    tickers = list(indicators_by_symbol.keys())
    for i in range(0, len(tickers), _BATCH):
        batch = tickers[i : i + _BATCH]
        try:
            q = (
                Query()
                .set_markets("india")
                .select(atr_col, "average_volume_10d_calc")
                .set_tickers(*batch)
            )
            _, df = q.get_scanner_data()
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            sym = row.get("ticker")
            ind = indicators_by_symbol.get(sym)
            if ind is None:
                continue
            atr = row.get(atr_col)
            if atr is not None:
                ind["ATR"] = float(atr)
            avg_vol = row.get("average_volume_10d_calc")
            if avg_vol is not None and avg_vol > 0:
                # Daily avg → per-bar baseline so intraday volume ratios are comparable.
                ind["volume.SMA20"] = float(avg_vol) / bars


# ─── Conviction & rationale ─────────────────────────────────────────────────

def _conviction(
    momentum_score: int, quality_score: int, rr2: Optional[float], bias: str, direction: str
) -> Tuple[int, str]:
    """Blend directional momentum + setup quality, then adjust for reward/risk and
    whether the timeframe trend AGREES with the trade direction. Returns (0-100, label)."""
    base = 0.45 * momentum_score + 0.45 * quality_score
    if rr2 is not None:
        if rr2 >= 2.5:
            base += 6
        elif rr2 >= 2.0:
            base += 3
        elif rr2 < 1.5:
            base -= 8
    aligned = (direction == "long" and bias == "Bullish") or (direction == "short" and bias == "Bearish")
    opposed = (direction == "long" and bias == "Bearish") or (direction == "short" and bias == "Bullish")
    if aligned:
        base += 4
    elif opposed:
        base -= 10
    score = max(0, min(100, round(base)))
    if score >= 80:
        label = "Very High"
    elif score >= 65:
        label = "High"
    elif score >= 50:
        label = "Medium"
    else:
        label = "Low"
    return score, label


def _grade(score: int) -> str:
    if score >= 80:
        return "Strong"
    if score >= 70:
        return "Good"
    if score >= 55:
        return "Watchlist"
    return "Weak"


def _short_momentum_score(ind: Dict[str, Any], change_pct_rank: Optional[float]) -> Tuple[int, List[str]]:
    """Compact 0-100 bearish-momentum score (mirror of compute_stock_score, inverted).
    High = strong, confirmed downtrend = better SHORT candidate."""
    close = ind.get("close")
    ema20, ema50, ema200 = ind.get("EMA20"), ind.get("EMA50"), ind.get("EMA200")
    rsi = ind.get("RSI")
    macd, macd_sig = ind.get("MACD.macd"), ind.get("MACD.signal")
    adx = ind.get("ADX")
    vol, vol_sma = ind.get("volume"), ind.get("volume.SMA20")
    pts = 0
    signals: List[str] = []

    # Bearish EMA structure — 30
    if close and ema20 and ema50 and ema200:
        if close < ema20 < ema50 < ema200:
            pts += 30; signals.append("Perfect bearish EMA alignment (Price<20<50<200)")
        elif close < ema20 < ema50:
            pts += 20; signals.append("Bearish EMA (Price<20<50)")
        elif close < ema20:
            pts += 10; signals.append("Price below EMA20")
    # RSI — 15 (trend-following short wants room to fall, not already oversold)
    if rsi is not None:
        if 35 <= rsi <= 55:
            pts += 15; signals.append(f"RSI {rsi:.0f} in short zone (35-55)")
        elif 55 < rsi <= 65:
            pts += 8
        elif rsi < 30:
            pts += 3; signals.append(f"RSI {rsi:.0f} oversold — bounce risk")
    # MACD bearish — 15
    if macd is not None and macd_sig is not None:
        if macd < macd_sig and (macd - macd_sig) < 0:
            pts += 15; signals.append("MACD bearish + histogram falling")
        elif macd < macd_sig:
            pts += 10; signals.append("MACD bearish crossover")
    # ADX trend strength — 10
    if adx is not None:
        if adx > 25:
            pts += 10
        elif adx > 20:
            pts += 5
    # Volume confirmation — 10
    if vol and vol_sma and vol_sma > 0:
        ratio = vol / vol_sma
        if ratio >= 1.5:
            pts += 10; signals.append(f"Volume {ratio:.1f}x avg (distribution)")
        elif ratio >= 1.2:
            pts += 7
        elif ratio >= 1.0:
            pts += 4
    # Relative weakness — 20 (low percentile = underperformer = better short)
    if change_pct_rank is not None:
        weak = 1.0 - change_pct_rank
        if weak >= 0.90:
            pts += 20; signals.append("Bottom 10% price performer")
        elif weak >= 0.75:
            pts += 15; signals.append("Bottom 25% price performer")
        elif weak >= 0.60:
            pts += 8
        elif weak >= 0.40:
            pts += 4
    return max(0, min(100, pts)), signals


def _short_quality(ind: Dict[str, Any], rr2: Optional[float], stop_pct: Optional[float]) -> Tuple[int, str]:
    """Compact 0-100 tradability score for a SHORT setup."""
    close = ind.get("close")
    ema20, ema50, ema200 = ind.get("EMA20"), ind.get("EMA50"), ind.get("EMA200")
    adx = ind.get("ADX")
    vol, vol_sma = ind.get("volume"), ind.get("volume.SMA20")
    total = 0
    # Bearish structure — 30
    if close and ema20 and ema50 and ema200 and close < ema20 < ema50 < ema200:
        total += 20
    elif close and ema50 and close < ema50:
        total += 10
    if adx and adx > 25:
        total += 10
    elif adx and adx > 20:
        total += 5
    # R:R — 35
    if rr2 is not None:
        if rr2 >= 2.5:
            total += 35
        elif rr2 >= 2.0:
            total += 28
        elif rr2 >= 1.5:
            total += 18
    # Volume — 20
    if vol and vol_sma and vol_sma > 0:
        ratio = vol / vol_sma
        total += 20 if ratio >= 1.5 else 14 if ratio >= 1.2 else 8 if ratio >= 1.0 else 0
    # Stop placement — 15
    if stop_pct is not None and 0.5 <= stop_pct <= 5.0:
        total += 15
    elif stop_pct is not None and stop_pct <= 8.0:
        total += 7
    total = max(0, min(100, total))
    quality = (
        "High Quality Setup" if total >= 80 else
        "Tradable" if total >= 65 else
        "Weak Setup" if total >= 50 else "Avoid Execution"
    )
    return total, quality


def _decide_direction(requested: str, bias: str, close: Optional[float], ema50: Optional[float]) -> Optional[str]:
    """Resolve the trade direction. 'auto' picks long for uptrends, short for
    downtrends, and skips ambiguous names. Returns 'long', 'short', or None."""
    below = bool(ema50 and close and close < ema50)
    above = bool(ema50 and close and close > ema50)
    if requested == "long":
        return "long" if not (bias == "Bearish") and not (below and bias != "Bullish") else None
    if requested == "short":
        return "short" if not (bias == "Bullish") and not (above and bias != "Bearish") else None
    # auto
    if bias == "Bullish" and not below:
        return "long"
    if bias == "Bearish" and not above:
        return "short"
    return None


def _fmt(v: Optional[float]) -> str:
    return f"₹{v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def _position_sizing(
    entry: Optional[float],
    stop_loss: Optional[float],
    t1: Optional[float],
    t2: Optional[float],
    direction: str,
    capital: float,
    risk_pct: float,
) -> Optional[Dict[str, Any]]:
    """Risk-based position size: quantity so that a stop-out loses ~risk_pct of capital,
    capped by available capital. Includes rupee P&L at the stop and both targets."""
    if not entry or capital <= 0 or risk_pct <= 0:
        return None
    risk_per_share = abs(entry - stop_loss) if stop_loss else 0
    if risk_per_share <= 0:
        return None
    risk_budget = capital * (risk_pct / 100.0)
    qty_by_risk = int(risk_budget // risk_per_share)
    qty_by_capital = int(capital // entry)  # notional cap (cash long; margin differs for short)
    qty = max(0, min(qty_by_risk, qty_by_capital))
    if qty == 0:
        return {
            "capital": capital, "risk_pct": risk_pct, "quantity": 0,
            "note": "Risk budget too small for even 1 share at this stop distance — "
                    "increase capital/risk_pct, or skip.",
        }

    position_value = round(qty * entry, 2)
    loss = round(qty * risk_per_share, 2)

    def _pnl(target):
        return round(qty * abs(target - entry), 2) if target else None

    out = {
        "capital": capital,
        "risk_pct": risk_pct,
        "risk_per_share": round(risk_per_share, 2),
        "quantity": qty,
        "position_value": position_value,
        "capital_deployed_pct": round(position_value / capital * 100, 1),
        "risk_amount": loss,
        "loss_at_stop": -loss,
        "profit_at_t1": _pnl(t1),
        "profit_at_t2": _pnl(t2),
    }
    if direction == "short":
        out["note"] = "Short sizing is notional — actual shorting requires margin (intraday/F&O); broker limits apply."
    return out


def _build_rationale(
    name: str,
    mode: str,
    direction: str,
    grade: str,
    momentum_score: int,
    bias: str,
    rsi: Optional[float],
    macd_aligned: Optional[bool],
    vol_ratio: Optional[float],
    entry: Optional[float],
    stop_loss: Optional[float],
    stop_pct: Optional[float],
    t1: Optional[float],
    t2: Optional[float],
    rr2: Optional[float],
    vwap_note: Optional[str],
    quality_label: str,
) -> str:
    """Assemble a plain-English explanation from the computed signals."""
    parts: List[str] = []
    is_short = direction == "short"

    trend_word = {"Bullish": "uptrend", "Bearish": "downtrend", "Neutral": "range"}.get(bias, "trend")
    lean = "short setup" if is_short else "long setup"
    parts.append(
        f"{name} rates {momentum_score}/100 for a {lean} (grade {grade}); the "
        f"{'daily' if mode == 'swing' else 'intraday'} trend is a {bias.lower()} {trend_word}."
    )

    if rsi is not None:
        if is_short:
            if rsi < 30:
                parts.append(f"RSI is {rsi:.0f} (oversold — bounce risk on fresh shorts).")
            elif rsi <= 55:
                parts.append(f"RSI is {rsi:.0f}, leaving room for further downside.")
            else:
                parts.append(f"RSI is {rsi:.0f} (still elevated — short into weakness).")
        else:
            if rsi > 70:
                parts.append(f"RSI is {rsi:.0f} (overbought — hot but stretched).")
            elif rsi >= 50:
                parts.append(f"RSI is {rsi:.0f}, showing healthy momentum without being overbought.")
            elif rsi >= 40:
                parts.append(f"RSI is {rsi:.0f} (neutral-to-soft — watch for a momentum turn).")
            else:
                parts.append(f"RSI is {rsi:.0f} (weak — bounce-trade only).")

    if macd_aligned:
        parts.append(
            "MACD is in a bearish crossover, supporting downside momentum."
            if is_short else
            "MACD is in a bullish crossover, supporting upward momentum."
        )

    if vol_ratio is not None and vol_ratio >= 1.2:
        parts.append(f"Volume is running {vol_ratio:.1f}× its 20-period average, confirming participation.")
    elif vol_ratio is not None and vol_ratio < 0.8:
        parts.append(f"Volume is light ({vol_ratio:.1f}× average) — conviction on the move is limited.")

    if vwap_note:
        parts.append(vwap_note)

    if entry and stop_loss and t1 and t2:
        verb = "short" if is_short else "buy"
        stop_side = "above" if is_short else "below"
        cover = "cover targets" if is_short else "targets"
        parts.append(
            f"Plan: {verb} around {_fmt(entry)} (CMP), stop {stop_side} at {_fmt(stop_loss)}"
            + (f" ({stop_pct:.1f}% risk)" if stop_pct is not None else "")
            + f", {cover} {_fmt(t1)} then {_fmt(t2)}"
            + (f" — about {rr2:.1f}:1 reward-to-risk." if rr2 else ".")
        )

    parts.append(f"Overall: {quality_label.lower()}.")
    return " ".join(parts)


# ─── Idea builder ─────────────────────────────────────────────────────────────

def _build_idea(
    full_symbol: str,
    ind: Dict[str, Any],
    change_pct_rank: Optional[float],
    mode: str,
    timeframe: str,
    direction: str = "auto",
    capital: float = 0.0,
    risk_pct: float = 1.0,
) -> Optional[Dict[str, Any]]:
    """Turn one symbol's indicators into a complete LONG or SHORT trade idea.

    Returns None if data is unusable or no setup matches the resolved direction."""
    metrics = compute_metrics(ind)
    if not metrics:
        return None

    ctx = analyze_timeframe_context(ind, timeframe)
    bias = ctx.get("bias", "Neutral")
    close = ind.get("close")
    ema50 = ind.get("EMA50")

    side = _decide_direction(direction, bias, close, ema50)
    if side is None:
        return None

    setup = compute_trade_setup(ind)  # provides ATR-based S/R, neutral level lists
    if not setup:
        return None
    supports = setup["supports"]
    resistances = setup["resistances"]
    atr = ind.get("ATR")
    if not close or not atr:
        return None

    min_stop_pct = 0.5 if mode == "swing" else 0.6
    entry = close

    if side == "long":
        score_result = compute_stock_score(ind, change_pct_rank=change_pct_rank, currency="INR")
        if not score_result:
            return None
        momentum_score = score_result["score"]
        grade = score_result["grade"]
        signals = score_result["signals"]
        trend_state = score_result.get("trend_state")

        t1 = resistances[0] if resistances else round(close + 1.5 * atr, 2)
        t2 = resistances[1] if len(resistances) >= 2 else round(close + 3.0 * atr, 2)
        stop_loss = setup["stop_loss"]
        stop_pct = setup["stop_distance_pct"]
        if stop_pct is not None and stop_pct < min_stop_pct:
            stop_loss = round(close * (1 - min_stop_pct / 100), 2)
            stop_pct = min_stop_pct
        risk = close - stop_loss
        _q = compute_trade_quality(ind, momentum_score, setup)
        quality_score, quality_label = _q["trade_quality_score"], _q["quality"]
    else:  # short
        momentum_score, signals = _short_momentum_score(ind, change_pct_rank)
        grade = _grade(momentum_score)
        trend_state = "Downtrend"
        t1 = supports[0] if supports else round(close - 1.5 * atr, 2)
        t2 = supports[1] if len(supports) >= 2 else round(close - 3.0 * atr, 2)
        # Stop above: tighter (lower) of nearest resistance + buffer, or close + 1.5*ATR.
        atr_stop = round(close + 1.5 * atr, 2)
        res_stop = round(resistances[0] + 0.5 * atr, 2) if resistances else None
        stop_loss = min(res_stop, atr_stop) if res_stop else atr_stop
        stop_pct = round((stop_loss - close) / close * 100, 2)
        if stop_pct < min_stop_pct:
            stop_loss = round(close * (1 + min_stop_pct / 100), 2)
            stop_pct = min_stop_pct
        risk = stop_loss - close

    def _rr(target):
        if not (risk and risk > 0 and target):
            return None
        reward = (target - entry) if side == "long" else (entry - target)
        return round(reward / risk, 1)

    rr1, rr2 = _rr(t1), _rr(t2)

    if side == "short":
        quality_score, quality_label = _short_quality(ind, rr2, stop_pct)

    conviction, conviction_label = _conviction(momentum_score, quality_score, rr2, bias, side)

    # MACD / volume / VWAP context for the rationale.
    vol, vol_sma = ind.get("volume"), ind.get("volume.SMA20")
    vol_ratio = round(vol / vol_sma, 2) if vol and vol_sma and vol_sma > 0 else None
    macd_line, macd_sig = ind.get("MACD.macd"), ind.get("MACD.signal")
    if side == "long":
        macd_aligned = (macd_line is not None and macd_sig is not None and macd_line > macd_sig)
    else:
        macd_aligned = (macd_line is not None and macd_sig is not None and macd_line < macd_sig)

    vwap_note = None
    vwap = ind.get("VWAP")
    if mode == "intraday" and vwap and close:
        if side == "long":
            vwap_note = (
                f"Price is above VWAP ({_fmt(vwap)}) — intraday bias favours longs."
                if close >= vwap else
                f"Price is below VWAP ({_fmt(vwap)}) — long bias is weak; wait for a reclaim."
            )
        else:
            vwap_note = (
                f"Price is below VWAP ({_fmt(vwap)}) — intraday bias favours shorts."
                if close <= vwap else
                f"Price is above VWAP ({_fmt(vwap)}) — short bias is weak; wait for a rejection."
            )

    rationale = _build_rationale(
        name=full_symbol.split(":")[-1], mode=mode, direction=side, grade=grade,
        momentum_score=momentum_score, bias=bias, rsi=ind.get("RSI"),
        macd_aligned=macd_aligned, vol_ratio=vol_ratio, entry=entry, stop_loss=stop_loss,
        stop_pct=stop_pct, t1=t1, t2=t2, rr2=rr2, vwap_note=vwap_note, quality_label=quality_label,
    )

    verb = "Short" if side == "short" else "Buy"
    return {
        "symbol": full_symbol,
        "name": full_symbol.split(":")[-1],
        "mode": mode,
        "timeframe": timeframe,
        "hold": _MODE_DEFAULTS[mode]["hold"],
        "direction": side.upper(),
        "price": metrics["price"],
        "currency": "INR",
        "change_pct": metrics.get("change"),
        "conviction": conviction,
        "conviction_label": conviction_label,
        "entry": entry,
        "entry_strategy": f"{verb} around current price (CMP ₹{entry:,.2f})" if entry else None,
        "stop_loss": stop_loss,
        "stop_distance_pct": stop_pct,
        "target_1": t1,
        "target_2": t2,
        "risk_reward_t1": rr1,
        "risk_reward_t2": rr2,
        "momentum_score": momentum_score,
        "grade": grade,
        "trend_state": trend_state,
        "trade_quality_score": quality_score,
        "trade_quality": quality_label,
        "vwap": vwap if mode == "intraday" else None,
        "key_levels": {"supports": supports, "resistances": resistances},
        "signals": signals,
        "position_sizing": _position_sizing(entry, stop_loss, t1, t2, side, capital, risk_pct),
        "rationale": rationale,
    }


# ─── Public API ─────────────────────────────────────────────────────────────

def generate_india_picks(
    exchange: str = "NSE",
    mode: str = "swing",
    timeframe: Optional[str] = None,
    top_n: int = 5,
    min_conviction: int = 60,
    scan_limit: int = 150,
    direction: str = "auto",
    index_filter: str = "",
    capital: float = 0.0,
    risk_pct: float = 1.0,
) -> dict:
    """Scan NSE/BSE stocks and return the top long/short trade ideas.

    Args:
        exchange:       "NSE" or "BSE".
        mode:           "swing" (2-7 day) or "intraday".
        timeframe:      Override analysis timeframe (else mode default: 1D / 15m).
        top_n:          Number of ideas to return (1-25).
        min_conviction: Minimum conviction score to include (0-100).
        scan_limit:     How many of the most-liquid symbols to scan (max 500).
        direction:      "auto" (long for uptrends, short for downtrends), "long", or "short".
        index_filter:   Restrict universe to an index — "NIFTY50", "NIFTYBANK", "NIFTYNEXT50" (empty = all).
        capital:        Account capital in INR — if > 0, each idea includes position sizing.
        risk_pct:       Risk per trade as % of capital (default 1.0).
    """
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is not installed."}

    mode = mode if mode in _MODE_DEFAULTS else "swing"
    direction = direction if direction in ("auto", "long", "short") else "auto"
    tf = timeframe or _MODE_DEFAULTS[mode]["timeframe"]
    ex_code, screener = _exchange_meta(exchange)
    top_n = max(1, min(25, top_n))
    scan_limit = max(10, min(500, scan_limit))

    universe_label = "All " + ex_code
    if index_filter:
        from tradingview_mcp.core.data.india_indices import get_index_symbols, available_indices
        symbols = get_index_symbols(index_filter, ex_code)
        if not symbols:
            return {"error": f"Unknown index: {index_filter}", "available_indices": available_indices()}
        universe_label = index_filter.strip().upper()
    else:
        symbols = load_symbols(ex_code.lower())[:scan_limit]  # nse.txt/bse.txt are volume-sorted
    if not symbols:
        return {"error": f"No symbols found for {ex_code}."}

    raw = _fetch_batch(symbols, screener, tf)
    if not raw:
        return {"error": f"No market data returned for {ex_code} on {tf}."}
    _enrich_atr_volume(raw, tf)

    # Cross-sectional percentile rank of intraday change (drives relative-performance scoring).
    changes = []
    for ind in raw.values():
        o, c = ind.get("open"), ind.get("close")
        if o and c and o > 0:
            changes.append(((c - o) / o) * 100)
    changes.sort()
    n = len(changes)

    def _pct_rank(val: float) -> float:
        return (sum(1 for c in changes if c < val) / n) if n else 0.5

    ideas: List[dict] = []
    for sym, ind in raw.items():
        o, c = ind.get("open"), ind.get("close")
        rank = _pct_rank(((c - o) / o) * 100) if (o and c and o > 0) else None
        try:
            idea = _build_idea(sym, ind, rank, mode, tf, direction, capital, risk_pct)
        except Exception:
            idea = None
        if idea and idea["conviction"] >= min_conviction:
            ideas.append(idea)

    ideas.sort(key=lambda x: (x["conviction"], x["trade_quality_score"], x["momentum_score"]), reverse=True)

    return {
        "exchange": ex_code,
        "universe": universe_label,
        "mode": mode,
        "direction": direction,
        "timeframe": tf,
        "holding_period": _MODE_DEFAULTS[mode]["hold"],
        "scanned": len(raw),
        "qualified": len(ideas),
        "min_conviction": min_conviction,
        "longs": len([i for i in ideas if i["direction"] == "LONG"]),
        "shorts": len([i for i in ideas if i["direction"] == "SHORT"]),
        "picks": ideas[:top_n],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": _DISCLAIMER,
    }


def generate_india_trade_plan(
    symbol: str,
    exchange: str = "NSE",
    mode: str = "swing",
    timeframe: Optional[str] = None,
    direction: str = "auto",
    capital: float = 0.0,
    risk_pct: float = 1.0,
) -> dict:
    """Full trade idea for one NSE/BSE stock (entry, stop, two targets, conviction, rationale, optional sizing)."""
    if not _TA_AVAILABLE:
        return {"error": "tradingview_ta is not installed."}

    mode = mode if mode in _MODE_DEFAULTS else "swing"
    direction = direction if direction in ("auto", "long", "short") else "auto"
    tf = timeframe or _MODE_DEFAULTS[mode]["timeframe"]
    ex_code, screener = _exchange_meta(exchange)
    full_symbol = symbol.upper() if ":" in symbol else f"{ex_code}:{symbol.upper()}"

    raw = _fetch_batch([full_symbol], screener, tf)
    ind = raw.get(full_symbol)
    if not ind:
        return {"error": f"No data found for {full_symbol} on {tf}."}
    _enrich_atr_volume(raw, tf)

    idea = _build_idea(full_symbol, ind, None, mode, tf, direction, capital, risk_pct)
    if not idea:
        # No setup matched the resolved direction — explain why.
        ctx = analyze_timeframe_context(ind, tf)
        want = "trade" if direction == "auto" else f"{direction.upper()} trade"
        return {
            "symbol": full_symbol,
            "mode": mode,
            "timeframe": tf,
            "direction": direction,
            "actionable": False,
            "reason": f"No qualifying {want} (trend conflicts with the requested side, or insufficient data).",
            "bias": ctx.get("bias", "Neutral"),
            "disclaimer": _DISCLAIMER,
        }

    idea["actionable"] = True
    idea["generated_at"] = datetime.now(timezone.utc).isoformat()
    idea["disclaimer"] = _DISCLAIMER
    return idea


# ─── Backtest validation hook ─────────────────────────────────────────────────

def _to_yahoo(symbol: str, exchange: str) -> str:
    """Map an NSE/BSE symbol to its Yahoo Finance ticker (.NS / .BO)."""
    base = symbol.split(":")[-1].upper().replace("_", "-")
    suffix = ".BO" if exchange.strip().upper() == "BSE" else ".NS"
    return f"{base}{suffix}"


def backtest_india_pick(
    symbol: str,
    exchange: str = "NSE",
    period: str = "2y",
    interval: str = "1d",
) -> dict:
    """Validate a stock against history: run all 6 strategies on its Yahoo data and
    report a ranked leaderboard. A sanity-check on whether the name "respects"
    technical setups before acting on a live suggestion.

    Args:
        symbol:   NSE/BSE symbol, e.g. "RELIANCE", "TCS".
        exchange: "NSE" or "BSE".
        period:   '6mo', '1y', '2y'.
        interval: '1d' or '1h'.
    """
    from tradingview_mcp.core.services.backtest_service import compare_strategies as _cmp

    yf_symbol = _to_yahoo(symbol, exchange)
    try:
        result = _cmp(yf_symbol, period=period, interval=interval)
    except Exception as exc:
        return {"symbol": symbol, "yahoo_symbol": yf_symbol, "error": str(exc)}

    result["symbol"] = symbol
    result["yahoo_symbol"] = yf_symbol
    result["note"] = (
        "Historical strategy performance is a robustness check, not a prediction. "
        "A name where multiple strategies were profitable tends to respect technical "
        "levels better than one where none worked."
    )
    result["disclaimer"] = _DISCLAIMER
    return result
