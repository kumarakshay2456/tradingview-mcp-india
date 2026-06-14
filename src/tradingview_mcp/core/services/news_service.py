"""
Financial News Service via RSS feeds.

Uses feedparser. No API keys required. Pulls from free, public RSS feeds.

Network notes:
  Feeds are fetched with an explicit certifi-backed SSL context and a browser
  User-Agent, and redirects (incl. HTTP 308) are followed manually. This avoids
  the macOS "CERTIFICATE_VERIFY_FAILED" issue and bot-blocking that breaks
  feedparser's default urllib fetch.

Sources:
  crypto: CoinDesk, Cointelegraph
  stocks: Reuters Business / Company news
  india:  Economic Times, Moneycontrol, LiveMint, Hindu BusinessLine
  all:    Combined (global + India)
"""
from __future__ import annotations

import ssl
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

try:
    import feedparser
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

try:
    import certifi
    _CA_FILE = certifi.where()
except ImportError:
    _CA_FILE = None

# ─── Feed Catalog ─────────────────────────────────────────────────────────────

RSS_FEEDS: dict[str, list[dict]] = {
    "crypto": [
        {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "name": "CoinDesk"},
        {"url": "https://cointelegraph.com/rss", "name": "CoinTelegraph"},
    ],
    "stocks": [
        {"url": "https://feeds.reuters.com/reuters/businessNews", "name": "Reuters Business"},
        {"url": "https://feeds.reuters.com/reuters/companyNews", "name": "Reuters Company"},
    ],
    # Indian market news — free public RSS, no key required.
    "india": [
        {"url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "name": "Economic Times Markets"},
        {"url": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms", "name": "Economic Times Stocks"},
        {"url": "https://www.moneycontrol.com/rss/business.xml", "name": "Moneycontrol Business"},
        {"url": "https://www.moneycontrol.com/rss/marketreports.xml", "name": "Moneycontrol Markets"},
        {"url": "https://www.livemint.com/rss/markets", "name": "LiveMint Markets"},
        {"url": "https://www.thehindubusinessline.com/markets/feeder/default.rss", "name": "Hindu BusinessLine Markets"},
    ],
    "all": [
        {"url": "https://feeds.reuters.com/reuters/businessNews", "name": "Reuters Business"},
        {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "name": "CoinDesk"},
        {"url": "https://cointelegraph.com/rss", "name": "CoinTelegraph"},
        {"url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "name": "Economic Times Markets"},
        {"url": "https://www.moneycontrol.com/rss/business.xml", "name": "Moneycontrol Business"},
        {"url": "https://www.livemint.com/rss/markets", "name": "LiveMint Markets"},
    ],
}

_TIMEOUT = 10
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_REDIRECTS = 5


def _ssl_context() -> ssl.SSLContext:
    """SSL context backed by certifi when available, else system default."""
    if _CA_FILE:
        return ssl.create_default_context(cafile=_CA_FILE)
    return ssl.create_default_context()


def _fetch_url(url: str) -> bytes:
    """Fetch raw feed bytes with a browser UA, certifi SSL, and manual redirect
    following (urllib in Python 3.10 does not auto-follow HTTP 308)."""
    ctx = _ssl_context()
    seen = 0
    while True:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and seen < _MAX_REDIRECTS:
                location = e.headers.get("Location")
                if location:
                    url = urllib.parse.urljoin(url, location)
                    seen += 1
                    continue
            raise


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch_news(
    symbol: Optional[str] = None,
    category: str = "stocks",
    limit: int = 10,
) -> list[dict]:
    """
    Fetch financial news from RSS feeds.

    Args:
        symbol:   Optional ticker filter. If provided, only returns headlines
                  that mention the symbol (case-insensitive). e.g. "AAPL", "RELIANCE"
        category: Feed group — "crypto" | "stocks" | "india" | "all"
        limit:    Maximum number of items to return

    Returns:
        List of news items with title, url, published, summary, source.
    """
    if not _FEEDPARSER_AVAILABLE:
        return [{
            "error": "feedparser not installed. Run: pip install feedparser",
            "install": "pip install feedparser"
        }]

    feeds = RSS_FEEDS.get(category, RSS_FEEDS["stocks"])
    results: list[dict] = []

    for feed_info in feeds:
        if len(results) >= limit:
            break
        try:
            raw = _fetch_url(feed_info["url"])
            feed = feedparser.parse(raw)
            source_name = feed.feed.get("title", feed_info["name"]) or feed_info["name"]

            for entry in feed.entries:
                if len(results) >= limit:
                    break

                title = _clean_html(entry.get("title", ""))
                summary = entry.get("summary", "") or entry.get("description", "")

                # Symbol filter
                if symbol:
                    combined = f"{title} {summary}".upper()
                    if symbol.upper() not in combined:
                        continue

                results.append({
                    "title": title,
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary": _clean_html(summary)[:300],
                    "source": source_name,
                })

        except Exception:
            continue

    return results[:limit]


def fetch_news_summary(
    symbol: Optional[str] = None,
    category: str = "stocks",
    limit: int = 10,
) -> dict:
    """
    Fetch news and return structured dict for MCP tool output.
    """
    items = fetch_news(symbol, category, limit)
    return {
        "symbol": symbol,
        "category": category,
        "count": len(items),
        "feedparser_available": _FEEDPARSER_AVAILABLE,
        "items": items,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Utils ────────────────────────────────────────────────────────────────────

def _clean_html(text: str) -> str:
    """Strip HTML tags and unescape entities (named + numeric)."""
    import re
    import html
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()
