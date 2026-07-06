import asyncio
import sys
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from market.manager import market_data_manager
from market.models import MarketData, Timeframe
from indicators.engine import indicator_engine
from indicators.models import IndicatorResult
from regime.engine import regime_engine
from regime.models import RegimeResult, MarketRegime
from paper.engine import paper_trading_engine
from paper.models import PaperTradingConfig
from agents.chief_decision_agent import ChiefDecisionAgent
from agents.market_analyst_agent import MarketAnalystAgent
from agents.risk_agent import RiskAgent
from agents.execution_agent import ExecutionAgent
from scanner.models import ScanResult
from scanner.publisher import ScannerPublisher
from portfolio.engine import portfolio_engine

from database.connection import init_db

async def main():
    print("Initializing components...")
    await init_db()
    event_bus.start()
    
    # Instantiate agents
    chief = ChiefDecisionAgent()
    analyst = MarketAnalystAgent()
    risk_a = RiskAgent()
    exec_a = ExecutionAgent()
    
    # Start agents (this registers their event bus subscriptions)
    await chief.start()
    await analyst.start()
    await risk_a.start()
    await exec_a.start()
    
    # Start engines
    await portfolio_engine.start()
    from journal.engine import trade_journal_engine
    await trade_journal_engine.start()
    from execution.engine import execution_engine as exec_eng
    await exec_eng.start()
    from scoring.engine import decision_scoring_engine
    await decision_scoring_engine.start()
    from strategies.engine import strategy_engine
    await strategy_engine.start()
    
    # Start Paper Trading session
    await paper_trading_engine.start()
    # Configure zero rejection / partial fill rates for deterministic test
    config = PaperTradingConfig(initial_capital=1000000.0)
    session_id = await paper_trading_engine.start_session(config)
    
    # Set paper broker parameters to be 100% deterministic
    from brokers.manager import broker_manager
    broker = broker_manager.get_active()
    broker._rejection_rate = 0.0
    broker._partial_fill_rate = 0.0
    
    print(f"Paper trading session started: {session_id}")
    
    # Force-reset RiskEngine managers to clean state
    from risk.engine import risk_engine
    risk_engine.drawdown_mgr.peak_equity = 1000000.0
    risk_engine.daily_loss_mgr.daily_pnl = 0.0
    risk_engine.daily_loss_mgr.weekly_pnl = 0.0
    risk_engine.limit_mgr.daily_trades_count = 0
    risk_engine.limit_mgr.consecutive_losses = 0
    risk_engine.limit_mgr.cooldown_until = None
    
    # Disable safety guards in safety engine to bypass market time checks
    from safety.engine import safety_engine
    safety_engine.config["market_closing_guard"] = False
    safety_engine.config["trading_session_guard"] = False
    safety_engine.config["holiday_guard"] = False
    safety_engine.config["broker_disconnect_guard"] = False
    
    print("Initial Risk Metrics:", await risk_engine.get_dashboard_metrics())
    
    # Seed cache data
    symbol = "BANKNIFTY"
    print(f"Seeding market state for {symbol}...")
    await market_data_manager.cache.update_tick(
        MarketData(symbol=symbol, ltp=58294.80, prev_close=58000.0, volume=15000.0)
    )
    await market_data_manager.cache.set_prev_close(symbol, 58000.0)
    
    # Seed indicator cache to satisfy:
    # 1. EMA_9 crosses_above EMA_20 (EMA_9 prev <= EMA_20 prev, EMA_9 curr > EMA_20 curr)
    # 2. RSI > 50.0
    await indicator_engine.cache.store(
        IndicatorResult(indicator_name="EMA_9", symbol=symbol, timeframe=Timeframe.M1, value=95.0)
    )
    await indicator_engine.cache.store(
        IndicatorResult(indicator_name="EMA_9", symbol=symbol, timeframe=Timeframe.M1, value=105.0)
    )
    await indicator_engine.cache.store(
        IndicatorResult(indicator_name="EMA_20", symbol=symbol, timeframe=Timeframe.M1, value=100.0)
    )
    await indicator_engine.cache.store(
        IndicatorResult(indicator_name="EMA_20", symbol=symbol, timeframe=Timeframe.M1, value=100.0)
    )
    await indicator_engine.cache.store(
        IndicatorResult(indicator_name="RSI", symbol=symbol, timeframe=Timeframe.M1, value=55.0)
    )
    
    # Seed regime cache to satisfy TRENDING_BULLISH
    await regime_engine.cache.store(
        RegimeResult(
            symbol=symbol,
            timeframe=Timeframe.M1,
            market_regime=MarketRegime.TRENDING_BULLISH,
            confidence=85.0,
            reason="Confirmed Bullish Uptrend"
        )
    )
    
    # Register callback to verify that the order was filled
    order_filled_future = asyncio.get_running_loop().create_future()
    
    async def on_order_filled(event: EventModel):
        payload = event.payload
        order_data = payload.get("order", payload)
        if order_data.get("symbol") == symbol:
            print(f"SUCCESS: Order filled event caught! Details: {order_data}")
            order_filled_future.set_result(order_data)
            
    await event_bus.subscribe("order_filled", on_order_filled)

    # Seed employee engine states for BANKNIFTY to allow CEO approval
    from employees import employee_engine
    employee_engine.trend_intelligence.latest_results[symbol] = {"recommendation": "BUY", "confidence": 90.0}
    employee_engine.volume_intelligence.latest_results[symbol] = {"confirmation_status": "CONFIRM", "confidence": 85.0}
    employee_engine.risk_emp.latest_results["SYSTEM"] = {"recommendation": "BUY", "confidence": 90.0}

    # All weighted employees recommend BUY
    for emp in [employee_engine.vwap_emp, employee_engine.momentum, employee_engine.liquidity, 
                employee_engine.oi_emp, employee_engine.pcr_emp, employee_engine.greeks, employee_engine.option_flow]:
        emp.latest_results[symbol] = {"recommendation": "BUY", "confidence": 85.0}

    # Advisory employees
    employee_engine.news_emp.latest_results[symbol] = {"recommendation": "BUY", "confidence": 75.0}
    employee_engine.calendar.latest_results[symbol] = {"recommendation": "BUY", "confidence": 75.0}
    employee_engine.event_risk.latest_results[symbol] = {"recommendation": "BUY", "confidence": 75.0}
    
    # Simulate a Scanner Match event
    print("Publishing simulated scanner match...")
    scan_res = ScanResult(
        symbol=symbol,
        exchange="NSE",
        segment="Options",
        scanner_name="PriceBreakout",
        priority=1,
        confidence=85.0,
        matched_conditions=["Price breakout above Bollinger Upper Band: 58294.80 > 58000.0"],
        scan_timestamp=datetime.now(timezone.utc)
    )
    
    # Publish match
    scanner_publisher = ScannerPublisher()
    await scanner_publisher.publish(scan_res)
    
    # Wait for the order filled callback (with 5 seconds timeout)
    try:
        filled_order = await asyncio.wait_for(order_filled_future, timeout=5.0)
        print("End-to-End Pipeline execution verified successfully!")
    except asyncio.TimeoutError:
        print("ERROR: Pipeline hung or order_filled event was not published within timeout!")
        sys.exit(1)
        
    # Clean up
    print("Stopping components...")
    await event_bus.unsubscribe("order_filled", on_order_filled)
    await paper_trading_engine.stop_session()
    await chief.stop()
    await analyst.stop()
    await risk_a.stop()
    await exec_a.stop()
    await exec_eng.stop()
    await portfolio_engine.stop()
    await trade_journal_engine.stop()
    await event_bus.stop()
    print("Shutdown complete.")

if __name__ == "__main__":
    asyncio.run(main())
