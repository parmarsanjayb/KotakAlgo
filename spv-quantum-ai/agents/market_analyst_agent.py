import time
from typing import Any, Dict, List, Optional
from core.agent import BaseAgent, AgentResultModel
from core.bus import EventModel
from market.models import Timeframe, Candle
from analysis.engine import market_analysis_engine

class MarketAnalystAgent(BaseAgent):
    """
    Market Analyst AI Agent.
    Subscribes to candle events, calls the MarketAnalysisEngine to perform
    multi-dimensional analysis, and publishes MarketAnalysisReport events.
    Does not place trades.
    """
    def __init__(self) -> None:
        super().__init__(
            name="market_analyst_agent",
            description="Analyzes regime, volatility, momentum, indicators, and price structure to output intelligence reports"
        )

    @property
    def input_event_types(self) -> List[str]:
        return ["candle"]

    @property
    def output_event_types(self) -> List[str]:
        return ["market_analysis"]

    async def initialize(self) -> None:
        self.log_info("MarketAnalystAgent initialized.")

    async def shutdown(self) -> None:
        self.log_info("MarketAnalystAgent shutdown complete.")

    async def analyze(self, event: EventModel) -> Optional[AgentResultModel]:
        if event.event_type != "candle":
            return None
            
        try:
            payload = event.payload
            raw_candle = payload.get("candle", payload)
            candle = Candle(**raw_candle)
            
            if not candle.complete:
                return None
                
            self.log_info(f"Triggering market analysis for {candle.symbol} ({candle.timeframe.value})")
            
            start_time = time.perf_counter()
            report = await market_analysis_engine.analyze_market(candle.symbol, candle.timeframe)
            processing_time = (time.perf_counter() - start_time) * 1000.0
            
            # Save results into agent's decisions structure
            return AgentResultModel(
                agent_name=self.agent_name,
                signal="NONE",
                confidence=report.confidence,
                reason=report.reasoning,
                processing_time=processing_time,
                metadata=report.model_dump()
            )
        except Exception as e:
            self.log_error(f"Failed to perform market analysis: {e}")
            return None
