from typing import Any, Dict, List, Optional
from abc import ABC, abstractmethod

from sqlalchemy import select
from database.connection import async_session
from database.models import IPOIssueModel, IPOSubscriptionSnapshotModel
from core.logging import get_logger

logger = get_logger("ipo_analysts")


class AnalystReport(Dict[str, Any]):
    """Shape: {analyst_name, score(0-100), confidence(0-100), reason, advantages[list], risks[list]}"""
    pass


class BaseIPOAnalyst(ABC):
    """
    An analyst either has real data to work with, or it produces nothing —
    there is no "best guess" mode. This is the mechanism that keeps the IPO
    CEO honest about data completeness: a symbol with 0 subscription
    snapshots simply never gets a SubscriptionAnalyst report, rather than a
    report built on an invented number.
    """
    name: str

    @abstractmethod
    async def analyze(self, issue: IPOIssueModel) -> Optional[AnalystReport]:
        ...


class SubscriptionAnalyst(BaseIPOAnalyst):
    """
    Reads real subscription snapshots (IPOSubscriptionSnapshotModel,
    populated from NSE's live noOfTime figures during an IPO's open
    window). Demand signal: how many times the "Total" category has been
    subscribed, using the most recent snapshot.
    """
    name = "Subscription Analyst"

    async def analyze(self, issue: IPOIssueModel) -> Optional[AnalystReport]:
        async with async_session() as session:
            result = await session.execute(
                select(IPOSubscriptionSnapshotModel)
                .where(IPOSubscriptionSnapshotModel.ipo_symbol == issue.symbol)
                .where(IPOSubscriptionSnapshotModel.category == "Total")
                .order_by(IPOSubscriptionSnapshotModel.snapshot_at.desc())
            )
            latest = result.scalars().first()

        if latest is None or latest.subscription_times is None:
            return None  # no real subscription data collected for this IPO yet

        x = latest.subscription_times
        if x >= 10:
            score, confidence = 90.0, 85.0
            reason = f"Heavily oversubscribed at {x:.2f}x — strong investor demand."
            advantages = [f"Total subscription {x:.2f}x indicates broad-based demand."]
            risks = ["Heavy oversubscription can mean a very low allotment probability."]
        elif x >= 3:
            score, confidence = 72.0, 75.0
            reason = f"Solidly subscribed at {x:.2f}x — healthy demand."
            advantages = [f"{x:.2f}x subscription shows genuine investor interest."]
            risks = ["Moderate allotment odds; listing gain not guaranteed."]
        elif x >= 1:
            score, confidence = 50.0, 70.0
            reason = f"Subscribed {x:.2f}x — demand met supply but without excess appetite."
            advantages = ["Full subscription achieved."]
            risks = ["Thin oversubscription suggests limited listing-day pop."]
        else:
            score, confidence = 20.0, 80.0
            reason = f"Undersubscribed at {x:.2f}x — demand did not meet supply."
            advantages = []
            risks = [f"Only {x:.2f}x subscribed — a real signal of weak market appetite.",
                     "Undersubscribed issues frequently list below issue price."]

        return AnalystReport(
            analyst_name=self.name, score=score, confidence=confidence,
            reason=reason, advantages=advantages, risks=risks,
        )


class ValuationAnalyst(BaseIPOAnalyst):
    """
    Honest scope: this does NOT assess fundamental valuation (P/E, DCF,
    peer comps) — that needs financial statement data this system doesn't
    have yet (see Phase 2 note on IPOCeoEngine). What IS real and
    computable from NSE's own price-band data: how tight the price band
    is. A narrow band (e.g. ±2-3%) signals the merchant banker priced with
    more confidence; a wide band signals more book-building uncertainty.
    """
    name = "Valuation Analyst"

    async def analyze(self, issue: IPOIssueModel) -> Optional[AnalystReport]:
        if issue.price_band_low is None or issue.price_band_high is None or issue.price_band_low <= 0:
            return None  # no real price band on record

        low, high = issue.price_band_low, issue.price_band_high
        band_width_pct = (high - low) / low * 100.0

        if band_width_pct <= 3.0:
            score, confidence = 75.0, 65.0
            reason = f"Tight price band ({band_width_pct:.1f}% spread) — confident pricing by the lead manager."
            advantages = ["Narrow band suggests less book-building uncertainty."]
            risks = ["A tight band alone says nothing about whether the price itself is fair."]
        elif band_width_pct <= 8.0:
            score, confidence = 55.0, 60.0
            reason = f"Moderate price band ({band_width_pct:.1f}% spread)."
            advantages = []
            risks = ["Wider band than typical mainboard issues — some pricing uncertainty."]
        else:
            score, confidence = 35.0, 55.0
            reason = f"Wide price band ({band_width_pct:.1f}% spread) — significant book-building uncertainty."
            advantages = []
            risks = [f"{band_width_pct:.1f}% band width is unusually wide."]

        return AnalystReport(
            analyst_name=self.name, score=score, confidence=confidence,
            reason=reason + " (Note: this measures price-band tightness only, not fundamental/fair value — "
                             "that requires financial statement data not yet integrated, see Phase 2.)",
            advantages=advantages, risks=risks,
        )


