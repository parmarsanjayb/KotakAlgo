"""
Grey Market Premium (GMP) scraper — the ONE deliberately unofficial data
source in the IPO module. GMP is not published by any exchange; it is an
informal, unverified indicator collected by grey-market tracking sites. The
user explicitly approved using it (2026-07-11) on the condition that it is
always clearly labelled unofficial. Every record this module produces carries
`unofficial=True` and a source URL, and any scrape failure yields None rather
than a fabricated premium.

On-demand only: this is never part of the background collection loop. It runs
when a user explicitly asks to analyse an IPO. A short in-memory TTL avoids
re-downloading the (large) source page when several symbols are analysed in
quick succession.
"""
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from sqlalchemy import select
from database.connection import async_session
from database.models import IPOIssueModel
from core.logging import get_logger

logger = get_logger("ipo_gmp")

SOURCE_NAME = "IPO Watch (grey market, unofficial)"
SOURCE_URL = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
# "₹249 (16.35%)" or "₹-5 (-2.1%)"
_MONEY_RE = re.compile(r"-?\d[\d,]*\.?\d*")
_PERCENT_RE = re.compile(r"\(\s*(-?\d[\d,]*\.?\d*)\s*%\s*\)")

# Company-name tokens that carry no identifying signal — dropped before match.
_STOPWORDS = {"limited", "ltd", "private", "pvt", "and", "the", "india", "co", "company"}

_TTL_SEC = 10 * 60  # in-memory cache for the whole table


def _clean_cell(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = (text.replace("&amp;", "&").replace("&nbsp;", " ")
                .replace("&#8377;", "₹").replace("₹", " "))
    return re.sub(r"\s+", " ", text).strip()


def _num(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    m = _MONEY_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _normalize_name(name: str) -> List[str]:
    name = name.replace("&amp;", "&").replace("&", " and ").lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return [tok for tok in name.split() if tok and tok not in _STOPWORDS]


def _match_score(nse_tokens: List[str], row_tokens: List[str]) -> float:
    """Token-overlap (Jaccard) between an NSE company name and a scraped row."""
    if not nse_tokens or not row_tokens:
        return 0.0
    a, b = set(nse_tokens), set(row_tokens)
    return len(a & b) / len(a | b)


class GMPScraper:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=_HEADERS, timeout=25.0, follow_redirects=True)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _parse_table(self, html: str) -> List[Dict[str, Any]]:
        """Parse the first GMP table into structured, real rows. Header is
        detected from the first row; missing/garbled rows are skipped, not
        guessed."""
        m = _TABLE_RE.search(html)
        if not m:
            return []
        rows = _ROW_RE.findall(m.group(1))
        parsed: List[Dict[str, Any]] = []
        for row in rows:
            cells = [_clean_cell(c) for c in _CELL_RE.findall(row)]
            if len(cells) < 5 or cells[0].lower() in ("ipo name", "ipo"):
                continue
            company = cells[0]
            gmp = _num(cells[1])
            price_band = _num(cells[3]) if len(cells) > 3 else None
            est_listing = cells[4] if len(cells) > 4 else ""
            est_price = _num(est_listing)
            pm = _PERCENT_RE.search(est_listing)
            gmp_percent = _num(pm.group(1)) if pm else None
            status = cells[7] if len(cells) > 7 else None
            if not company or gmp is None:
                continue  # a row with no real premium is not recorded
            parsed.append({
                "company": company,
                "gmp": gmp,
                "price_band": price_band,
                "est_listing_price": est_price,
                "gmp_percent": gmp_percent,
                "status": status,
                "tokens": _normalize_name(company),
            })
        return parsed

    async def fetch_all_gmp(self, force: bool = False) -> List[Dict[str, Any]]:
        """Scrape all current IPOs' GMP once. Cached in memory for a few
        minutes so analysing several symbols doesn't re-download the page."""
        now = time.time()
        if not force and self._cache is not None and (now - self._cache_at) < _TTL_SEC:
            return self._cache
        client = await self._get_client()
        try:
            resp = await client.get(SOURCE_URL, headers=_HEADERS)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("GMP source fetch failed", error=str(e))
            return self._cache or []  # keep last good data rather than nothing
        parsed = self._parse_table(resp.text)
        if parsed:
            self._cache = parsed
            self._cache_at = now
        return parsed

    async def fetch_gmp_for(self, symbol: str, company_name: str,
                            force: bool = False) -> Optional[Dict[str, Any]]:
        """On-demand: find the GMP row matching one IPO by company name and
        cache it (labelled unofficial) on the IPO row inside raw_data. Returns
        None if no confident name match — never a fabricated premium."""
        symbol = symbol.upper()
        table = await self.fetch_all_gmp(force=force)
        if not table:
            return None

        nse_tokens = _normalize_name(company_name)
        best, best_score = None, 0.0
        for entry in table:
            score = _match_score(nse_tokens, entry["tokens"])
            if score > best_score:
                best, best_score = entry, score
        if best is None or best_score < 0.6:  # require a confident match
            logger.info("No confident GMP match", symbol=symbol, best_score=round(best_score, 2))
            return None

        record = {
            "gmp": best["gmp"],
            "gmp_percent": best["gmp_percent"],
            "price_band": best["price_band"],
            "est_listing_price": best["est_listing_price"],
            "matched_name": best["company"],
            "match_score": round(best_score, 2),
            "source": SOURCE_NAME,
            "source_url": SOURCE_URL,
            "unofficial": True,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        async with async_session() as session:
            issue = (await session.execute(
                select(IPOIssueModel).where(IPOIssueModel.symbol == symbol)
            )).scalars().first()
            if issue is None:
                return None
            raw = dict(issue.raw_data or {})
            raw["gmp"] = record
            issue.raw_data = raw  # reassign so SQLAlchemy flags the JSON column dirty
            await session.commit()
        return record


# Singleton
gmp_scraper = GMPScraper()
