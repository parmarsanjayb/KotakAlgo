import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from market.manager import market_data_manager
from indicators.engine import indicator_engine
from regime.engine import regime_engine

from scanner.models import ScanResult, ScannerConfig
from scanner.registry import ScannerRegistry
from scanner.cache import ScannerCache
from scanner.publisher import ScannerPublisher
from core.logging import get_logger

logger = get_logger("scanner_engine")

class MarketScannerEngine:
    """
    Market Scanner Engine.
    Scans all registered instruments against configured opportunity filters.
    Does not place trades.
    """
    def __init__(self, directory: str = "config/scanners") -> None:
        self.registry = ScannerRegistry(directory)
        self.cache = ScannerCache()
        self.publisher = ScannerPublisher()
        self.registry.load_all()
        
        self.scan_time_ms = 0.0
        self.health_status = "HEALTHY"
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("MarketScannerEngine started.")

    async def stop(self) -> None:
        self._running = False
        logger.info("MarketScannerEngine stopped.")

    async def run_scan(self) -> List[ScanResult]:
        """
        Executes scan for all active scanners over all symbols in registry.
        Uses synchronous in-memory resolution to run in <10ms.
        """
        start_time = time.perf_counter()
        active_scanners = self.registry.get_active()
        symbols = list(market_data_manager.registry.get_symbols())
        
        results = []
        for scanner in active_scanners:
            # Clear previous matches for this scanner
            await self.cache.clear_scanner(scanner.name)
            for symbol in symbols:
                try:
                    out = self._scan_symbol_sync(symbol, scanner)
                    if out:
                        results.append(out)
                        await self.cache.store(out)
                        await self.publisher.publish(out)
                except Exception as e:
                    logger.error(f"Error scanning symbol {symbol} with {scanner.name}: {e}")
                    self.health_status = "DEGRADED"

        self.scan_time_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(f"Scan complete in {self.scan_time_ms:.2f}ms. Found {len(results)} matches.")
        return results

    def _scan_symbol_sync(self, symbol: str, scanner: ScannerConfig) -> Optional[ScanResult]:
        # 1. Resolve Instrument Metadata
        inst = market_data_manager.instruments.get(symbol)
        exchange = inst.get("exchange", "UNKNOWN") if inst else "UNKNOWN"
        segment = scanner.segment

        # 2. Gather inputs
        tick = market_data_manager.cache._ticks.get(symbol)
        if not tick:
            return None

        # Fetch indicators/regimes for rules
        # Use default Timeframe.M1 for scanning
        from market.models import Timeframe
        rsi = 50.0
        atr = 0.0
        bb_upper = tick.ltp
        bb_lower = tick.ltp
        bb_bw = 0.0
        
        # Access indicator cache directly without locks to avoid asyncio overhead
        tf_val = Timeframe.M1.value
        store = indicator_engine.cache._values.get(symbol, {}).get(tf_val, {})
        
        r_rsi_list = store.get("RSI", [])
        if r_rsi_list:
            r_rsi = r_rsi_list[-1]
            if r_rsi and isinstance(r_rsi.value, (int, float)):
                rsi = float(r_rsi.value)
            
        r_atr_list = store.get("ATR", [])
        if r_atr_list:
            r_atr = r_atr_list[-1]
            if r_atr and isinstance(r_atr.value, (int, float)):
                atr = float(r_atr.value)
            
        r_bb_list = store.get("BOLLINGER", [])
        if r_bb_list:
            r_bb = r_bb_list[-1]
            if r_bb and isinstance(r_bb.value, dict):
                bb_upper = float(r_bb.value.get("upper", tick.ltp))
                bb_lower = float(r_bb.value.get("lower", tick.ltp))
                bb_bw = float(r_bb.value.get("bandwidth", 0.0))

        # 3. Evaluate filter
        matched = False
        conditions = []
        conf = 50.0
        
        f_type = scanner.filter_type
        params = scanner.params

        if f_type == "VolumeSpike" or f_type == "HighRelativeVolume":
            mult = float(params.get("volume_multiplier", 2.0))
            avg_vol = params.get("average_volume")
            if avg_vol is None:
                session_vol = market_data_manager.cache._volume.get(symbol, 0.0)
                avg_vol = session_vol if session_vol > 0 else 10.0
            avg_vol = float(avg_vol)
                
            if tick.volume > avg_vol * mult:
                matched = True
                conditions.append(f"Volume spike: {tick.volume:.1f} > average {avg_vol:.1f} * {mult}")
                conf = 80.0

        elif f_type == "PriceBreakout":
            if tick.ltp > bb_upper:
                matched = True
                conditions.append(f"Price breakout above Bollinger Upper Band: {tick.ltp:.2f} > {bb_upper:.2f}")
                conf = 85.0
            elif tick.ltp < bb_lower:
                matched = True
                conditions.append(f"Price breakdown below Bollinger Lower Band: {tick.ltp:.2f} < {bb_lower:.2f}")
                conf = 85.0

        elif f_type == "GapUp":
            gap_pct = ((tick.ltp - tick.prev_close) / tick.prev_close * 100) if tick.prev_close > 0 else 0
            limit = float(params.get("gap_threshold_pct", 0.5))
            if gap_pct >= limit:
                matched = True
                conditions.append(f"Gap Up: {gap_pct:.2f}% >= threshold {limit}%")
                conf = 75.0

        elif f_type == "GapDown":
            gap_pct = ((tick.prev_close - tick.ltp) / tick.prev_close * 100) if tick.prev_close > 0 else 0
            limit = float(params.get("gap_threshold_pct", 0.5))
            if gap_pct >= limit:
                matched = True
                conditions.append(f"Gap Down: {gap_pct:.2f}% >= threshold {limit}%")
                conf = 75.0

        elif f_type == "HighOIChange":
            oi_change = abs(tick.open_interest)
            limit = float(params.get("oi_change_threshold", 5000.0))
            if oi_change >= limit:
                matched = True
                conditions.append(f"High OI Change: {oi_change} >= threshold {limit}")
                conf = 70.0

        elif f_type == "VWAPDeviation":
            dev_pct = abs(tick.ltp - tick.vwap) / tick.vwap * 100 if tick.vwap > 0 else 0
            limit = float(params.get("deviation_threshold_pct", 1.5))
            if dev_pct >= limit:
                matched = True
                conditions.append(f"VWAP Deviation: {dev_pct:.2f}% >= threshold {limit}%")
                conf = 70.0

        elif f_type == "ATRExpansion":
            if atr > 0:
                matched = True
                conditions.append(f"ATR Expansion: {atr:.2f}")
                conf = 65.0

        elif f_type == "52WeekHigh":
            sess_high = market_data_manager.cache._session_high.get(symbol, 0.0)
            if tick.ltp >= sess_high * 0.999:
                matched = True
                conditions.append(f"Price near Session High: {tick.ltp:.2f} vs High {sess_high:.2f}")
                conf = 75.0

        elif f_type == "52WeekLow":
            sess_low = market_data_manager.cache._session_low.get(symbol, 0.0)
            if tick.ltp <= sess_low * 1.001:
                matched = True
                conditions.append(f"Price near Session Low: {tick.ltp:.2f} vs Low {sess_low:.2f}")
                conf = 75.0

        elif f_type == "OpeningRangeBreak":
            sess_high = market_data_manager.cache._session_high.get(symbol, 0.0)
            if tick.ltp > sess_high:
                matched = True
                conditions.append(f"Opening Range Breakout: {tick.ltp:.2f} > High {sess_high:.2f}")
                conf = 80.0

        elif f_type == "MomentumExpansion":
            if rsi > 70:
                matched = True
                conditions.append(f"Overbought Momentum Expansion: RSI {rsi:.1f} > 70")
                conf = 80.0
            elif rsi < 30:
                matched = True
                conditions.append(f"Oversold Momentum Expansion: RSI {rsi:.1f} < 30")
                conf = 80.0

        if matched:
            return ScanResult(
                symbol=symbol,
                exchange=exchange,
                segment=segment,
                scanner_name=scanner.name,
                priority=scanner.priority,
                confidence=conf,
                matched_conditions=conditions,
                scan_timestamp=datetime.now(timezone.utc)
            )
            
        return None

# Singleton instance
market_scanner_engine = MarketScannerEngine()
