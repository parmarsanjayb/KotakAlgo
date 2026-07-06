import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from core.agent import BaseAgent, AgentResultModel
from core.bus import event_bus, EventModel
from core.logging import get_logger

from portfolio.engine import portfolio_engine
from risk.engine import risk_engine
from scoring.engine import decision_scoring_engine
from employees import employee_engine

logger = get_logger("chief_decision_agent")

class ConflictResolver:
    """
    Resolves scoring, regime, and risk conflicts.
    """
    def resolve(self, decision_score: float, risk_status: str) -> str:
        if risk_status == "BLOCK":
            return "REJECTED"
        if decision_score < 50.0:
            return "REJECTED"
        if decision_score >= 80.0 and risk_status == "ALLOW":
            return "APPROVED"
        return "MANUAL_REVIEW"


class ApprovalManager:
    """
    Applies mandatory criteria validation checks.
    """
    def __init__(self) -> None:
        self.max_daily_trades = 10
        self.max_open_positions = 5
        self.min_confidence = 50.0
        self._daily_trade_count = 0

    async def validate_checks(self, payload: Dict[str, Any]) -> tuple[str, str, str]:
        symbol = payload.get("symbol", "UNKNOWN")
        confidence = float(payload.get("overall_confidence") or payload.get("confidence") or 0.0)
        risk_status = payload.get("risk_status", "BLOCK")

        # 1. Decision Confidence Check
        if confidence < self.min_confidence:
            return "REJECTED", "CONFIDENCE_TOO_LOW", f"Confidence {confidence}% is below threshold {self.min_confidence}%"

        # 2. Risk Engine Approval
        if risk_status != "ALLOW":
            return "BLOCKED", "RISK_REJECTION", f"Risk engine returned status: {risk_status}"

        # 3. Open Positions Limit
        open_pos = len(await portfolio_engine.positions.get_open_positions())
        if open_pos >= self.max_open_positions:
            return "REJECTED", "POSITION_LIMIT_EXCEEDED", f"Active positions {open_pos} exceed limit {self.max_open_positions}"

        # 4. Capital Availability Check
        summary = await portfolio_engine.recalculate_summary()
        available_capital = summary.available_capital
        if available_capital <= 0.0:
            return "REJECTED", "CAPITAL_UNAVAILABLE", "Available margin is zero or negative."

        # 5. Duplicate Trade Check
        open_pos_list = await portfolio_engine.positions.get_open_positions()
        is_open = any(p.symbol == symbol for p in open_pos_list)
        if is_open:
            return "REJECTED", "DUPLICATE_TRADE", f"An active position already exists for {symbol}."

        # 6. Max Daily Trades Limit
        if self._daily_trade_count >= self.max_daily_trades:
            return "REJECTED", "DAILY_LIMIT_EXCEEDED", f"Daily trade limit {self.max_daily_trades} reached."

        return "APPROVED", "SUCCESS", "All checks passed successfully."

    def increment_daily_trades(self) -> None:
        self._daily_trade_count += 1

    def reset_daily_trades(self) -> None:
        self._daily_trade_count = 0


class DecisionPublisher:
    """
    Publishes chief decision outcomes to the event bus.
    """
    async def publish_approved(self, payload: Dict[str, Any]) -> None:
        await event_bus.publish(EventModel(
            event_type="trade_approved",
            source_agent="chief_decision_agent",
            payload=payload
        ))
        # Route to Risk Agent via live event loop
        await event_bus.publish(EventModel(
            event_type="order_request",
            source_agent="chief_decision_agent",
            payload={
                "symbol": payload["symbol"],
                "side": payload.get("side", "BUY"),
                "quantity": payload.get("quantity", 10.0),
                "price": payload.get("price", 100.0),
                "type": "LIMIT",
                "strategy_name": payload.get("strategy_name")
            }
        ))

    async def publish_rejected(self, payload: Dict[str, Any]) -> None:
        await event_bus.publish(EventModel(
            event_type="trade_rejected",
            source_agent="chief_decision_agent",
            payload=payload
        ))

    async def publish_blocked(self, payload: Dict[str, Any]) -> None:
        await event_bus.publish(EventModel(
            event_type="trade_blocked",
            source_agent="chief_decision_agent",
            payload=payload
        ))