class IssueSizeAnalyst(BaseIPOAnalyst):
    """
    Real signal from issue_size × price band: total capital being raised,
    and whether this is a mainboard (EQ) or SME issue (security_type) —
    SME issues trade in a separate, far less liquid market segment, a real
    and material risk difference from real NSE-reported data.
    """
    name = "Issue Size Analyst"

    async def analyze(self, issue: IPOIssueModel) -> Optional[AnalystReport]:
        if issue.issue_size is None or issue.price_band_high is None:
            return None

        total_raise = issue.issue_size * issue.price_band_high
        is_sme = (issue.security_type or "").upper() == "SME"

        advantages: List[str] = []
        risks: List[str] = []

        if is_sme:
            score, confidence = 40.0, 70.0
            reason = f"SME issue raising ~₹{total_raise:,.0f} — trades on the SME platform, a much smaller, less liquid market."
            risks.append("SME-listed stocks have materially lower liquidity than mainboard.")
        elif total_raise >= 1_000_000_000:  # >= ₹100 crore
            score, confidence = 68.0, 65.0
            reason = f"Mainboard issue raising ~₹{total_raise:,.0f} — institutional-scale offering."
            advantages.append("Larger raises typically draw more institutional (QIB) participation.")
        else:
            score, confidence = 55.0, 60.0
            reason = f"Mainboard issue raising ~₹{total_raise:,.0f} — smaller mainboard offering."

        return AnalystReport(
            analyst_name=self.name, score=score, confidence=confidence,
            reason=reason, advantages=advantages, risks=risks,
        )


class GreyMarketAnalyst(BaseIPOAnalyst):
    """
    Reads the UNOFFICIAL grey-market premium scraped on demand into
    issue.raw_data['gmp'] (see ipo/gmp.py). Unlike every other analyst here,
    its input is not exchange-published data — GMP is an informal, unverified,
    highly volatile indicator. So: it only reports when a real scraped value
    exists, its confidence is deliberately capped low, and every report it
    produces states plainly that the number is unofficial. It measures
    listing-day sentiment only — never long-term quality.
    """
    name = "Grey Market Analyst"

    async def analyze(self, issue: IPOIssueModel) -> Optional[AnalystReport]:
        gmp = (issue.raw_data or {}).get("gmp")
        if not gmp or gmp.get("gmp") is None:
            return None  # no real scraped GMP for this IPO

        premium = gmp["gmp"]
        pct = gmp.get("gmp_percent")
        if pct is None and gmp.get("price_band"):
            try:
                pct = premium / gmp["price_band"] * 100.0
            except (TypeError, ZeroDivisionError):
                pct = None

        src = gmp.get("source", "grey market")
        unofficial_note = (f" Source: {src} — this is an UNOFFICIAL, unverified grey-market "
                           "figure, not exchange data, and can change or vanish before listing.")
        base_risk = "Grey-market premium is unofficial and highly volatile — treat as sentiment, not fact."

        pct_txt = f"{pct:.1f}%" if pct is not None else "an unknown %"
        if pct is None:
            score, confidence = 50.0, 30.0
            reason = f"Grey market premium of ₹{premium:.0f}, but no reliable percentage could be derived."
            advantages, risks = [], [base_risk]
        elif pct <= 0:
            score, confidence = 30.0, 45.0
            reason = f"Flat/negative grey market premium ({pct_txt}) — weak unofficial listing sentiment."
            advantages, risks = [], [base_risk, "Zero or negative GMP suggests little expected listing pop."]
        elif pct >= 30:
            score, confidence = 82.0, 50.0
            reason = f"Strong grey market premium (~{pct_txt}) — high unofficial listing-gain expectation."
            advantages = [f"GMP of ₹{premium:.0f} ({pct_txt}) points to strong listing-day demand."]
            risks = [base_risk, "Very high GMP often deflates sharply near listing."]
        elif pct >= 15:
            score, confidence = 70.0, 50.0
            reason = f"Healthy grey market premium (~{pct_txt}) — positive unofficial listing sentiment."
            advantages = [f"GMP of ₹{premium:.0f} ({pct_txt}) suggests a listing-day gain."]
            risks = [base_risk]
        elif pct >= 5:
            score, confidence = 58.0, 48.0
            reason = f"Modest grey market premium (~{pct_txt}) — mildly positive unofficial sentiment."
            advantages = [f"Small positive GMP of ₹{premium:.0f} ({pct_txt})."]
            risks = [base_risk]
        else:
            score, confidence = 50.0, 45.0
            reason = f"Marginal grey market premium (~{pct_txt}) — near-flat unofficial sentiment."
            advantages, risks = [], [base_risk]

        return AnalystReport(
            analyst_name=self.name, score=score, confidence=confidence,
            reason=reason + unofficial_note, advantages=advantages, risks=risks,
        )


