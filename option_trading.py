import os
import time
import random
import logging
import requests
import threading
from datetime import datetime, timezone, timedelta

from db import get_db
from kotak_auth import KOTAK_SDK_AVAILABLE, get_neo_client, login_kotak_neo
from order_manager import place_neo_order

logger = logging.getLogger("TradingBot.OptionTrading")

# Shared state variables
active_position = None  # None or dict: {'ticker': ..., 'entry_price': ..., 'qty': ..., 'max_price': ..., 'stop_loss': ..., 'type': 'BUY', 'entry_time': ...}
token_cache = {}
last_real_fetch_time = 0
last_auto_activation_date = None

# Fallback default premiums
options_feed = {
    "NIFTY_CE": 120.0,
    "NIFTY_PE": 120.0,
    "BANK_CE": 250.0,
    "BANK_PE": 250.0
}

# Live index prices
mock_stocks = {
    "NIFTY50": {"price": 23970.0, "high": 23970.0, "low": 23970.0},
    "BANKNIFTY": {"price": 57210.0, "high": 57210.0, "low": 57210.0}
}

# ----------------- TIMEZONE & UTIL FUNCTIONS -----------------

def get_ist_now():
    utc_now = datetime.now(timezone.utc)
    ist_timezone = timezone(timedelta(hours=5, minutes=30))
    return utc_now.astimezone(ist_timezone)

# ----------------- REAL-TIME INDEX PRICE FETCHING -----------------

def fetch_real_prices_neo():
    neo_client = get_neo_client()
    if not KOTAK_SDK_AVAILABLE or neo_client is None:
        return None
        
    try:
        # Fetch Nifty 50 (token 26000) and Nifty Bank (token 26009)
        tokens = [
            {"instrument_token": "26000", "exchange_segment": "nse_cm"},
            {"instrument_token": "26009", "exchange_segment": "nse_cm"}
        ]
        
        response = neo_client.quotes(
            instrument_tokens=tokens,
            quote_type="LTP"
        )
        
        nifty_price = None
        banknifty_price = None
        
        if response and isinstance(response, dict) and response.get('stat') == 'Ok':
            for item in response.get('data', []):
                if item.get('tok') == '26000':
                    nifty_price = float(item.get('ltp'))
                elif item.get('tok') == '26009':
                    banknifty_price = float(item.get('ltp'))
                    
        if nifty_price and banknifty_price:
            return {
                "NIFTY50": round(nifty_price, 2),
                "BANKNIFTY": round(banknifty_price, 2)
            }
    except Exception as e:
        logger.warning(f"Could not fetch quotes from Kotak Neo: {e}.")
    return None

def fetch_real_prices():
    # Try Kotak Neo first
    if KOTAK_SDK_AVAILABLE and get_neo_client() is not None:
        prices = fetch_real_prices_neo()
        if prices:
            return prices
            
    # Fallback to Yahoo Finance
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # Fetch Nifty 50 (^NSEI)
        r = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/^NSEI', headers=headers, timeout=3)
        nifty_price = r.json()['chart']['result'][0]['meta']['regularMarketPrice']
        
        # Fetch Bank Nifty (^NSEBANK)
        r2 = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/^NSEBANK', headers=headers, timeout=3)
        banknifty_price = r2.json()['chart']['result'][0]['meta']['regularMarketPrice']
        
        return {
            "NIFTY50": round(nifty_price, 2),
            "BANKNIFTY": round(banknifty_price, 2)
        }
    except Exception as e:
        logger.warning(f"Could not fetch real-time prices from Yahoo Finance: {e}")
        return None

# Load starting prices
try:
    real_prices = fetch_real_prices()
    if real_prices:
        mock_stocks["NIFTY50"]["price"] = real_prices["NIFTY50"]
        mock_stocks["NIFTY50"]["high"] = real_prices["NIFTY50"]
        mock_stocks["NIFTY50"]["low"] = real_prices["NIFTY50"]
        mock_stocks["BANKNIFTY"]["price"] = real_prices["BANKNIFTY"]
        mock_stocks["BANKNIFTY"]["high"] = real_prices["BANKNIFTY"]
        mock_stocks["BANKNIFTY"]["low"] = real_prices["BANKNIFTY"]
except Exception:
    pass

# ----------------- ATM STRIKE & PREMIUM ENGINE -----------------