class ChiefDecisionAgent(BaseAgent):
    """
    AI CEO & Final Decision Authority for the Trading OS.
    Evaluates scoring and risk metrics, applies mandatory checks, and issues APPROVED or REJECTED statuses.
    """
    def __init__(self) -> None:
        super().__init__(
            name="chief_decision_agent",
            description="Final trade decision manager of the SPV Quantum AI system"
        )
        self.coordinator = ApprovalManager()
        self.resolver = ConflictResolver()
        self.publisher = DecisionPublisher()
        
        self.approved_queue: List[Dict[str, Any]] = []
        self.rejected_queue: List[Dict[str, Any]] = []
        self.blocked_queue: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def input_event_types(self) -> List[str]:
        return ["decision_score"]

    @property
    def output_event_types(self) -> List[str]:
        return ["trade_approved", "trade_rejected", "trade_blocked", "order_request"]

    async def initialize(self) -> None:
        self.log_info("ChiefDecisionAgent initialized.")

    async def shutdown(self) -> None:
        self.log_info("ChiefDecisionAgent stopped.")

    def _get_employee_recommendation(self, employee, symbol: str) -> tuple[str, float]:
        """
        Retrieves (recommendation, confidence) from an employee instance for the given symbol.
        """
        if not employee:
            return "WAIT", 0.0
        
        results = getattr(employee, "latest_results", {})
        if not results:
            return "WAIT", 0.0
        
        res = results.get(symbol)
        if not res:
            res = results.get(symbol.upper())
        if not res:
            res = results.get("SYSTEM")
        if not res:
            try:
                res = next(iter(results.values()))
            except Exception:
                res = None
            
        if not res:
            return "WAIT", 0.0
            
        rec = res.get("recommendation") or res.get("confirmation_status") or "WAIT"
        conf = float(res.get("confidence") or 50.0)
        return str(rec).upper(), conf

    def _evaluate_ceo_decision(self, symbol: str, side: str, risk_status: str) -> Dict[str, Any]:
        """
        Executes the AI CEO Weighted Decision Engine.
        """
        # Helper to check matching side
        def matches_side(rec: str, trade_side: str) -> bool:
            rec_upper = rec.upper()
            side_upper = trade_side.upper()
            if rec_upper == "WAIT" or rec_upper == "NEUTRAL" or rec_upper == "NO_TRADE":
                return False
            if side_upper == "BUY":
                return "BUY" in rec_upper or "BULLISH" in rec_upper or "CE" in rec_upper or rec_upper == "ALLOW"
            elif side_upper == "SELL":
                return "SELL" in rec_upper or "BEARISH" in rec_upper or "PE" in rec_upper
            return False

        # Helper to check opposite side
        def opposite_side(rec: str, trade_side: str) -> bool:
            rec_upper = rec.upper()
            side_upper = trade_side.upper()
            if rec_upper == "WAIT" or rec_upper == "NEUTRAL" or rec_upper == "NO_TRADE":
                return False
            if side_upper == "BUY":
                return "SELL" in rec_upper or "BEARISH" in rec_upper or "PE" in rec_upper
            elif side_upper == "SELL":
                return "BUY" in rec_upper or "BULLISH" in rec_upper or "CE" in rec_upper or rec_upper == "ALLOW"
            return False

        # 1. Evaluate Mandatory Employees
        trend_rec, trend_conf = self._get_employee_recommendation(employee_engine.trend_intelligence, symbol)
        vol_rec, vol_conf = self._get_employee_recommendation(employee_engine.volume_intelligence, symbol)
        risk_rec, risk_conf = self._get_employee_recommendation(employee_engine.risk_emp, symbol)

        trend_passed = matches_side(trend_rec, side)
        vol_passed = (vol_rec == "CONFIRM" or matches_side(vol_rec, side))
        risk_passed = (risk_status == "ALLOW" and risk_rec != "WAIT" and risk_rec != "BLOCK")

        mandatory_passed = trend_passed and vol_passed and risk_passed
        block_trade = not risk_passed

        mandatory_reason = []
        if not trend_passed:
            mandatory_reason.append(f"Trend Employee reject ({trend_rec})")
        if not vol_passed:
            mandatory_reason.append(f"Volume Employee reject ({vol_rec})")
        if not risk_passed:
            mandatory_reason.append(f"Risk Employee block ({risk_rec}/RiskStatus:{risk_status})")

        # 2. Evaluate Weighted Employees
        weights = {
            "vwap": (employee_engine.vwap_emp, 0.15),
            "momentum": (employee_engine.momentum, 0.15),
            "liquidity": (employee_engine.liquidity, 0.15),
            "oi": (employee_engine.oi_emp, 0.10),
            "pcr": (employee_engine.pcr_emp, 0.10),
            "greeks": (employee_engine.greeks, 0.10),
            "option_flow": (employee_engine.option_flow, 0.25)
        }

        weighted_score_sum = 0.0
        weighted_breakdown = []
        agreed_count = 0

        # Count mandatory agreement
        if trend_passed: agreed_count += 1
        if vol_passed: agreed_count += 1
        if risk_passed: agreed_count += 1

        for name, (emp, weight) in weights.items():
            emp_rec, emp_conf = self._get_employee_recommendation(emp, symbol)
            
            if matches_side(emp_rec, side):
                score = emp_conf
                agreed_count += 1
            elif opposite_side(emp_rec, side):
                score = -emp_conf
            else:
                score = 0.0

            weighted_score_sum += weight * score
            weighted_breakdown.append(f"{name.upper()}: {emp_rec} ({emp_conf:.1f}% -> weight impact: {weight*score:.1f}%)")

        # 3. Evaluate Advisory Employees
        news_rec, news_conf = self._get_employee_recommendation(employee_engine.news_emp, symbol)
        cal_rec, cal_conf = self._get_employee_recommendation(employee_engine.calendar, symbol)
        evt_rec, evt_conf = self._get_employee_recommendation(employee_engine.event_risk, symbol)

        advisory_adjustment = 0.0
        advisory_breakdown = []

        if matches_side(news_rec, side):
            advisory_adjustment += 5.0
            advisory_breakdown.append("News: BULLISH bonus (+5.0%)")
        elif opposite_side(news_rec, side):
            advisory_adjustment -= 5.0
            advisory_breakdown.append("News: BEARISH penalty (-5.0%)")

        if matches_side(cal_rec, side):
            advisory_adjustment += 5.0
            advisory_breakdown.append("Calendar: High-match bonus (+5.0%)")
        elif opposite_side(cal_rec, side):
            advisory_adjustment -= 5.0
            advisory_breakdown.append("Calendar: Contrary-match penalty (-5.0%)")

        if evt_rec == "WAIT" or opposite_side(evt_rec, side):
            advisory_adjustment -= 10.0
            advisory_breakdown.append("Event Risk: Alert penalty (-10.0%)")

        # Final calculations
        final_confidence = max(0.0, min(100.0, weighted_score_sum + advisory_adjustment))
        consensus_pct = (agreed_count / 10.0) * 100.0

        # Risk designation
        if block_trade or not mandatory_passed:
            risk_level = "HIGH"
        elif consensus_pct >= 75.0 and final_confidence >= 75.0:
            risk_level = "LOW"
        else:
            risk_level = "MEDIUM"

        # Construct Reason
        reason_parts = []
        if mandatory_passed:
            reason_parts.append("Mandatory employees approved.")
        else:
            reason_parts.append(f"Mandatory employee check failed: {', '.join(mandatory_reason)}.")

        reason_parts.append("Weighted votes: [" + ", ".join(weighted_breakdown) + "]")
        if advisory_breakdown:
            reason_parts.append("Advisory feedback: [" + ", ".join(advisory_breakdown) + "]")
        
        reason_parts.append(f"Calculated Weighted Score: {weighted_score_sum:.1f}%.")
        reason_parts.append(f"Advisory Adjustments: {advisory_adjustment:+.1f}%.")
        reason_parts.append(f"CEO Consensus: {consensus_pct:.1f}% agreement ({agreed_count}/10 employees).")
        reason_parts.append(f"Calculated CEO Confidence: {final_confidence:.1f}% | Risk: {risk_level}.")

        reason = " ".join(reason_parts)

        return {
            "confidence": round(final_confidence, 2),
            "risk": risk_level,
            "consensus": round(consensus_pct, 2),
            "reason": reason,
            "mandatory_passed": mandatory_passed,
            "block_trade": block_trade
        }

    async def analyze(self, event: EventModel) -> Optional[AgentResultModel]:
        if event.event_type != "decision_score":
            return None

        start_time = time.perf_counter()
        score_data = event.payload.get("decision_score", event.payload)
        symbol = score_data.get("symbol", "UNKNOWN")
        side = score_data.get("side", "BUY")
        risk_status = score_data.get("risk_status", "ALLOW")
        strategy_name = score_data.get("recommended_strategy", "trend_strategy")

        async with self._lock:
            # 1. Run AI CEO Weighted Decision Engine
            ceo_eval = self._evaluate_ceo_decision(symbol, side, risk_status)
            confidence = ceo_eval["confidence"]

            # 2. Determine state and detailed checks
            if ceo_eval["block_trade"]:
                state = "BLOCKED"
                code = "RISK_REJECTION"
                explanation = ceo_eval["reason"]
            elif not ceo_eval["mandatory_passed"]:
                state = "REJECTED"
                code = "MANDATORY_EMPLOYEE_FAILURE"
                explanation = ceo_eval["reason"]
            else:
                # Copy score data and override confidence with CEO confidence
                score_data_copy = dict(score_data)
                score_data_copy["confidence"] = confidence
                score_data_copy["overall_confidence"] = confidence
                
                chk_state, code, explanation = await self.coordinator.validate_checks(score_data_copy)
                if chk_state != "APPROVED":
                    state = chk_state
                    explanation = f"{explanation} [CEO explanation: {ceo_eval['reason']}]"
                else:
                    state = "APPROVED"
                    explanation = ceo_eval["reason"]

            # Resolve actual price and quantity dynamically
            from market.manager import market_data_manager
            tick = await market_data_manager.cache.get_tick(symbol)
            price = tick.ltp if tick else score_data.get("price", 100.0)
            qty = score_data.get("quantity")
            if not qty:
                qty = 0.1 if price > 1000.0 else 10.0

            # Build record payload
            record = {
                "decision_id": f"DEC-{uuid.uuid4().hex[:8]}",
                "symbol": symbol,
                "strategy_name": strategy_name,
                "confidence": confidence,
                "risk": ceo_eval["risk"],
                "consensus": ceo_eval["consensus"],
                "status": state,
                "reason_code": code,
                "explanation": explanation,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "side": side,
                "quantity": qty,
                "price": price
            }

            # 3. Publish and Queue
            if state == "APPROVED":
                self.coordinator.increment_daily_trades()
                self.approved_queue.append(record)
                await self.publisher.publish_approved(record)
            elif state == "BLOCKED":
                self.blocked_queue.append(record)
                await self.publisher.publish_blocked(record)
            else:
                self.rejected_queue.append(record)
                await self.publisher.publish_rejected(record)

            self.log_info(f"Chief Decision: {state} for symbol {symbol} | Reason: {explanation}")
            
            processing_time = (time.perf_counter() - start_time) * 1000.0
            return AgentResultModel(
                agent_name=self.agent_name,
                signal=state,
                confidence=confidence,
                reason=explanation,
                processing_time=processing_time,
                metadata=record
            )
