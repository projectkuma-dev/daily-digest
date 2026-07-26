"""Free data sources for the daily digest: RSS news, NWS weather, Yahoo finance.

Everything here is $0 to call. Each fetcher degrades gracefully — a dead feed
or API hiccup shrinks the material instead of failing the run.

Run directly for a self-test: python pipeline/sources.py
"""

import concurrent.futures
import socket
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests

# feedparser has no timeout parameter and blocks indefinitely on a hung host,
# which would stall the whole morning run. This is the only way to bound it.
socket.setdefaulttimeout(15)

# Curated feed list, grouped by category. The curator only ever sees what these
# provide, so the category balance here IS the digest's balance. Feeds are
# presented to the model grouped by category so it can allocate slots across
# them rather than picking whatever the loudest category supplied.
# All entries verified live 2026-07-24.
NEWS_FEEDS = [
    # World and US national, including politics and policy
    ("world", "BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("world", "NPR News", "https://feeds.npr.org/1001/rss.xml"),
    ("world", "Guardian World", "https://www.theguardian.com/world/rss"),
    # Business, economy, markets
    ("business", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("business", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("business", "Guardian Business", "https://www.theguardian.com/uk/business/rss"),
    # Technology: products, engineering, AI industry
    ("tech", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("tech", "The Verge", "https://www.theverge.com/rss/index.xml"),
    ("tech", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    # Science and space
    ("science", "Phys.org", "https://phys.org/rss-feed/"),
    ("science", "Space.com", "https://www.space.com/feeds/all"),
    ("science", "NASA", "https://www.nasa.gov/rss/dyn/breaking_news.rss"),
    # Climate and energy
    ("climate", "Grist", "https://grist.org/feed/"),
    ("climate", "Ars Science", "https://feeds.arstechnica.com/arstechnica/science"),
    # Fitness, running, longevity
    ("health", "Runner's World", "https://www.runnersworld.com/rss/all.xml/"),
    ("health", "NYT Well", "https://rss.nytimes.com/services/xml/rss/nyt/Well.xml"),
    # Local: SLO county, Central Coast, California statewide
    ("local", "KSBY (SLO County)", "https://www.ksby.com/news/local-news.rss"),
    ("local", "LA Times California", "https://www.latimes.com/california/rss2.0.xml"),
    ("local", "CalMatters", "https://calmatters.org/feed/"),
    # Defense and DoD (trimmed from 4 feeds to 2 — one slot per digest, major stories only)
    ("defense", "Breaking Defense", "https://breakingdefense.com/feed/"),
    ("defense", "DefenseScoop", "https://defensescoop.com/feed/"),
]

# Order categories appear in the material message shown to the curator.
CATEGORY_ORDER = ["world", "business", "tech", "science", "climate", "health", "local", "defense"]
CATEGORY_LABELS = {
    "world": "WORLD & US NATIONAL",
    "business": "BUSINESS, ECONOMY & MARKETS",
    "tech": "TECHNOLOGY & AI INDUSTRY",
    "science": "SCIENCE & SPACE",
    "climate": "CLIMATE & ENERGY",
    "health": "FITNESS, RUNNING & HEALTH",
    "local": "LOCAL (SLO COUNTY & CALIFORNIA)",
    "defense": "DEFENSE & DOD (at most one item, major stories only)",
}

MAX_AGE_HOURS = 36
MAX_PER_FEED = 6
SNIPPET_CHARS = 300

# San Luis Obispo, CA
SLO_LAT, SLO_LON = 35.2828, -120.6596
NWS_HEADERS = {"User-Agent": "daily-digest (personal app; contact via GitHub)"}
WEATHER_SOURCE_URL = f"https://forecast.weather.gov/MapClick.php?lat={SLO_LAT}&lon={SLO_LON}"

TICKERS = [
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq Composite"),
    ("^DJI", "Dow Jones Industrial Average"),
    ("BA", "Boeing"),
    ("SPY", "SPY (S&P 500 ETF)"),
]


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text or "").strip()


def _fetch_one_feed(category: str, name: str, url: str) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    parsed = feedparser.parse(url)
    items = []
    for entry in parsed.entries[: MAX_PER_FEED * 3]:
        published = None
        for key in ("published_parsed", "updated_parsed"):
            t = entry.get(key)
            if t:
                published = datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
                break
        if published and published < cutoff:
            continue
        title = _strip_html(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        items.append(
            {
                "category": category,
                "source": name,
                "title": title,
                "url": link,
                "published": published.isoformat() if published else None,
                "snippet": _strip_html(entry.get("summary", ""))[:SNIPPET_CHARS],
            }
        )
        if len(items) >= MAX_PER_FEED:
            break
    return items


def fetch_news() -> list[dict]:
    """Recent entries from all feeds, fetched in parallel; dead feeds skipped."""
    items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_one_feed, c, n, u): n for c, n, u in NEWS_FEEDS}
        for future in concurrent.futures.as_completed(futures):
            try:
                items.extend(future.result())
            except Exception as exc:
                print(f"Feed {futures[future]!r} failed: {exc}")
    return items


def fetch_weather() -> dict | None:
    """Today's SLO forecast from the National Weather Service (free, no key)."""
    try:
        points = requests.get(
            f"https://api.weather.gov/points/{SLO_LAT},{SLO_LON}", headers=NWS_HEADERS, timeout=20
        )
        points.raise_for_status()
        forecast_url = points.json()["properties"]["forecast"]
        forecast = requests.get(forecast_url, headers=NWS_HEADERS, timeout=20)
        forecast.raise_for_status()
        periods = forecast.json()["properties"]["periods"][:4]
        return {
            "location": "San Luis Obispo, CA",
            "source_url": WEATHER_SOURCE_URL,
            "periods": [
                {
                    "name": p["name"],
                    "temperature": f"{p['temperature']}°{p['temperatureUnit']}",
                    "wind": f"{p.get('windSpeed', '')} {p.get('windDirection', '')}".strip(),
                    "precip_chance": (p.get("probabilityOfPrecipitation") or {}).get("value"),
                    "forecast": p.get("detailedForecast") or p.get("shortForecast", ""),
                }
                for p in periods
            ],
        }
    except Exception as exc:
        print(f"NWS weather fetch failed: {exc}")
        return None


def fetch_finance() -> list[dict]:
    """Prior close and latest (pre-market at 13:00 UTC) prices via yfinance."""
    import yfinance as yf

    quotes = []
    for symbol, label in TICKERS:
        try:
            info = yf.Ticker(symbol).fast_info
            last = info.get("lastPrice")
            prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
            quote = {
                "symbol": symbol,
                "label": label,
                "source_url": f"https://finance.yahoo.com/quote/{symbol.replace('^', '%5E')}",
                "previous_close": round(prev, 2) if prev else None,
                "latest_price": round(last, 2) if last else None,
            }
            if prev and last:
                quote["change_pct_vs_prev_close"] = round((last - prev) / prev * 100, 2)
            quotes.append(quote)
        except Exception as exc:
            print(f"Quote for {symbol} failed: {exc}")
    return quotes


def gather_material() -> dict:
    return {"news": fetch_news(), "weather": fetch_weather(), "finance": fetch_finance()}


if __name__ == "__main__":
    material = gather_material()
    by_cat: dict[str, dict[str, int]] = {}
    for item in material["news"]:
        by_cat.setdefault(item["category"], {})
        by_cat[item["category"]][item["source"]] = by_cat[item["category"]].get(item["source"], 0) + 1
    print(f"\nNews: {len(material['news'])} items")
    for cat in CATEGORY_ORDER:
        sources = by_cat.get(cat, {})
        total = sum(sources.values())
        flag = "  <-- EMPTY" if total == 0 else ""
        print(f"  [{cat}] {total} items{flag}")
        for source, n in sorted(sources.items()):
            print(f"      {source}: {n}")
    w = material["weather"]
    print(f"Weather: {'OK — ' + w['periods'][0]['name'] + ': ' + w['periods'][0]['forecast'][:80] if w else 'FAILED'}")
    print(f"Finance: {len(material['finance'])} quotes:")
    for q in material["finance"]:
        print(f"  {q['label']}: prev {q['previous_close']}, latest {q['latest_price']}")
