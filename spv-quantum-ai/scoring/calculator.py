from typing import Any, Dict, List, Tuple
from scoring.models import DecisionQuality
from scoring.weights import WeightManager

class ConfidenceCalculator:
    """
    Computes overall confidence score and decision quality using weighted averages,
    bonuses for alignment, and penalties for conflict.
    """
    def __init__(self, weight_mgr: WeightManager) -> None:
        self.wm = weight_mgr

    def calculate(self, inputs: Dict[str, Any]) -> Tuple[float, Dict[str, float], DecisionQuality, List[str], List[str]]:
        """
        Calculates: (overall_confidence, component_scores, quality, missing_reqs, conflicts)
        """
        comp_scores: Dict[str, float] = {}
        missing_reqs: List[str] = []
        conflicts: List[str] = []

        # ── 1. Resolve Component Scores ──────────────────────────────────────
        # A. Market Analysis Report
        report = inputs.get("market_analysis")
        if report:
            comp_scores["market_analysis"] = float(getattr(report, "confidence", 50.0))
        else:
            comp_scores["market_analysis"] = 0.0
            missing_reqs.append("market_analysis_report")

        # B. Market Regime
        regime = inputs.get("market_regime", "UNKNOWN")
        if regime == "UNKNOWN" or not regime:
            comp_scores["market_regime"] = 0.0
            missing_reqs.append("market_regime")
        elif regime in ("TRENDING_BULLISH", "TRENDING_BEARISH", "BREAKOUT", "BREAKDOWN"):
            comp_scores["market_regime"] = 100.0
        elif regime in ("SIDEWAYS", "RANGE_BOUND"):
            comp_scores["market_regime"] = 60.0
        else:
            comp_scores["market_regime"] = 40.0

        # C. Strategy Match
        strategy_matched = bool(inputs.get("strategy_matched", False))
        comp_scores["strategy_match"] = 100.0 if strategy_matched else 30.0

        # D. Risk Status
        risk_status = inputs.get("risk_status", "BLOCK")
        if risk_status == "ALLOW":
            comp_scores["risk_status"] = 100.0
        elif risk_status == "REDUCE_POSITION":
            comp_scores["risk_status"] = 60.0
        else:
            comp_scores["risk_status"] = 0.0
            missing_reqs.append("risk_allowance")

        # ── 2. Weighted Sum ──────────────────────────────────────────────────
        weighted_score = 0.0
        for comp, score in comp_scores.items():
            weight = self.wm.get_weight(comp)
            weighted_score += score * weight

        # ── 3. Detect Conflicts and Alignments ────────────────────────────────
        bias = getattr(report, "market_bias", "NEUTRAL") if report else "NEUTRAL"
        momentum = getattr(report, "momentum", "FLAT") if report else "FLAT"
        strategy_action = inputs.get("strategy_action", "SIGNAL_NONE")

        # Conflicts
        if bias == "BULLISH" and regime == "TRENDING_BEARISH":
            conflicts.append("bullish_bias_in_bearish_regime")
        if bias == "BEARISH" and regime == "TRENDING_BULLISH":
            conflicts.append("bearish_bias_in_bullish_regime")
        if bias == "BULLISH" and momentum == "BEARISH":
            conflicts.append("bullish_bias_with_bearish_momentum")
        if bias == "BEARISH" and momentum == "BULLISH":
            conflicts.append("bearish_bias_with_bullish_momentum")
        if strategy_action == "SIGNAL_BUY" and bias == "BEARISH":
            conflicts.append("buy_strategy_with_bearish_bias")
        if strategy_action == "SIGNAL_SELL" and bias == "BULLISH":
            conflicts.append("sell_strategy_with_bullish_bias")

        # Apply Penalties
        penalty = len(conflicts) * self.wm.penalty_conflicting_signals
        final_score = max(0.0, weighted_score - penalty)

        # Alignments (Bonus)
        aligned = False
        if bias == "BULLISH" and regime == "TRENDING_BULLISH" and momentum == "BULLISH":
            aligned = True
        elif bias == "BEARISH" and regime == "TRENDING_BEARISH" and momentum == "BEARISH":
            aligned = True
            
        if aligned and len(conflicts) == 0:
            final_score = min(100.0, final_score + self.wm.bonus_aligned_signals)

        # ── 4. Decision Quality Classification ────────────────────────────────
        quality = DecisionQuality.MODERATE
        if risk_status == "BLOCK" or "risk_allowance" in missing_reqs:
            quality = DecisionQuality.INVALID
        elif final_score >= 85.0:
            quality = DecisionQuality.VERY_STRONG
        elif final_score >= 70.0:
            quality = DecisionQuality.STRONG
        elif final_score >= 55.0:
            quality = DecisionQuality.MODERATE
        elif final_score >= self.wm.min_confidence_threshold:
            quality = DecisionQuality.WEAK
        else:
            quality = DecisionQuality.INVALID

        return round(final_score, 2), comp_scores, quality, missing_reqs, conflicts