def get_option_name(index_name, option_type):
    spot = mock_stocks[index_name]['price']
    strike = round(spot / 100) * 100
    symbol = "NIFTY" if index_name == "NIFTY50" else "BANKNIFTY"
    return f"{symbol} {strike} {option_type}"

def get_live_price(ticker):
    global token_cache, mock_stocks, options_feed
    
    if "CE" in ticker or "PE" in ticker:
        try:
            parts = ticker.strip().split(" ")
            if len(parts) >= 3:
                index_name = "NIFTY50" if "NIFTY" in parts[0] and "BANK" not in parts[0] else "BANKNIFTY"
                symbol = "NIFTY" if index_name == "NIFTY50" else "BANKNIFTY"
                strike = float(parts[1])
                option_type = parts[2]
                
                # Fetch settings for expiry date and implied volatility
                conn = get_db()
                settings = conn.execute("SELECT expiry_date, implied_volatility FROM settings WHERE id = 1").fetchone()
                conn.close()
                
                expiry_db = settings['expiry_date'] if settings and settings['expiry_date'] else '2026-06-30'
                iv = settings['implied_volatility'] if settings and settings['implied_volatility'] is not None else 0.165
                
                # Format expiry to DDMMMYYYY
                try:
                    expiry_dt = datetime.strptime(expiry_db, '%Y-%m-%d')
                    expiry_str = expiry_dt.strftime('%d%b%Y').upper()
                except Exception:
                    expiry_str = '30JUN2026'  # fallback
                
                # 1. Try Kotak Neo API if online
                neo_client = get_neo_client()
                if KOTAK_SDK_AVAILABLE and neo_client is not None:
                    cache_key = f"{symbol}|{expiry_str}|{option_type}|{strike}"
                    token = token_cache.get(cache_key)
                    if not token:
                        search_params = {
                            "exchange_segment": "nse_fo",
                            "symbol": symbol,
                            "expiry": expiry_str,
                            "option_type": option_type,
                            "strike_price": str(int(strike))
                        }
                        res = neo_client.search_scrip(**search_params)
                        if res and isinstance(res, dict) and res.get('stat') == 'Ok' and res.get('data'):
                            token = res['data'][0].get('token')
                            token_cache[cache_key] = token
                    
                    if token:
                        quotes = neo_client.quotes([{"instrument_token": token, "exchange_segment": "nse_fo"}])
                        if quotes and isinstance(quotes, dict) and quotes.get('stat') == 'Ok':
                            for item in quotes.get('data', []):
                                if 'ltp' in item and item['ltp'] is not None:
                                    return float(item['ltp'])
                
                # 2. Fallback to Black-Scholes Dynamic Premium Formula
                spot = mock_stocks[index_name]["price"]
                
                # Calculate time to expiry in years
                try:
                    expiry_dt = datetime.strptime(expiry_db, '%Y-%m-%d')
                    now_ist = get_ist_now().date()
                    days_remaining = max(0.5, (expiry_dt.date() - now_ist).days)
                except Exception:
                    days_remaining = 7.0
                    
                t_years = days_remaining / 365.0
                base_atm = 0.4 * spot * iv * (t_years ** 0.5)
                decay_rate = 0.88
                strike_interval = 100.0
                
                if option_type == "CE":
                    intrinsic = max(0.0, spot - strike)
                else:
                    intrinsic = max(0.0, strike - spot)
                    
                distance = abs(spot - strike)
                time_value = base_atm * (decay_rate ** (distance / strike_interval))
                
                return round(intrinsic + time_value, 2)
        except Exception as e:
            logger.error(f"Error calculating dynamic option price for {ticker}: {e}")
            
        # Absolute fallback to options_feed dictionary
        if "NIFTY" in ticker:
            return options_feed["NIFTY_CE"] if "CE" in ticker else options_feed["NIFTY_PE"]
        else:
            return options_feed["BANK_CE"] if "CE" in ticker else options_feed["BANK_PE"]
            
    return 100.0

# ----------------- MANUAL EXECUTION ACTIONS -----------------

