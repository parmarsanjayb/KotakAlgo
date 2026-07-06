import pytest
import asyncio
import json
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock
from market.websocket import WebSocketStreamManager
from market.health import FeedHealthMonitor
from core.config import settings

@pytest.mark.asyncio
async def test_websocket_stream_manager_mode_selection() -> None:
    # Test default mode (should be 'mock' based on modified settings.yaml)
    monitor = FeedHealthMonitor()
    manager = WebSocketStreamManager(on_raw_tick=lambda x: None, health_monitor=monitor)
    assert manager._feed_mode == "mock"

@pytest.mark.asyncio
async def test_binance_feed_loop_subscription() -> None:
    monitor = FeedHealthMonitor()
    received_ticks = []
    
    async def on_tick(tick):
        received_ticks.append(tick)
        
    manager = WebSocketStreamManager(on_raw_tick=on_tick, health_monitor=monitor)
    manager._feed_mode = "binance"
    manager._running = True
    manager._connected = True

    # Mock websockets.connect context manager
    mock_ws = AsyncMock()
    mock_ws.__aenter__.return_value = mock_ws
    
    # Simulate a single ticker message followed by loop termination
    ticker_msg = {
        "e": "24hrTicker",
        "s": "BTCUSDT",
        "c": "67500.00",
        "v": "2.5",
        "b": "67490.00",
        "a": "67510.00",
        "w": "67500.00",
        "o": "66000.00",
        "h": "68000.00",
        "l": "65000.00"
    }
    
    async def recv_side_effect():
        # Stop manager after receiving one message to prevent infinite loop
        manager._connected = False
        manager._running = False
        return json.dumps(ticker_msg)
        
    mock_ws.recv.side_effect = recv_side_effect
    mock_ws.send = AsyncMock()

    with mock.patch("websockets.connect", return_value=mock_ws) as mock_connect:
        await manager._binance_loop()
        
        mock_connect.assert_called_once()
        mock_ws.send.assert_called_once()
        sent_data = json.loads(mock_ws.send.call_args[0][0])
        assert sent_data["method"] == "SUBSCRIBE"
        assert "btcusdt@ticker" in sent_data["params"]
        
        # Verify the tick was parsed and forwarded
        await asyncio.sleep(0.1) # allow task to execute
        assert len(received_ticks) == 1
        tick = received_ticks[0]
        assert tick["symbol"] == "BTCUSD"
        assert tick["price"] == 67500.0
        assert tick["volume"] == 2.5

@pytest.mark.asyncio
async def test_kotak_feed_loop_subscription() -> None:
    monitor = FeedHealthMonitor()
    received_ticks = []
    
    async def on_tick(tick):
        received_ticks.append(tick)
        
    manager = WebSocketStreamManager(on_raw_tick=on_tick, health_monitor=monitor)
    manager._feed_mode = "kotak"
    manager._running = True
    manager._connected = True

    mock_ws = AsyncMock()
    mock_ws.__aenter__.return_value = mock_ws
    
    ticker_msg = {
        "symbol": "NIFTY50",
        "price": "24300.00",
        "volume": "100",
        "open_interest": "5000",
        "vwap": "24300.00"
    }
    
    async def recv_side_effect():
        manager._connected = False
        manager._running = False
        return json.dumps(ticker_msg)
        
    mock_ws.recv.side_effect = recv_side_effect
    mock_ws.send = AsyncMock()

    # Mock authenticate
    mock_auth = AsyncMock()
    mock_auth.authenticate = AsyncMock(return_value=True)
    mock_auth.session_token = "mock-kotak-token"

    with mock.patch("websockets.connect", return_value=mock_ws) as mock_connect:
        with mock.patch("brokers.kotak_neo.KotakAuthenticationManager", return_value=mock_auth):
            await manager._kotak_loop()
            
            mock_connect.assert_called_once()
            mock_ws.send.assert_called_once()
            sent_data = json.loads(mock_ws.send.call_args[0][0])
            assert "mock-kotak-token" in sent_data["Authorization"]
            assert sent_data["action"] == "subscribe"
            
            # Verify the tick was parsed and forwarded
            await asyncio.sleep(0.1)
            assert len(received_ticks) == 1
            tick = received_ticks[0]
            assert tick["symbol"] == "NIFTY50"
            assert tick["price"] == 24300.0
