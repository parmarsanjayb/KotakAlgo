import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.bus import event_bus, EventModel
from core.logging import get_logger
from market.models import Timeframe, Candle
from indicators.engine import indicator_engine
from regime.engine import regime_engine
from risk.engine import risk_engine
from market.manager import market_data_manager

from strategies.models import (
    Strategy, StrategyResponse, StrategyMatchedEvent, StrategyRejectedEvent
)
from strategies.loader import StrategyRegistry, StrategyLoader
from strategies.evaluator import RuleEngine

logger = get_logger("strategy_engine")

class StrategyEngine:
    """
    Orchestrates the Strategy Rules Engine.
    Loads/reloads YAML strategies, builds feature contexts on new candle events,
    evaluates rules, and publishes Matched/Rejected events.
    Does not execute trades.
    """
    def __init__(self, directory: str = "config/strategies") -> None:
        self.registry = StrategyRegistry()
        self.loader = StrategyLoader(self.registry, directory)
        self.evaluator = RuleEngine()
        self._running = False
        
        # Load strategies on startup
        self.loader.load_all()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await event_bus.subscribe("candle", self._on_candle_event)
        await event_bus.subscribe("scanner_match", self._on_scanner_match)
        logger.info("StrategyEngine started and subscribed to candle and scanner_match events.")

    async def stop(self) -> None:
        self._running = False
        await event_bus.unsubscribe("candle", self._on_candle_event)
        await event_bus.unsubscribe("scanner_match", self._on_scanner_match)
        logger.info("StrategyEngine stopped.")

    async def _on_scanner_match(self, event: EventModel) -> None:
        try:
            payload = event.payload
            scan_result = payload.get("scan_result", payload)
            symbol = scan_result.get("symbol")
            if not symbol:
                return
            await self.evaluate_all(symbol, Timeframe.M1)
        except Exception as e:
            logger.error("Error processing scanner_match event in StrategyEngine", error=str(e))

    async def _on_candle_event(self, event: EventModel) -> None:
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            candle = Candle(**raw_candle)
            if candle.complete:
                await self.evaluate_all(candle.symbol, candle.timeframe)
        except Exception as e:
            logger.error("Error processing candle event in StrategyEngine", error=str(e))

    async def evaluate_all(self, symbol: str, timeframe: Timeframe, publish_events: bool = True) -> List[StrategyResponse]:
        """
        Builds the context for symbol/timeframe and evaluates all active strategies.
        """
        context = await self._build_context(symbol, timeframe)
        responses: List[StrategyResponse] = []

        active_strategies = self.registry.get_active()
        for strategy in active_strategies:
            try:
                matched = self.evaluator.evaluate_group(strategy.rules, context)
                
                status_str = "ACTIVE" if strategy.enabled else "DISABLED"
                
                if matched:
                    action_info = strategy.actions.get("matched", {})
                    resp = StrategyResponse(
                        strategy_name=strategy.name,
                        version=strategy.version,
                        status=status_str,
                        matched=True,
                        confidence=float(action_info.get("confidence", 100.0)),
                        reason=action_info.get("reason", "All rules matched successfully."),
                        required_action=action_info.get("action", "SIGNAL_NONE")
                    )
                    responses.append(resp)
                    
                    # Publish Matched event
                    if publish_events:
                        evt = StrategyMatchedEvent(
                            symbol=symbol,
                            timeframe=timeframe.value if hasattr(timeframe, "value") else str(timeframe),
                            strategy_response=resp,
                            context=context.get("current", {})
                        )
                        await event_bus.publish(EventModel(
                            event_type="strategy_matched",
                            source_agent="strategy_engine",
                            payload=evt.model_dump()
                        ))
                else:
                    resp = StrategyResponse(
                        strategy_name=strategy.name,
                        version=strategy.version,
                        status=status_str,
                        matched=False,
                        confidence=0.0,
                        reason="One or more rules failed matching.",
                        required_action=None
                    )
                    responses.append(resp)
                    
                    # Publish Rejected event
                    if publish_events:
                        evt = StrategyRejectedEvent(
                            symbol=symbol,
                            timeframe=timeframe.value if hasattr(timeframe, "value") else str(timeframe),
                            strategy_response=resp,
                            context=context.get("current", {})
                        )
                        await event_bus.publish(EventModel(
                            event_type="strategy_rejected",
                            source_agent="strategy_engine",
                            payload=evt.model_dump()
                        ))
            except Exception as e:
                logger.error(f"Failed to evaluate strategy {strategy.name}", error=str(e))

        return responses

    async def _build_context(self, symbol: str, timeframe: Timeframe) -> Dict[str, Any]:
        """Gathers latest values and previous values into a rule context."""
        # ── 1. Gather Current Context ─────────────────────────────────────────
        curr_indicators = {}
        prev_indicators = {}
        
        # Load all registered indicators
        from indicators.registry import INDICATOR_REGISTRY
        for name in INDICATOR_REGISTRY.keys():
            # Current
            r = await indicator_engine.cache.get_latest(symbol, timeframe, name)
            if r:
                curr_indicators[name] = r.value
            # Previous
            pr = await indicator_engine.cache.get_previous(symbol, timeframe, name)
            if pr:
                prev_indicators[name] = pr.value

        # Market Regime
        regime_val = None
        r_reg = await regime_engine.cache.get_latest(symbol, timeframe)
        if r_reg:
            regime_val = r_reg.market_regime.value

        # Risk Status
        risk_status_val = "ALLOW"
        try:
            risk_metrics = await risk_engine.get_dashboard_metrics()
            risk_status_val = risk_metrics.get("risk_status", "ALLOW")
        except Exception:
            pass

        # Market Data
        mkt_dict = {}
        tick = await market_data_manager.cache.get_tick(symbol)
        if tick:
            mkt_dict = {
                "ltp": tick.ltp,
                "vwap": tick.vwap,
                "volume": tick.volume,
                "oi": tick.open_interest,
                "open": tick.open,
                "high": tick.high,
                "low": tick.low,
                "close": tick.close,
                "prev_close": tick.prev_close
            }

        now = datetime.now(timezone.utc)
        current_context = {
            "indicators": curr_indicators,
            "market_regime": regime_val,
            "risk_status": risk_status_val,
            "market_data": mkt_dict,
            "time": now.strftime("%H:%M"),
            "session": market_data_manager.status.get_status().value
        }

        # ── 2. Gather Previous Context ────────────────────────────────────────
        previous_context = {
            "indicators": prev_indicators,
            "market_regime": None,
            "risk_status": None,
            "market_data": {},
            "time": None,
            "session": None
        }

        return {
            "current": current_context,
            "prev": previous_context
        }

# Singleton instance
strategy_engine = StrategyEngine()