def buy_option_manual(index_name, option_type):
    global active_position
    
    if active_position:
        return False, "An active option position is already open! Please square it off first."
        
    try:
        conn = get_db()
        settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        conn.close()
        
        trailing_sl_pct = settings['trailing_sl_pct'] / 100.0
        nifty_qty = settings['nifty_qty'] if settings['nifty_qty'] is not None else 65
        banknifty_qty = settings['banknifty_qty'] if settings['banknifty_qty'] is not None else 30
        qty = nifty_qty if index_name == "NIFTY50" else banknifty_qty
        
        opt_name = get_option_name(index_name, option_type)
        entry_price = get_live_price(opt_name)
        initial_sl = round(entry_price * (1 - trailing_sl_pct), 2)
        
        # Place live order
        order_success = place_neo_order(opt_name, 'BUY', qty)
        if order_success:
            active_position = {
                'ticker': opt_name,
                'entry_price': entry_price,
                'qty': qty,
                'max_price': entry_price,
                'stop_loss': initial_sl,
                'type': 'BUY',
                'entry_time': get_ist_now().strftime("%H:%M:%S")
            }
            
            logger.info(f"🔔 Manual Order Executed: Bought {opt_name} at {entry_price}. Initial SL: {initial_sl}")
            
            # Log Open Position to database
            conn = get_db()
            conn.execute("""
            INSERT INTO trades (ticker, action, entry_price, quantity, exit_reason, timestamp)
            VALUES (?, 'BUY', ?, ?, 'OPEN', ?)
            """, (opt_name, entry_price, qty, get_ist_now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()
            return True, f"Successfully bought {opt_name} at {entry_price}"
        else:
            return False, "Failed to place order via Broker API."
    except Exception as e:
        logger.error(f"Error executing manual option buy: {e}", exc_info=True)
        return False, f"Execution failed: {e}"

def square_off_manual(reason="MANUAL_SQUARE_OFF"):
    global active_position
    if not active_position:
        return False, "No active position to square off."
        
    try:
        ticker = active_position['ticker']
        entry_price = active_position['entry_price']
        qty = active_position['qty']
        exit_price = get_live_price(ticker)
        
        # Place live exit order
        place_neo_order(ticker, 'SELL', qty)
        
        # Calculate P&L
        pnl = round((exit_price - entry_price) * qty, 2)
        logger.info(f"🔴 Manual Exit: Squaring off {ticker}. Entry: {entry_price}, Exit: {exit_price}, P&L: {pnl} (Reason: {reason})")
        
        # Save exit log to database
        conn = get_db()
        conn.execute("""
        UPDATE trades 
        SET exit_price = ?, exit_reason = ?, pnl = ? 
        WHERE ticker = ? AND exit_reason = 'OPEN'
        """, (exit_price, reason, pnl, ticker))
        
        conn.execute("UPDATE settings SET virtual_balance = virtual_balance + ? WHERE id = 1", (pnl,))
        conn.commit()
        conn.close()
        
        active_position = None
        return True, f"Successfully squared off {ticker} at {exit_price} (P&L: {pnl})"
    except Exception as e:
        logger.error(f"Error squaring off position: {e}", exc_info=True)
        return False, f"Square off failed: {e}"

# ----------------- BACKGROUND WORKER LOOP -----------------

def run_trading_bot():
    global active_position, last_real_fetch_time, last_auto_activation_date, mock_stocks, options_feed
    logger.info("Background Options Trading Bot Thread Started.")
    
    while True:
        try:
            conn = get_db()
            settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            conn.close()
            
            if not settings:
                time.sleep(2)
                continue
                
            is_active = settings['is_active']
            trailing_sl_pct = settings['trailing_sl_pct'] / 100.0
            target_pct = settings['target_pct'] / 100.0
            sl_hits_count = settings['sl_hits_count']
            max_daily_sl = settings['max_daily_sl']
            
            # Check market hours
            now = get_ist_now()
            current_time_str = now.strftime("%H:%M")
            day_of_week = now.weekday()  # 0 = Monday
            
            is_market_hours = (day_of_week < 5) and ("09:15" <= current_time_str <= "15:30")
            
            # Auto-activate at market open
            current_date_str = now.strftime("%Y-%m-%d")
            if day_of_week < 5 and "09:15" <= current_time_str <= "15:30" and last_auto_activation_date != current_date_str:
                if is_active == 0:
                    try:
                        conn_act = get_db()
                        conn_act.execute("UPDATE settings SET is_active = 1 WHERE id = 1")
                        conn_act.commit()
                        conn_act.close()
                        is_active = 1
                        logger.info(f"Market Open. Automatically activating Options Trading Console!")
                    except Exception as e:
                        logger.error(f"Failed to auto-activate bot settings: {e}")
                last_auto_activation_date = current_date_str
            
            if is_active == 1:
                # Login session check
                if KOTAK_SDK_AVAILABLE and get_neo_client() is None:
                    login_kotak_neo()
                    
                if not is_market_hours:
                    # Outside market hours: Auto Square Off open trades
                    if active_position:
                        logger.info("Market Closed. Automatically squaring off options position.")
                        square_off_manual("MARKET_CLOSE")
                    time.sleep(10)
                    continue
                
                # Check daily stop-loss limit
                if sl_hits_count >= max_daily_sl:
                    if active_position:
                        logger.warning(f"Daily SL limit reached. Squaring off open options.")
                        square_off_manual("MAX_SL_LIMIT")
                    
                    conn = get_db()
                    conn.execute("UPDATE settings SET is_active = 0 WHERE id = 1")
                    conn.commit()
                    conn.close()
                    logger.error(f"Algo stopped: Daily {max_daily_sl} SL hits reached.")
                    time.sleep(5)
                    continue
                
                # Tick calculations: Update Index Prices
                prev_nifty = mock_stocks["NIFTY50"]["price"]
                prev_bank = mock_stocks["BANKNIFTY"]["price"]
                
                now_epoch = time.time()
                synced = False
                if now_epoch - last_real_fetch_time > 3:
                    real_prices = fetch_real_prices()
                    if real_prices:
                        for idx in ["NIFTY50", "BANKNIFTY"]:
                            new_val = real_prices[idx]
                            mock_stocks[idx]['price'] = new_val
                            if new_val > mock_stocks[idx]['high']: mock_stocks[idx]['high'] = new_val
                            if new_val < mock_stocks[idx]['low']: mock_stocks[idx]['low'] = new_val
                        last_real_fetch_time = now_epoch
                        synced = True
                        
                if not synced:
                    # Micro random walk simulations
                    for idx in ["NIFTY50", "BANKNIFTY"]:
                        price = mock_stocks[idx]['price']
                        change = random.uniform(-0.0001, 0.0001)
                        new_val = round(price * (1 + change), 2)
                        mock_stocks[idx]['price'] = new_val
                        if new_val > mock_stocks[idx]['high']: mock_stocks[idx]['high'] = new_val
                        if new_val < mock_stocks[idx]['low']: mock_stocks[idx]['low'] = new_val
                        
                n_diff = mock_stocks["NIFTY50"]["price"] - prev_nifty
                b_diff = mock_stocks["BANKNIFTY"]["price"] - prev_bank
                
                # Tick premium simulations for fallback feed
                options_feed["NIFTY_CE"] = max(1.0, round(options_feed["NIFTY_CE"] + n_diff * 0.55, 2))
                options_feed["NIFTY_PE"] = max(1.0, round(options_feed["NIFTY_PE"] - n_diff * 0.45, 2))
                options_feed["BANK_CE"] = max(1.0, round(options_feed["BANK_CE"] + b_diff * 0.55, 2))
                options_feed["BANK_PE"] = max(1.0, round(options_feed["BANK_PE"] - b_diff * 0.45, 2))
                
                # Active trade check
                if active_position:
                    ticker = active_position['ticker']
                    curr_price = get_live_price(ticker)
                    entry_price = active_position['entry_price']
                    
                    # Trailing Stop Loss logic
                    if curr_price > active_position['max_price']:
                        active_position['max_price'] = curr_price
                        new_sl = round(curr_price * (1 - trailing_sl_pct), 2)
                        active_position['stop_loss'] = new_sl
                        logger.info(f"Trailed SL UP to {new_sl} for {ticker}")
                        
                    target_price = entry_price * (1 + target_pct)
                    
                    if curr_price >= target_price:
                        square_off_manual("TARGET_HIT")
                    elif curr_price <= active_position['stop_loss']:
                        # Increment daily SL hit count in DB
                        try:
                            conn_sl = get_db()
                            conn_sl.execute("UPDATE settings SET sl_hits_count = sl_hits_count + 1 WHERE id = 1")
                            conn_sl.commit()
                            conn_sl.close()
                        except Exception as e:
                            logger.error(f"Failed to increment daily SL hit count: {e}")
                        square_off_manual("SL_HIT")
            else:
                # Toggled off: ensure positions are squared off
                if active_position:
                    square_off_manual("BOT_STOPPED")
                
        except Exception as e:
            logger.error(f"Error in background option trading loop: {e}", exc_info=True)
            
        time.sleep(2.0)

def start_bot_thread():
    t = threading.Thread(target=run_trading_bot, daemon=True)
    t.start()
    logger.info("Background Options Trading Bot loop initialized.")
    return t
