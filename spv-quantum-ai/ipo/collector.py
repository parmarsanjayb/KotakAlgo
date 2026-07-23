import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from sqlalchemy import select
from database.connection import async_session
from database.models import IPOIssueModel, IPOSubscriptionSnapshotModel
from core.logging import get_logger

logger = get_logger("ipo_collector")

NSE_BASE = "https://www.nseindia.com"
NSE_API = f"{NSE_BASE}/api"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_PRICE_RANGE_RE = re.compile(r"Rs\.?\s*([\d,.]+)\s*to\s*Rs\.?\s*([\d,.]+)", re.IGNORECASE)


def _parse_price_band(price_str: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """'Rs.203 to Rs.214' -> (203.0, 214.0). A single flat price ('99') has
    no band — both bounds come back equal. Anything unparseable returns
    (None, None) rather than a guessed number."""
    if not price_str:
        return None, None
    price_str = price_str.strip()
    m = _PRICE_RANGE_RE.search(price_str)
    if m:
        try:
            return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        except ValueError:
            return None, None
    # Flat single price, e.g. "Rs.1000" or "99" — strip the currency prefix
    # first so its own "." isn't misread as the number's decimal point.
    cleaned = re.sub(r"^Rs\.?\s*", "", price_str, flags=re.IGNORECASE)
    m2 = re.search(r"[\d,]+\.?\d*", cleaned)
    if not m2:
        return None, None
    try:
        val = float(m2.group(0).replace(",", ""))
        return val, val
    except ValueError:
        return None, None


def _parse_nse_date(date_str: Optional[str]) -> Optional[datetime]:
    """NSE dates come as 'dd-Mon-yyyy' (mixed case, e.g. '09-Jul-2026' or
    '07-JUL-2026'). Returns None (never a guessed date) if unparseable."""
    if not date_str:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── Detail (ipo-detail) parsing ────────────────────────────────────────────
# NSE's /api/ipo-detail returns a richer, official payload than the list
# endpoints. Its `issueInfo.dataList` is a flat list of {title, value} rows
# (labels vary slightly across issues), plus links to the official offer
# documents (RHP, "Basis of Issue Price" ratios). Everything below is
# extracted from that real payload — nothing is invented; a field NSE didn't
# supply stays absent.

_HREF_RE = re.compile(r"href=[\"']?([^\"'>\s]+)", re.IGNORECASE)
_FACE_VALUE_RE = re.compile(r"Rs\.?\s*([\d,.]+)", re.IGNORECASE)
_LEADING_INT_RE = re.compile(r"([\d,]+)")
# "Fresh Issue aggregating up to Rs. 5,420 million" / "... Rs. 542 crore"
_FRESH_RE = re.compile(r"Fresh Issue\s+aggregating up to\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s*(million|crore|lakh|billion)?", re.IGNORECASE)
_OFS_RE = re.compile(r"Offer for Sale[^R]*aggregating up to\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s*(million|crore|lakh|billion)?", re.IGNORECASE)

_UNIT_MULT = {"lakh": 1e5, "million": 1e6, "crore": 1e7, "billion": 1e9}

# issueInfo.dataList titles → keys in the parsed detail. Matched by
# case-insensitive substring so minor label variations still map.
_DETAIL_TEXT_FIELDS = {
    "issue period": "issue_period",
    "price range": "price_range",
    "book running lead managers": "lead_managers",
    "name of the registrar": "registrar",
    "issue type": "issue_type",
}
_DETAIL_DOC_FIELDS = {
    "red herring prospectus": "rhp_url",
    "ratios / basis of issue price": "ratios_url",
    "anchor allocation report": "anchor_url",
}


def _extract_url(value: Optional[str]) -> Optional[str]:
    """A dataList value is either a bare URL or an <a href=...> anchor."""
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith("http"):
        return value.split()[0]
    m = _HREF_RE.search(value)
    return m.group(1) if m else None


def _parse_amount_with_unit(match: Optional[re.Match]) -> Optional[float]:
    """'5,420 million' → 5_420_000_000.0 (rupees). No unit → the number as-is."""
    if not match:
        return None
    try:
        num = float(match.group(1).replace(",", ""))
    except (ValueError, IndexError):
        return None
    unit = (match.group(2) or "").lower()
    return num * _UNIT_MULT.get(unit, 1.0)


def parse_issue_info(data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Turn NSE ipo-detail `issueInfo.dataList` into a flat, real-data-only
    dict of structured fields + official document links. Unrecognised rows
    are ignored; missing fields stay absent rather than guessed."""
    parsed: Dict[str, Any] = {"documents": {}}
    for row in data_list or []:
        title = (row.get("title") or "").strip().lower()
        value = row.get("value")
        if not title or value in (None, ""):
            continue
        for needle, key in _DETAIL_TEXT_FIELDS.items():
            if needle in title:
                parsed[key] = str(value).strip()
                break
        for needle, key in _DETAIL_DOC_FIELDS.items():
            if needle in title:
                url = _extract_url(str(value))
                if url:
                    parsed["documents"][key] = url
                break
        if "face value" in title:
            m = _FACE_VALUE_RE.search(str(value))
            if m:
                parsed["face_value"] = _to_float(m.group(1).replace(",", ""))
        elif "bid lot" in title:
            m = _LEADING_INT_RE.search(str(value))
            if m:
                try:
                    parsed["bid_lot"] = int(m.group(1).replace(",", ""))
                except ValueError:
                    pass
        elif "issue size" in title:
            text = str(value).strip()
            parsed["issue_structure"] = text
            fresh = _parse_amount_with_unit(_FRESH_RE.search(text))
            ofs = _parse_amount_with_unit(_OFS_RE.search(text))
            if fresh is not None:
                parsed["fresh_issue_amount"] = fresh
            if ofs is not None:
                parsed["ofs_amount"] = ofs
            if fresh is not None or ofs is not None:
                parsed["total_issue_amount"] = (fresh or 0.0) + (ofs or 0.0)
    return parsed


class IPOCollector:
    """
    Fetches real IPO data from NSE's public JSON API (verified working
    endpoints, not scraped from rendered HTML). NSE blocks direct API
    requests without a warmed-up browser-like session, so every fetch
    first visits the NSE homepage to acquire cookies before calling the
    API with them. There is no synthetic/fabricated fallback — if NSE is
    unreachable or a field is missing, that field stays null rather than
    being invented.
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0, follow_redirects=True)
        # Warm up cookies — NSE returns 401/403 on the API without a prior
        # visit to a normal page establishing session cookies.
        try:
            await self._client.get(NSE_BASE, headers={**_HEADERS, "Accept": "text/html"})
        except httpx.HTTPError as e:
            logger.warning("NSE session warmup failed", error=str(e))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _fetch_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        client = await self._get_client()
        try:
            resp = await client.get(f"{NSE_API}{path}", params=params, headers={**_HEADERS, "Referer": NSE_BASE})
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.HTTPError as e:
            logger.error(f"NSE fetch failed for {path}", error=str(e))
            return []
        except ValueError as e:
            logger.error(f"NSE response for {path} was not valid JSON", error=str(e))
            return []

    async def fetch_current(self) -> List[Dict[str, Any]]:
        """Currently open IPOs, with live subscription figures."""
        return await self._fetch_json("/ipo-current-issue")

    async def fetch_upcoming(self) -> List[Dict[str, Any]]:
        """Mix of Active (open) and Forthcoming (truly upcoming) issues —
        the caller distinguishes via the `status` field."""
        return await self._fetch_json("/all-upcoming-issues", params={"category": "ipo"})

    async def fetch_past(self, from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Listed IPOs. from_date/to_date as 'dd-mm-yyyy'; NSE defaults to
        the last 90 days when omitted."""
        params = {}
        if from_date:
            params["fromDate"] = from_date
        if to_date:
            params["toDate"] = to_date
        return await self._fetch_json("/public-past-issues", params=params or None)

    async def _fetch_json_obj(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Like _fetch_json but for endpoints that return a single JSON
        object (e.g. ipo-detail). Returns None on any error — never a
        fabricated shape."""
        client = await self._get_client()
        try:
            resp = await client.get(f"{NSE_API}{path}", params=params, headers={**_HEADERS, "Referer": NSE_BASE})
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else None
        except httpx.HTTPError as e:
            logger.error(f"NSE fetch failed for {path}", error=str(e))
            return None
        except ValueError as e:
            logger.error(f"NSE response for {path} was not valid JSON", error=str(e))
            return None

    async def fetch_detail(self, symbol: str, force: bool = False) -> Optional[Dict[str, Any]]:
        """On-demand: fetch NSE's rich ipo-detail payload for one symbol,
        parse its structured fields + official document links, and cache the
        result on the IPO row (inside raw_data — no schema change, matching
        this model's 'named columns are extractions of raw_data' design).

        This is deliberately NOT part of the periodic collect_all pass: it
        runs only when a user opens a specific IPO, so the app never
        continuously polls per-symbol detail in the background. Returns the
        parsed detail dict (also served from cache on repeat opens unless
        force=True)."""
        symbol = symbol.upper()
        async with async_session() as session:
            issue = (await session.execute(
                select(IPOIssueModel).where(IPOIssueModel.symbol == symbol)
            )).scalars().first()
            if issue is None:
                return None

            raw = dict(issue.raw_data or {})
            if not force and raw.get("detail") and raw.get("detail_fetched_at"):
                return raw["detail"]  # served from cache; no network hit

        payload = await self._fetch_json_obj("/ipo-detail", params={"symbol": symbol})
        if not payload:
            return None
        data_list = (payload.get("issueInfo") or {}).get("dataList") or []
        parsed = parse_issue_info(data_list)

        async with async_session() as session:
            issue = (await session.execute(
                select(IPOIssueModel).where(IPOIssueModel.symbol == symbol)
            )).scalars().first()
            if issue is None:
                return None
            raw = dict(issue.raw_data or {})
            raw["detail"] = parsed
            raw["detail_fetched_at"] = datetime.now(timezone.utc).isoformat()
            issue.raw_data = raw  # reassign so SQLAlchemy flags the JSON column dirty
            # Backfill the real convenience column if NSE's list feed left it
            # empty but the detail payload supplies it.
            if issue.lot_size is None and parsed.get("bid_lot"):
                issue.lot_size = parsed["bid_lot"]
            await session.commit()
        return parsed

    # ── Normalization ──────────────────────────────────────────────────────

    @staticmethod
    def _normalize_current_or_upcoming(item: Dict[str, Any], force_status: Optional[str] = None) -> Optional[Dict[str, Any]]:
        symbol = item.get("symbol")
        if not symbol:
            return None
        if force_status:
            status = force_status
        else:
            status = "OPEN" if (item.get("status") or "").upper() == "ACTIVE" else "UPCOMING"
        low, high = _parse_price_band(item.get("issuePrice"))
        return {
            "symbol": symbol,
            "company_name": item.get("companyName", symbol),
            "status": status,
            "fields": {
                "security_type": item.get("series"),
                "price_band_low": low,
                "price_band_high": high,
                "issue_size": _to_float(item.get("issueSize")),
                "issue_start_date": _parse_nse_date(item.get("issueStartDate")),
                "issue_end_date": _parse_nse_date(item.get("issueEndDate")),
            },
            "raw": item,
        }

    @staticmethod
    def _normalize_past(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = item.get("symbol")
        if not symbol:
            return None
        low, high = _parse_price_band(item.get("priceRange"))
        return {
            "symbol": symbol,
            "company_name": item.get("companyName") or item.get("company") or symbol,
            "status": "LISTED",
            "fields": {
                "security_type": item.get("securityType"),
                "price_band_low": low,
                "price_band_high": high,
                "issue_start_date": _parse_nse_date(item.get("ipoStartDate")),
                "issue_end_date": _parse_nse_date(item.get("ipoEndDate")),
                "listing_date": _parse_nse_date(item.get("listingDate")),
                # NOTE: NSE's public-past-issues feed carries no actual
                # listing-day price — only `issuePrice` (the IPO price itself).
                # Mapping listing_price to issuePrice made every listing gain a
                # mathematical 0%, which the performance tracker then scored as
                # a wrong call (fake "0% accuracy"). Leave it None until a real
                # listing-price source is wired; the tracker treats None as
                # "not judged yet" rather than fabricating a verdict.
                "listing_price": None,
            },
            "raw": item,
        }

    # ── Persistence ────────────────────────────────────────────────────────
    # Bulk per collection pass: dedupe by symbol in Python first (NSE's own
    # feeds contain repeat symbols — e.g. multiple bond/NCD tranches sharing
    # a base symbol), one bulk SELECT to find existing rows, then a single
    # commit. Avoids both a commit-per-row pattern (public-past-issues alone
    # can return 1000+ rows) and the UNIQUE-constraint crash a naive
    # select-then-insert loop hits on in-batch duplicates under this
    # session's autoflush=False.

    async def _bulk_upsert(self, normalized: List[Dict[str, Any]]) -> int:
        if not normalized:
            return 0
        by_symbol: Dict[str, Dict[str, Any]] = {n["symbol"]: n for n in normalized}  # last one wins

        async with async_session() as session:
            result = await session.execute(
                select(IPOIssueModel).where(IPOIssueModel.symbol.in_(by_symbol.keys()))
            )
            existing = {row.symbol: row for row in result.scalars().all()}

            for symbol, n in by_symbol.items():
                row = existing.get(symbol)
                if row is None:
                    row = IPOIssueModel(symbol=symbol, company_name=n["company_name"], status=n["status"])
                    session.add(row)
                row.company_name = n["company_name"] or row.company_name
                row.status = n["status"]
                for key, value in n["fields"].items():
                    if value is not None:
                        setattr(row, key, value)
                row.raw_data = n["raw"]

            await session.commit()
        return len(by_symbol)

    async def _record_subscriptions(self, items: List[Dict[str, Any]]) -> None:
        async with async_session() as session:
            for item in items:
                symbol = item.get("symbol")
                subscription_times = _to_float(item.get("noOfTime"))
                if not symbol or subscription_times is None:
                    continue  # no real figure reported — don't fabricate a row
                session.add(IPOSubscriptionSnapshotModel(
                    ipo_symbol=symbol,
                    category=item.get("category") or "Total",
                    shares_offered=_to_float(item.get("noOfSharesOffered")),
                    shares_bid=_to_float(item.get("noOfsharesBid")),
                    subscription_times=subscription_times,
                ))
            await session.commit()

    async def collect_all(self) -> Dict[str, int]:
        """Runs a full collection pass across all three real NSE sources.
        Returns counts collected per category for observability."""
        current_items = await self.fetch_current()
        current_normalized = [
            n for n in (self._normalize_current_or_upcoming(i, force_status="OPEN") for i in current_items) if n
        ]
        current_count = await self._bulk_upsert(current_normalized)
        await self._record_subscriptions(current_items)

        upcoming_items = await self.fetch_upcoming()
        upcoming_normalized = [
            n for n in (self._normalize_current_or_upcoming(i) for i in upcoming_items) if n
        ]
        upcoming_count = await self._bulk_upsert(upcoming_normalized)

        past_items = await self.fetch_past()
        past_normalized = [n for n in (self._normalize_past(i) for i in past_items) if n]
        past_count = await self._bulk_upsert(past_normalized)

        counts = {"current": current_count, "upcoming": upcoming_count, "past": past_count}
        logger.info("IPO collection pass complete", **counts)
        return counts


# Singleton
ipo_collector = IPOCollector()
