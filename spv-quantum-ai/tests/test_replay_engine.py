import pytest
import asyncio
from datetime import datetime, timezone
from replay.models import ReplayConfig, ReplayState
from replay.engine import replay_engine
from core.bus import event_bus, EventModel

@pytest.mark.asyncio
async def test_market_replay_controls():
    event_bus.start()
    
    start = datetime(2026, 7, 4, 9, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 4, 9, 5, 0, tzinfo=timezone.utc)
    config = ReplayConfig(
        symbols=["TCS"],
        timeframe="1m",
        start_date=start,
        end_date=end,
        speed="1x",
        mode="Full Trading System"
    )

    # 1. Setup Replay
    await replay_engine.start()
    replay_id = await replay_engine.setup_replay(config)
    assert replay_id.startswith("RPL-")
    
    status = await replay_engine.get_dashboard_status()
    assert status["status"] == "PENDING"
    assert status["total_candles"] >= 1
    
    # 2. Play
    await replay_engine.play()
    status = await replay_engine.get_dashboard_status()
    assert status["status"] == "PLAYING"
    
    # 3. Pause
    await replay_engine.pause()
    status = await replay_engine.get_dashboard_status()
    assert status["status"] == "PAUSED"
    
    index_before = status["current_index"]
    
    # 4. Next Candle
    await replay_engine.next_candle()
    status = await replay_engine.get_dashboard_status()
    assert status["current_index"] == index_before + 1
    
    # 5. Previous Candle
    await replay_engine.previous_candle()
    status = await replay_engine.get_dashboard_status()
    assert status["current_index"] == index_before
    
    # 6. Resume & Speed increase to complete
    await replay_engine.set_speed("Unlimited")
    await replay_engine.resume()
    
    # Wait for completion
    for _ in range(50):
        status = await replay_engine.get_dashboard_status()
        if status["status"] in ("COMPLETED", "FAILED"):
            break
        await asyncio.sleep(0.05)
        
    status = await replay_engine.get_dashboard_status()
    assert status["status"] == "COMPLETED"
    assert status["progress_pct"] == 100.0
    
    await replay_engine.stop()
    await event_bus.stop()
