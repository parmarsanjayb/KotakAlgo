import pytest
import asyncio
from datetime import datetime, timezone
from core.bus import event_bus, EventModel
from charges.models import BrokerProfile, BrokerageRules, TaxRules, ExchangeChargeRules, TradeChargesBreakdown
from charges.calculators import BrokerageCalculator, TaxCalculator, ExchangeChargeCalculator
from charges.engine import charges_engine, trade_cost_manager

@pytest.mark.asyncio
async def test_calculators_kotak_neo():
    # Fetch active/default profile (Kotak Neo)
    profile = await charges_engine.get_active_profile()
    assert profile.name == "kotak_neo"
    
    # Kotak Neo has free brokerage (0.0) across all segments
    brokerage_intraday = BrokerageCalculator.calculate(profile, "Equity Intraday", "BUY", 100, 1500.0)
    assert brokerage_intraday == 0.0
    
    brokerage_options = BrokerageCalculator.calculate(profile, "Options", "SELL", 1, 100.0)
    assert brokerage_options == 0.0

@pytest.mark.asyncio
async def test_calculators_zerodha():
    # Load Zerodha profile rules
    zerodha = charges_engine.profiles["zerodha"]
    
    # Zerodha intraday is 0.03% capped at 20
    # Turnover = 100 * 100 = 10000. Brokerage = 10000 * 0.0003 = 3.0
    brok_intraday = BrokerageCalculator.calculate(zerodha, "Equity Intraday", "BUY", 100, 100.0)
    assert brok_intraday == pytest.approx(3.0)
    
    # Cap test: Turnover = 1000 * 1000 = 1000000. Brokerage = 1000000 * 0.0003 = 300 capped at 20
    brok_intraday_cap = BrokerageCalculator.calculate(zerodha, "Equity Intraday", "BUY", 1000, 1000.0)
    assert brok_intraday_cap == pytest.approx(20.0)
    
    # Zerodha delivery is free (0.0)
    brok_delivery = BrokerageCalculator.calculate(zerodha, "Equity Delivery", "BUY", 100, 1500.0)
    assert brok_delivery == 0.0
    
    # Zerodha options flat 20
    brok_options = BrokerageCalculator.calculate(zerodha, "Options", "SELL", 2, 200.0)
    assert brok_options == 20.0

@pytest.mark.asyncio
async def test_taxes_and_exchange_charges():
    profile = charges_engine.profiles["zerodha"]
    
    # 1. STT
    # Intraday buy has no STT
    stt_buy = TaxCalculator.calculate_stt(profile, "Equity Intraday", "BUY", 10, 1000.0)
    assert stt_buy == 0.0
    
    # Intraday sell has 0.025% STT
    # Turnover = 10 * 1000 = 10000. STT = 10000 * 0.00025 = 2.5
    stt_sell = TaxCalculator.calculate_stt(profile, "Equity Intraday", "SELL", 10, 1000.0)
    assert stt_sell == 2.5
    
    # Delivery has 0.1% STT on both buy/sell
    stt_del_buy = TaxCalculator.calculate_stt(profile, "Equity Delivery", "BUY", 10, 1000.0)
    assert stt_del_buy == 10.0
    
    # 2. Stamp Duty (BUY side only)
    stamp_buy = TaxCalculator.calculate_stamp_duty(profile, "Equity Intraday", "BUY", 100, 100.0)
    assert stamp_buy == pytest.approx(0.3)  # 10000 * 0.00003 = 0.3
    
    stamp_sell = TaxCalculator.calculate_stamp_duty(profile, "Equity Intraday", "SELL", 100, 100.0)
    assert stamp_sell == 0.0

    # 3. Exchange transaction
    # Txn = 10000 * 0.0000345 = 0.345
    txn = ExchangeChargeCalculator.calculate_exchange_txn(profile, "Equity Intraday", 100, 100.0)
    assert txn == pytest.approx(0.345)
    
    # 4. GST
    # 18% of (20.0 + 0.345) = 3.6621
    gst = TaxCalculator.calculate_gst(profile, 20.0, 0.345)
    assert gst == pytest.approx(3.6621)

@pytest.mark.asyncio
async def test_charges_engine_end_to_end():
    event_bus.start()
    await trade_cost_manager.start()
    
    # Set to Zerodha for calculation variability
    await charges_engine.set_active_profile("zerodha")
    
    try:
        # Reset cost stats
        async with trade_cost_manager._lock:
            trade_cost_manager.todays_charges = 0.0
            trade_cost_manager.gross_profit = 0.0
            trade_cost_manager.net_profit = 0.0
            
        # Simulate fill
        await charges_engine.calculate_charges(
            order_id="ORD-TST-1",
            symbol="RELIANCE",
            side="BUY",
            qty=10.0,
            price=2500.0,
            segment="Equity Intraday"
        )
        
        # Wait for Event Bus dispatch
        await asyncio.sleep(0.05)
        
        # Verify TradeCostManager updated
        summary = await trade_cost_manager.get_dashboard_summary()
        assert summary["todays_charges"] > 0.0
        assert summary["brokerage_breakdown"] > 0.0
        assert summary["tax_breakdown"] > 0.0
        
    finally:
        await trade_cost_manager.stop()
        await event_bus.stop()
        # Reset back to default profile
        await charges_engine.set_active_profile("kotak_neo")