class NewsAnalyst(BaseIPOAnalyst):
    """
    Reads recent, real IPO news headlines scraped on demand into
    issue.raw_data['news'] (see ipo/news.py). The headlines are real; the
    sentiment lean is a transparent positive/negative keyword tally over
    them — a heuristic, not a fundamental read. So confidence stays capped
    and every report says the sentiment is headline-derived. Reports nothing
    when no relevant news exists.
    """
    name = "News Analyst"

    async def analyze(self, issue: IPOIssueModel) -> Optional[AnalystReport]:
        news = (issue.raw_data or {}).get("news")
        if not news or not news.get("count"):
            return None  # no relevant news collected

        count = news["count"]
        sentiment = news.get("sentiment") or {}
        net = sentiment.get("net", 0.0)
        pos, neg = sentiment.get("positive_hits", 0), sentiment.get("negative_hits", 0)
        src = news.get("source", "news headlines")

        score = round(max(0.0, min(100.0, 50.0 + net * 30.0)), 1)
        # Confidence grows a little with news volume but is capped — this is a
        # headline heuristic, not hard data. No sentiment words at all → lower.
        confidence = min(55.0, 35.0 + min(count, 10) * 1.5)
        if pos + neg == 0:
            confidence = min(confidence, 32.0)

        heuristic_note = (f" Based on {count} recent headlines via {src}. Sentiment is a keyword "
                          "heuristic over headlines, not a fundamental assessment.")
        base_risk = "News sentiment here is a headline keyword tally — indicative only, not a fundamental view."

        if pos + neg == 0:
            reason = f"{count} recent news items found, but no clear positive/negative sentiment signal in the headlines."
            advantages, risks = [f"Active news coverage ({count} recent items)."], [base_risk]
        elif net > 0.2:
            reason = f"Recent headlines lean positive ({pos} positive vs {neg} negative signals across {count} items)."
            advantages = [f"Positive news sentiment ({pos} positive signals)."]
            risks = [base_risk]
        elif net < -0.2:
            reason = f"Recent headlines lean negative ({neg} negative vs {pos} positive signals across {count} items)."
            advantages = []
            risks = [base_risk, f"Negative news sentiment ({neg} negative signals) — investigate the concerns raised."]
        else:
            reason = f"Recent headlines are mixed/neutral ({pos} positive, {neg} negative across {count} items)."
            advantages, risks = [], [base_risk]

        return AnalystReport(
            analyst_name=self.name, score=score, confidence=confidence,
            reason=reason + heuristic_note, advantages=advantages, risks=risks,
        )


# The registry of real-data analysts. Grey Market (scraped, unofficial) and
# News (headline sentiment, heuristic) are both user-approved soft sources and
# always labelled as such; the rest use exchange-published data. Still to come
# from the original spec: Fundamental, Financial, Business, Risk, Sector —
# these need the RATIOS/RHP offer documents parsed (Phase 2d). See
# IPOCeoEngine's data_completeness reporting.
IPO_ANALYST_REGISTRY: List[BaseIPOAnalyst] = [
    SubscriptionAnalyst(),
    ValuationAnalyst(),
    IssueSizeAnalyst(),
    GreyMarketAnalyst(),
    NewsAnalyst(),
]
