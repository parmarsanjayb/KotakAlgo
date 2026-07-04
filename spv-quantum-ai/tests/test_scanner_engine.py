import pytest
import asyncio
from datetime import datetime, timezone
import os
import yaml
from market.models import Timeframe
from scanner.models import ScanResult, ScannerConfig, ScannerEvent
from scanner.cache import ScannerCache
from scanner.publisher import ScannerPublisher
from scanner.registry import ScannerRegistry
from scanner.engine import MarketScannerEngine
from scanner.scheduler import ScannerScheduler
from core.bus import event_bus, EventModel

# ── Registry & Cache Tests ───────────────────────────────────────────────────

def test_scanner_registry_load_configs(tmp_path):
    dir_path = tmp_path / "scanners"
    dir_path.mkdir()
    
    registry = ScannerRegistry(str(dir_path))
    
    sample = {
        "name": "CustomVolScanner",
        "enabled": True,
        "segment": "Equity",
        "filter_type": "VolumeSpike",
        "params": {"volume_multiplier": 3.0},
        "priority": 1
    }
    
    file_path = dir_path / "custom_vol.yaml"
    with open(file_path, "w") as f:
        yaml.safe_dump(sample, f)
        
    registry.load_all()
    assert len(registry.get_all()) == 1
    
    cfg = registry.get_scanner("CustomVolScanner")
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.priority == 1
    assert cfg.segment == "Equity"
    
    registry.set_enabled("CustomVolScanner", False)
    assert len(registry.get_active()) == 0


@pytest.mark.asyncio
async def test_scanner_cache_operations():
    cache = ScannerCache()
    res = ScanResult(
        symbol="NIFTY50", exchange="NSE", segment="Index Futures",
        scanner_name="VolumeSpikeScanner", priority=1, confidence=80.0,
        matched_conditions=["Volume spike"], scan_timestamp=datetime.now(timezone.utc)
    )
    
    await cache.store(res)
    matches = await cache.get_matches("VolumeSpikeScanner")
    assert len(matches) == 1
    assert matches[0].symbol == "NIFTY50"
    
    # Clear
    await cache.clear_scanner("VolumeSpikeScanner")
    matches_after = await cache.get_matches("VolumeSpikeScanner")
    assert len(matches_after) == 0


# ── Engine & Heuristics Tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_market_scanner_engine_filters(tmp_path):
    event_bus.start()
    
    dir_path = tmp_path / "scanners"
    dir_path.mkdir()
    
    # 1. Setup scanner configs: VolumeSpike (with explicit average_volume) and PriceBreakout
    vol_cfg = {
        "name": "VolScanner", "enabled": True, "segment": "Equity",
        "filter_type": "VolumeSpike", "params": {"volume_multiplier": 2.0, "average_volume": 10.0}, "priority": 1
    }
    breakout_cfg = {
        "name": "BreakoutScanner", "enabled": True, "segment": "Equity",
        "filter_type": "PriceBreakout", "params": {}, "priority": 2
    }
    
    with open(dir_path / "vol.yaml", "w") as f:
        yaml.safe_dump(vol_cfg, f)
    with open(dir_path / "breakout.yaml", "w") as f:
        yaml.safe_dump(breakout_cfg, f)
        
    engine = MarketScannerEngine(directory=str(dir_path))
    await engine.start()
    
    # 2. Mock Market Data cache
    from market.manager import market_data_manager
    from market.models import MarketData
    
    # NIFTY50: Volume spike simulation (volume = 50 > avg 10 * 2.0)
    tick1 = MarketData(symbol="NIFTY50", ltp=24200.0, prev_close=24200.0, volume=50.0)
    await market_data_manager.cache.update_tick(tick1)
    # Register symbol in registry
    market_data_manager.registry.register("NIFTY50")
    
    # RELIANCE: Breakout simulation (BB Upper = 2450.0, LTP = 2460.0)
    tick2 = MarketData(symbol="RELIANCE", ltp=2460.0, prev_close=2450.0, volume=5.0)
    await market_data_manager.cache.update_tick(tick2)
    market_data_manager.registry.register("RELIANCE")
    
    from indicators.engine import indicator_engine
    from indicators.models import IndicatorResult
    await indicator_engine.cache.store(IndicatorResult(
        indicator_name="BOLLINGER", symbol="RELIANCE", timeframe=Timeframe.M1,
        value={"upper": 2450.0, "middle": 2430.0, "lower": 2410.0, "bandwidth": 1.6}
    ))

    # 3. Run scan
    results = await engine.run_scan()
    
    # Verify matches
    assert len(results) >= 2
    names = {r.scanner_name for r in results}
    assert "VolScanner" in names
    assert "BreakoutScanner" in names
    
    # Clean up registry
    market_data_manager.registry.unregister("NIFTY50")
    market_data_manager.registry.unregister("RELIANCE")
    await engine.stop()
    await event_bus.stop()


# ── Scheduler Tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scanner_scheduler_triggers():
    scheduler = ScannerScheduler(interval_sec=0.1)
    
    # Verify it runs and stops cleanly
    await scheduler.start()
    assert scheduler._running is True
    assert scheduler._task is not None
    
    await asyncio.sleep(0.2)
    await scheduler.stop()
    assert scheduler._running is False
    assert scheduler._task is None
