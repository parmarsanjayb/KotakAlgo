"""
IPO news collector — pulls real, recent news headlines about an IPO from
Google News RSS (free, no API key) and derives a transparent, keyword-based
sentiment lean from them.

Honesty boundary: the headlines themselves are real data. The *sentiment* is
a simple, explainable positive/negative keyword tally over those headlines —
NOT a fundamental judgment and NOT an ML model. So, like the grey-market
source, the NewsAnalyst that consumes this keeps its confidence capped and
every report states that the sentiment is a headline heuristic. If no
relevant news is found, this returns None rather than inventing sentiment.

On-demand only — runs when a user analyses an IPO, never in a background loop.
"""
import re
import time
import html
from urllib.parse import quote_plus
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

import httpx

from sqlalchemy import select
from database.connection import async_session
from database.models import IPOIssueModel
from core.logging import get_logger

logger = get_logger("ipo_news")

SOURCE_NAME = "Google News (headline sentiment, heuristic)"
_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Transparent sentiment lexicon. Deliberately IPO-specific and small so the
# scoring is auditable — anyone can see exactly why a headline counted.
_POSITIVE = {
    "oversubscribed", "fully subscribed", "strong", "surge", "surges", "gain", "gains",
    "premium", "robust", "record", "jump", "jumps", "rally", "rallies", "growth",
    "bullish", "healthy", "positive", "soars", "soar", "profit", "upbeat", "boom",
    "multibagger", "listing gains", "outperform", "buy",
}
_NEGATIVE = {
    "undersubscribed", "weak", "fall", "falls", "decline", "declines", "loss", "losses",
    "concern", "concerns", "risk", "risks", "overvalued", "expensive", "avoid", "muted",
    "tepid", "discount", "lists below", "bearish", "slump", "drop", "drops", "plunge",
    "sell", "downgrade", "worry", "worries", "flat",
}
_STOPWORDS = {"limited", "ltd", "private", "pvt", "and", "the", "india", "ipo", "co", "company"}

_TTL_SEC = 15 * 60


def _company_tokens(name: str) -> List[str]:
    name = name.replace("&", " and ").lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    # keep only distinctive tokens (len>3) to avoid matching generic words
    return [t for t in name.split() if len(t) > 3 and t not in _STOPWORDS]


def _score_sentiment(titles: List[str]) -> Dict[str, Any]:
    pos = neg = 0
    for title in titles:
        low = title.lower()
        pos += sum(1 for w in _POSITIVE if w in low)
        neg += sum(1 for w in _NEGATIVE if w in low)
    total = pos + neg
    # net lean in [-1, 1]; 0 when no sentiment words hit
    net = (pos - neg) / total if total else 0.0
    return {"positive_hits": pos, "negative_hits": neg, "net": round(net, 3)}


class NewsCollector:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=_HEADERS, timeout=20.0, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_rss(self, query: str) -> List[Dict[str, Any]]:
        client = await self._get_client()
        url = _RSS_URL.format(query=quote_plus(query))
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("News RSS fetch failed", error=str(e))
            return []
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as e:
            logger.error("News RSS parse failed", error=str(e))
            return []
        items: List[Dict[str, Any]] = []
        for it in root.findall(".//item"):
            title = html.unescape((it.findtext("title") or "").strip())
            if not title:
                continue
            src_el = it.find("{*}source")
            source = src_el.text.strip() if src_el is not None and src_el.text else ""
            items.append({
                "title": title,
                "link": (it.findtext("link") or "").strip(),
                "source": source,
                "published": (it.findtext("pubDate") or "").strip(),
            })
        return items

    async def fetch_news(self, symbol: str, company_name: str,
                         max_items: int = 12, force: bool = False) -> Optional[Dict[str, Any]]:
        """On-demand: fetch recent IPO news for one company, keep only headlines
        that actually name the company, derive a transparent sentiment lean,
        and cache the result on the IPO row. Returns None if nothing relevant
        is found — never a fabricated sentiment."""
        symbol = symbol.upper()
        now = time.time()
        cached = self._cache.get(symbol)
        if not force and cached and (now - cached["_at"]) < _TTL_SEC:
            return cached["data"]

        tokens = _company_tokens(company_name)
        raw_items = await self._fetch_rss(f"{company_name} IPO")
        # Relevance: title must mention the company (a distinctive token) AND IPO.
        relevant = [
            it for it in raw_items
            if "ipo" in it["title"].lower()
            and (not tokens or any(t in it["title"].lower() for t in tokens))
        ][:max_items]
        if not relevant:
            return None

        sentiment = _score_sentiment([it["title"] for it in relevant])
        record = {
            "items": relevant,
            "count": len(relevant),
            "sentiment": sentiment,
            "source": SOURCE_NAME,
            "heuristic": True,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        self._cache[symbol] = {"data": record, "_at": now}

        async with async_session() as session:
            issue = (await session.execute(
                select(IPOIssueModel).where(IPOIssueModel.symbol == symbol)
            )).scalars().first()
            if issue is None:
                return record
            raw = dict(issue.raw_data or {})
            raw["news"] = record
            issue.raw_data = raw  # reassign so SQLAlchemy flags the JSON column dirty
            await session.commit()
        return record


# Singleton
news_collector = NewsCollector()
