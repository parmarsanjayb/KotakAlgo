import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from core.bus import event_bus, EventModel
from core.logging import get_logger
from market.models import OptionChain, OptionContract

logger = get_logger("option_flow_intelligence")

class OptionFlowIntelligenceEmployee:
    """
    Option Flow Intelligence Employee.
    Analyzes ATM option chain metrics, calculates PCR, build-ups, Max Pain, Smart Money Bias,
    and classifies market state. Does NOT execute trades directly.
    """
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {
            "atm_window": 5,
            "min_volume": 100.0,
            "min_oi": 50.0,
            "min_confidence": 60.0,
            "pcr_threshold": 1.0,
            "oi_threshold": 1.0
        }
        # (underlying, expiry) -> list of previous contracts state for delta checks
        self.previous_chains: Dict[tuple, OptionChain] = {}
        # symbol -> latest option flow analysis details
        self.latest_results: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("option_chain_updated", self._on_option_chain_event)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("OptionFlowIntelligenceEmployee started and subscribed to option_chain_updated events.")

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        await event_bus.unsubscribe("option_chain_updated", self._on_option_chain_event)
        logger.info("OptionFlowIntelligenceEmployee stopped.")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                from employees.engine import employee_engine
                # Safely get latest metrics/status
                decision_str = "Wait"
                score = 50.0
                if self.latest_results:
                    # Pick arbitrary symbol to check status
                    first_res = next(iter(self.latest_results.values()))
                    decision_str = first_res.get('recommendation', 'WAIT')
                    score = first_res.get("confidence", 50.0)
                
                await employee_engine.manager.record_activity(
                    employee_code="EMP-OFT",
                    decision=decision_str,
                    confidence=score,
                    execution_time_ms=0.0
                )
            except Exception:
                pass
            await asyncio.sleep(5)

    async def _on_option_chain_event(self, event: EventModel) -> None:
        try:
            payload = event.payload
            # Try to load option chain
            raw_chain = payload.get("option_chain", payload)
            # Handle if dict or object
            if isinstance(raw_chain, dict):
                chain = OptionChain(**raw_chain)
            else:
                chain = raw_chain
            
            await self.analyze_option_chain(chain)
        except Exception as e:
            logger.error("Error processing option chain in OptionFlowIntelligenceEmployee", error=str(e))

    async def analyze_option_chain(self, chain: OptionChain) -> Dict[str, Any]:
        async with self._lock:
            underlying = chain.underlying
            expiry = chain.expiry
            key = (underlying, expiry)

            prev_chain = self.previous_chains.get(key)
            self.previous_chains[key] = chain

            # 1. Identify ATM Strike
            contracts = chain.contracts
            if not contracts:
                return {}

            strikes = sorted(list(set(c.strike for c in contracts)))
            if not strikes:
                return {}

            underlying_price = chain.underlying_price
            atm_strike = min(strikes, key=lambda s: abs(s - underlying_price))

            # Resolve strike step
            strike_step = 100.0
            if len(strikes) > 1:
                strike_step = strikes[1] - strikes[0]

            # 2. Filter window
            window = self.config.get("atm_window", 5)
            min_strike = atm_strike - window * strike_step
            max_strike = atm_strike + window * strike_step

            window_contracts = [c for c in contracts if min_strike <= c.strike <= max_strike]

            # Map contracts for quick access
            ce_map: Dict[float, OptionContract] = {}
            pe_map: Dict[float, OptionContract] = {}
            prev_ce_map: Dict[float, OptionContract] = {}
            prev_pe_map: Dict[float, OptionContract] = {}

            for c in window_contracts:
                if c.option_type == "CE":
                    ce_map[c.strike] = c
                elif c.option_type == "PE":
                    pe_map[c.strike] = c

            if prev_chain:
                for c in prev_chain.contracts:
                    if min_strike <= c.strike <= max_strike:
                        if c.option_type == "CE":
                            prev_ce_map[c.strike] = c
                        elif c.option_type == "PE":
                            prev_pe_map[c.strike] = c

            # Strikes inside the filtered window
            active_strikes = sorted(list(set(c.strike for c in window_contracts)))

            # Sum accumulators
            ce_vol_strength = 0.0
            pe_vol_strength = 0.0
            ce_oi_strength = 0.0
            pe_oi_strength = 0.0
            total_oi_change = 0.0
            total_vol_change = 0.0
            ce_oi_change_sum = 0.0
            pe_oi_change_sum = 0.0
            ce_vol_change_sum = 0.0
            pe_vol_change_sum = 0.0

            smart_money_bias = 0.0
            long_bu = 0
            short_bu = 0
            long_uw = 0
            short_cv = 0

            weighted_ce_strike_sum = 0.0
            weighted_pe_strike_sum = 0.0

            for strike in active_strikes:
                ce = ce_map.get(strike)
                pe = pe_map.get(strike)
                prev_ce = prev_ce_map.get(strike)
                prev_pe = prev_pe_map.get(strike)

                # Call metrics
                if ce:
                    ce_vol_strength += ce.volume
                    ce_oi_strength += ce.open_interest
                    weighted_ce_strike_sum += strike * ce.volume
                    
                    ce_prev_oi = prev_ce.open_interest if prev_ce else ce.open_interest
                    ce_prev_vol = prev_ce.volume if prev_ce else ce.volume
                    
                    ce_oi_change = ce.open_interest - ce_prev_oi
                    ce_vol_change = ce.volume - ce_prev_vol
                    ce_oi_change_sum += ce_oi_change
                    ce_vol_change_sum += ce_vol_change

                    # Bid/Ask Qty fallbacks if not in model
                    ce_bid_qty = getattr(ce, "bid_quantity", 100.0)
                    ce_ask_qty = getattr(ce, "ask_quantity", 100.0)
                    smart_money_bias -= (ce_bid_qty - ce_ask_qty)

                    # Build-up classification (price change vs OI change)
                    ce_price_change = ce.ltp - (prev_ce.ltp if prev_ce else ce.ltp)
                    if ce_price_change > 0 and ce_oi_change > 0:
                        long_bu += 1
                    elif ce_price_change < 0 and ce_oi_change > 0:
                        short_bu += 1
                    elif ce_price_change < 0 and ce_oi_change < 0:
                        long_uw += 1
                    elif ce_price_change > 0 and ce_oi_change < 0:
                        short_cv += 1

                # Put metrics
                if pe:
                    pe_vol_strength += pe.volume
                    pe_oi_strength += pe.open_interest
                    weighted_pe_strike_sum += strike * pe.volume
                    
                    pe_prev_oi = prev_pe.open_interest if prev_pe else pe.open_interest
                    pe_prev_vol = prev_pe.volume if prev_pe else pe.volume
                    
                    pe_oi_change = pe.open_interest - pe_prev_oi
                    pe_vol_change = pe.volume - pe_prev_vol
                    pe_oi_change_sum += pe_oi_change
                    pe_vol_change_sum += pe_vol_change

                    # Bid/Ask Qty fallbacks
                    pe_bid_qty = getattr(pe, "bid_quantity", 100.0)
                    pe_ask_qty = getattr(pe, "ask_quantity", 100.0)
                    smart_money_bias += (pe_bid_qty - pe_ask_qty)

                    # Build-up classification
                    pe_price_change = pe.ltp - (prev_pe.ltp if prev_pe else pe.ltp)
                    if pe_price_change > 0 and pe_oi_change > 0:
                        short_bu += 1  # Put buying is short building on market
                    elif pe_price_change < 0 and pe_oi_change > 0:
                        long_bu += 1
                    elif pe_price_change < 0 and pe_oi_change < 0:
                        short_cv += 1
                    elif pe_price_change > 0 and pe_oi_change < 0:
                        long_uw += 1

            total_oi_change = pe_oi_change_sum - ce_oi_change_sum
            total_vol_change = pe_vol_change_sum - ce_vol_change_sum

            # 3. PCR calculation
            pcr = pe_oi_strength / ce_oi_strength if ce_oi_strength > 0 else 1.0

            # 4. ATM Dominance
            atm_ce = ce_map.get(atm_strike)
            atm_pe = pe_map.get(atm_strike)
            atm_vol = (atm_ce.volume if atm_ce else 0.0) + (atm_pe.volume if atm_pe else 0.0)
            total_vol = ce_vol_strength + pe_vol_strength
            atm_dominance = atm_vol / total_vol if total_vol > 0 else 0.0

            # 5. Option Chain Imbalance
            imbalance = (pe_oi_strength - ce_oi_strength) / (pe_oi_strength + ce_oi_strength) if (pe_oi_strength + ce_oi_strength) > 0 else 0.0

            # 6. Volume Weighted Direction
            ce_weighted_strike = weighted_ce_strike_sum / ce_vol_strength if ce_vol_strength > 0 else atm_strike
            pe_weighted_strike = weighted_pe_strike_sum / pe_vol_strength if pe_vol_strength > 0 else atm_strike
            volume_weighted_direction = "BULLISH" if ce_weighted_strike > pe_weighted_strike else "BEARISH"

            # 7. Max Pain Calculation
            max_pain_strike = atm_strike
            min_pain = float("inf")
            for candidate in active_strikes:
                pain = 0.0
                for s in active_strikes:
                    ce_contract = ce_map.get(s)
                    pe_contract = pe_map.get(s)
                    ce_oi = ce_contract.open_interest if ce_contract else 0.0
                    pe_oi = pe_contract.open_interest if pe_contract else 0.0

                    pain += max(0.0, s - candidate) * ce_oi  # Call buyers pain when strike > candidate
                    pain += max(0.0, candidate - s) * pe_oi  # Put buyers pain when candidate > strike
                
                if pain < min_pain:
                    min_pain = pain
                    max_pain_strike = candidate

            # 8. Option Flow Score Calculation
            volume_imbalance = (pe_vol_strength - ce_vol_strength) / total_vol if total_vol > 0 else 0.0
            
            # Incorporate both OI and Volume Imbalances
            score = 50.0 + (imbalance * 25.0) + (volume_imbalance * 25.0)
            
            # Static PCR contribution
            if pcr > 1.8:
                score += 15.0
            elif pcr > 1.2:
                score += 8.0
            elif pcr < 0.5:
                score -= 15.0
            elif pcr < 0.8:
                score -= 8.0

            # Dynamic changes if history is available
            if prev_chain:
                if pe_oi_change_sum > ce_oi_change_sum:
                    score += 10.0
                elif ce_oi_change_sum > pe_oi_change_sum:
                    score -= 10.0
                
                if total_vol_change > 0:
                    score += 5.0
                elif total_vol_change < 0:
                    score -= 5.0

            # Smart money bias contribution
            if smart_money_bias > 0:
                score += 5.0
            elif smart_money_bias < 0:
                score -= 5.0

            score = max(0.0, min(100.0, score))

            # 9. Market Classification
            classification = "SIDEWAYS"
            is_trap = (pcr > 2.0 and volume_imbalance < -0.4) or (pcr < 0.5 and volume_imbalance > 0.4)

            if is_trap:
                classification = "TRAP"
            else:
                if score > 65.0:
                    classification = "BULLISH"
                elif score < 35.0:
                    classification = "BEARISH"
                
                if classification == "SIDEWAYS":
                    if atm_dominance > 0.35:
                        classification = "VOLATILE"
                    elif abs(total_vol_change) < (total_vol * 0.05) if prev_chain else True:
                        classification = "SIDEWAYS"

            if ce_vol_strength + pe_vol_strength < self.config.get("min_volume", 100.0):
                classification = "NO_TRADE"

            # 10. Strength & Confidence
            strength = abs(score - 50.0) * 2.0 # Scale 0 to 100
            confidence = strength

            # 11. Risk & Recommendation
            risk = "MEDIUM"
            if classification == "VOLATILE":
                risk = "HIGH"
            elif classification == "SIDEWAYS":
                risk = "LOW"

            recommendation = "WAIT"
            min_conf = self.config.get("min_confidence", 60.0)
            
            if classification == "BULLISH" and confidence >= min_conf:
                recommendation = "BUY CE"
            elif classification == "BEARISH" and confidence >= min_conf:
                recommendation = "BUY PE"
            elif classification == "TRAP" or classification == "NO_TRADE":
                recommendation = "NO_TRADE"

            result = {
                "underlying": underlying,
                "atm_strike": atm_strike,
                "ce_strength": round(ce_vol_strength, 2),
                "pe_strength": round(pe_vol_strength, 2),
                "ce_oi_strength": round(ce_oi_strength, 2),
                "pe_oi_strength": round(pe_oi_strength, 2),
                "pcr": round(pcr, 4),
                "atm_dominance": round(atm_dominance, 4),
                "option_chain_imbalance": round(imbalance, 4),
                "smart_money_bias": round(smart_money_bias, 2),
                "long_bu_count": long_bu,
                "short_bu_count": short_bu,
                "long_uw_count": long_uw,
                "short_cv_count": short_cv,
                "max_pain_strike": max_pain_strike,
                "volume_weighted_direction": volume_weighted_direction,
                "option_flow_score": round(score, 2),
                "classification": classification,
                "confidence": round(confidence, 2),
                "strength": round(strength, 2),
                "risk": risk,
                "recommendation": recommendation,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            self.latest_results[underlying] = result

            # Publish to Event Bus
            await event_bus.publish(EventModel(
                event_type="option_flow_updated",
                source_agent="option_flow_employee",
                payload=result
            ))

            # Conditional triggers
            if recommendation in ("BUY CE", "BUY PE"):
                await event_bus.publish(EventModel(
                    event_type="option_flow_signal",
                    source_agent="option_flow_employee",
                    payload={"symbol": underlying, "recommendation": recommendation, "score": score, "confidence": confidence}
                ))

            if risk == "HIGH":
                await event_bus.publish(EventModel(
                    event_type="option_flow_warning",
                    source_agent="option_flow_employee",
                    payload={"symbol": underlying, "message": f"Option chain risk is HIGH. Classification is {classification}."}
                ))

            if classification == "TRAP":
                await event_bus.publish(EventModel(
                    event_type="option_flow_trap",
                    source_agent="option_flow_employee",
                    payload={"symbol": underlying, "message": "Market Trap detected! High PCR divergence against option flow score."}
                ))

            return result
