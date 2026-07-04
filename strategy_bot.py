import logging
import time
import random
import requests
import threading
from datetime import datetime, timezone, timedelta

from db import get_db
from kotak_auth import KOTAK_SDK_AVAILABLE, login_kotak_neo, get_neo_client
from market_data import (
    mock_stocks, options_feed, candle_history, prev_sma_states,
    get_live_price, get_option_name, fetch_real_prices,
    ensure_candle_history, get_recent_volatility, get_momentum_stocks,
    calculate_atr, calculate_supertrend, calculate_rsi_new,
    calculate_rsi, calculate_ema, calculate_macd, get_ist_now
)
from order_manager import place_neo_order

logger = logging.getLogger("TradingBot.Strategy")

# Shared state variables
active_position = None  # None or dict: {'ticker': ..., 'entry_price': ..., 'qty': ..., 'max_price': ..., 'stop_loss': ..., 'type': 'BUY'/'SELL', ...}
active_positions = {}   # dict: {symbol: position_dict}
pending_signals = {}    # dict: {symbol: {'type': 'BUY'/'SELL'}}
bot_running = False
last_candle_minute = None
last_real_fetch_time = 0
last_opening_trade_date = None
last_auto_activation_date = None
sl_hits_count = 0

def get_bot_state():
    return {
        "active_position": active_position,
        "active_positions": active_positions,
        "pending_signals": pending_signals,
        "bot_running": bot_running,
        "sl_hits_count": sl_hits_count,
        "last_opening_trade_date": last_opening_trade_date,
        "last_auto_activation_date": last_auto_activation_date
    }

def increment_sl_hits():
    global sl_hits_count
    try:
        conn = get_db()
        conn.execute("UPDATE settings SET sl_hits_count = sl_hits_count + 1 WHERE id = 1")
        conn.commit()
        conn.close()
        logger.info("Daily SL hits count incremented in database.")
    except Exception as e:
        logger.error(f"Failed to increment SL hits: {e}")

def trigger_entry_signal(index_name, option_type, fast_p, slow_p, strategy_type='SMA_CROSSOVER'):
    global active_position
    
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
        
        # Reversal Logic: If we already have a position in the same index but opposite type, square off first
        if active_position:
            curr_ticker = active_position['ticker']
            if index_name in curr_ticker and option_type not in curr_ticker:
                logger.info(f"Reversal signal! Squaring off existing position in {curr_ticker} before entering {opt_name}")
                exit_price = get_live_price(curr_ticker)
                square_off_position(curr_ticker, "REVERSAL_SIGNAL", exit_price)
            else:
                return  # already in correct position
                
        # Enter new position
        order_success = place_neo_order(opt_name, 'BUY', qty)
        if order_success:
            active_position = {
                'ticker': opt_name,
                'entry_price': entry_price,
                'qty': qty,
                'max_price': entry_price,
                'stop_loss': initial_sl,
                'type': 'BUY',
                'entry_time': get_ist_now().strftime("%H:%M:%S"),
                'strategy': strategy_type
            }
            
            logger.info(f"🔔 Strategy Crossover Signal! Buying Option {opt_name} at {entry_price}. Initial SL: {initial_sl}")
            
            conn = get_db()
            conn.execute("""
            INSERT INTO trades (ticker, action, entry_price, quantity, exit_reason, timestamp, strategy)
            VALUES (?, 'BUY', ?, ?, 'OPEN', ?, ?)
            """, (opt_name, entry_price, qty, get_ist_now().strftime("%Y-%m-%d %H:%M:%S"), strategy_type))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error triggering options entry signal: {e}", exc_info=True)

def square_off_position(ticker, reason, exit_price):
    global active_position
    if not active_position:
        return
        
    try:
        entry_price = active_position['entry_price']
        qty = active_position['qty']
        action_type = active_position['type']
        
        # Place live square off order if online
        place_neo_order(ticker, 'SELL', qty)
        
        # Calculate PnL
        if action_type == 'BUY':
            pnl = round((exit_price - entry_price) * qty, 2)
        else:
            pnl = round((entry_price - exit_price) * qty, 2)
            
        logger.info(f"🔴 Squaring off {action_type} {ticker}. Entry: {entry_price}, Exit: {exit_price}, P&L: {pnl} (Reason: {reason})")
        
        # Update DB logs
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
    except Exception as e:
        logger.error(f"Error squaring off options position {ticker}: {e}", exc_info=True)

def trigger_equity_signal(symbol, action_type, fast_p, slow_p, strategy_type='SMA_CROSSOVER'):
    global active_positions
    
    if len(active_positions) >= 5:
        logger.warning(f"Cannot enter position in {symbol}. Already holding maximum of 5 positions.")
        return
        
    if symbol in active_positions:
        return
        
    try:
        conn = get_db()
        settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        conn.close()
        
        trailing_sl_pct = settings['trailing_sl_pct'] / 100.0
        equity_allocation = settings['equity_allocation'] if ('equity_allocation' in settings.keys() and settings['equity_allocation']) else 10000.0
        
        if symbol in mock_stocks:
            stock_price = mock_stocks[symbol]['price']
        else:
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                import urllib.parse
                symbol_encoded = urllib.parse.quote(f"{symbol}.NS")
                r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1m&range=1d", headers=headers, timeout=3)
                stock_price = r.json()['chart']['result'][0]['meta']['regularMarketPrice']
                mock_stocks[symbol] = {"price": stock_price, "high": stock_price, "low": stock_price}
            except Exception:
                stock_price = 100.0
        
        qty = int(equity_allocation / stock_price)
        if qty <= 0:
            qty = 1
            
        initial_sl = round(stock_price * (1 - trailing_sl_pct) if action_type == 'BUY' else stock_price * (1 + trailing_sl_pct), 2)
        
        order_success = place_neo_order(symbol, action_type, qty, segment='nse_cm')
        if order_success:
            active_positions[symbol] = {
                'ticker': symbol,
                'entry_price': stock_price,
                'qty': qty,
                'max_price': stock_price,
                'min_price': stock_price,
                'stop_loss': initial_sl,
                'type': action_type,
                'entry_time': get_ist_now().strftime("%H:%M:%S"),
                'strategy': strategy_type
            }
            
            logger.info(f"🔔 [EQUITY] Momentum Signal! {action_type} {symbol} Qty {qty} at {stock_price}. Initial SL: {initial_sl}")
            
            conn = get_db()
            conn.execute("""
            INSERT INTO trades (ticker, action, entry_price, quantity, exit_reason, timestamp, strategy)
            VALUES (?, ?, ?, ?, 'OPEN', ?, ?)
            """, (symbol, action_type, stock_price, qty, get_ist_now().strftime("%Y-%m-%d %H:%M:%S"), strategy_type))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error triggering equity signal for {symbol}: {e}", exc_info=True)

def square_off_equity_position(symbol, reason, exit_price):
    global active_positions
    if symbol not in active_positions:
        return
        
    try:
        pos = active_positions[symbol]
        entry_price = pos['entry_price']
        qty = pos['qty']
        action_type = pos['type']
        
        exit_action = 'SELL' if action_type == 'BUY' else 'BUY'
        place_neo_order(symbol, exit_action, qty, segment='nse_cm')
        
        if action_type == 'BUY':
            pnl = round((exit_price - entry_price) * qty, 2)
        else:
            pnl = round((entry_price - exit_price) * qty, 2)
            
        logger.info(f"🔴 Squaring off [EQUITY] {action_type} {symbol}. Entry: {entry_price}, Exit: {exit_price}, P&L: {pnl} (Reason: {reason})")
        
        conn = get_db()
        conn.execute("""
        UPDATE trades 
        SET exit_price = ?, exit_reason = ?, pnl = ? 
        WHERE ticker = ? AND exit_reason = 'OPEN'
        """, (exit_price, reason, pnl, symbol))
        
        conn.execute("UPDATE settings SET virtual_balance = virtual_balance + ? WHERE id = 1", (pnl,))
        conn.commit()
        conn.close()
        
        if symbol in active_positions:
            del active_positions[symbol]
    except Exception as e:
        logger.error(f"Error squaring off equity position {symbol}: {e}", exc_info=True)

def check_watchlist_breakout(ticker, scanner_high, fast_period, slow_period):
    try:
        import urllib.parse
        symbol_ns = f"{ticker}.NS"
        symbol_encoded = urllib.parse.quote(symbol_ns)
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1m&range=1d"
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code == 200:
            res = r.json().get('chart', {}).get('result', [])[0]
            quote = res.get('indicators', {}).get('quote', [{}])[0]
            closes = quote.get('close', [])
            opens = quote.get('open', [])
            
            valid_data = [(o, c) for o, c in zip(opens, closes) if o is not None and c is not None]
            if len(valid_data) >= 4:
                # Rolling 3-minute candle confirmation
                open_3m = valid_data[-4][0]
                close_3m = valid_data[-2][1]
                
                # Check if it meets the breakout condition
                if close_3m > scanner_high and close_3m > open_3m:
                    logger.info(f"🚀 [WATCHLIST BREAKOUT] {ticker} confirmed breakout! 3m Close {close_3m} > High {scanner_high} (Open: {open_3m}).")
                    trigger_equity_signal(ticker, 'BUY', fast_period, slow_period)
                    
                    conn = get_db()
                    conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
                    conn.commit()
                    conn.close()
    except Exception as e:
        logger.warning(f"Error checking breakout for watchlist stock {ticker}: {e}")

# ----------------- CONTROL INTERFACE FUNCTIONS -----------------

def reset_bot_simulation():
    global active_position, active_positions, sl_hits_count, pending_signals
    
    logger.info("Resetting virtual portfolio ledger and daily stop loss logs...")
    
    if active_position:
        ticker = active_position['ticker']
        opt_exit = get_live_price(ticker)
        square_off_position(ticker, "MANUAL_RESET", opt_exit)
        
    for sym in list(active_positions.keys()):
        square_off_equity_position(sym, "MANUAL_RESET", mock_stocks[sym]['price'])
        
    conn = get_db()
    conn.execute("UPDATE settings SET virtual_balance = 100000.0, sl_hits_count = 0, is_active = 0 WHERE id = 1")
    conn.execute("DELETE FROM trades")
    conn.commit()
    conn.close()
    
    sl_hits_count = 0
    active_positions.clear()
    pending_signals.clear()
    logger.info("Ledger reset complete.")

def manual_square_off_position(ticker):
    global active_positions, active_position
    
    if active_position and active_position['ticker'] == ticker:
        opt_exit = get_live_price(ticker)
        square_off_position(ticker, "MANUAL_SQUARE_OFF", opt_exit)
        return True, f"Successfully squared off {ticker}"
        
    elif ticker in active_positions:
        exit_price = mock_stocks[ticker]['price']
        square_off_equity_position(ticker, "MANUAL_SQUARE_OFF", exit_price)
        return True, f"Successfully squared off {ticker}"
        
    return False, f"No active position found for {ticker}"

# ----------------- BACKGROUND WORKER THREAD LOOP -----------------

def run_trading_bot():
    global active_position, active_positions, sl_hits_count, bot_running, last_real_fetch_time, last_candle_minute, last_opening_trade_date, last_auto_activation_date, pending_signals
    logger.info("Background Algorithmic Trading Bot Thread Started.")
    
    while True:
        try:
            # 1. Fetch current status and settings
            conn = get_db()
            cursor = conn.cursor()
            settings = cursor.execute("SELECT * FROM settings WHERE id = 1").fetchone()
            creds = cursor.execute("SELECT * FROM credentials LIMIT 1").fetchone()
            conn.close()
            
            if not settings:
                time.sleep(2)
                continue
                
            is_active = settings['is_active']
            trailing_sl_pct = settings['trailing_sl_pct'] / 100.0
            target_pct = settings['target_pct'] / 100.0
            trade_qty = settings['trade_quantity']
            virtual_balance = settings['virtual_balance']
            sl_hits_count = settings['sl_hits_count']
            fast_period = settings['fast_period']
            slow_period = settings['slow_period']
            trade_mode = settings['trade_mode'] if ('trade_mode' in settings.keys() and settings['trade_mode']) else 'EQUITY'
            equity_allocation = settings['equity_allocation'] if ('equity_allocation' in settings.keys() and settings['equity_allocation']) else 10000.0
            max_daily_sl = settings['max_daily_sl'] if 'max_daily_sl' in settings.keys() else 3
            enable_atr_filter = settings['enable_atr_filter'] if 'enable_atr_filter' in settings.keys() else 0
            min_atr_val = settings['min_atr_val'] if 'min_atr_val' in settings.keys() else 1.5
            trade_duration = settings['trade_duration'] if ('trade_duration' in settings.keys() and settings['trade_duration']) else 'INTRADAY'
            enable_candle_confirm = settings['enable_candle_confirm'] if ('enable_candle_confirm' in settings.keys() and settings['enable_candle_confirm'] is not None) else 1
            
            # Check market hours (Auto-start/stop) - converted to Indian Standard Time (IST)
            now = get_ist_now()
            current_time_str = now.strftime("%H:%M")
            day_of_week = now.weekday()  # 0 = Monday, 6 = Sunday
            
            is_market_hours = (day_of_week < 5) and ("09:15" <= current_time_str <= "15:30")
            
            # Auto-activate bot at market open (09:15 AM) on weekdays (once per day)
            current_date_str = now.strftime("%Y-%m-%d")
            if day_of_week < 5 and "09:15" <= current_time_str <= "15:30" and last_auto_activation_date != current_date_str:
                if is_active == 0:
                    try:
                        conn_act = get_db()
                        conn_act.execute("UPDATE settings SET is_active = 1 WHERE id = 1")
                        conn_act.commit()
                        conn_act.close()
                        is_active = 1
                        logger.info(f"New trading day {current_date_str} market open. Automatically activating the Algo Trading Bot!")
                    except Exception as e:
                        logger.error(f"Failed to auto-activate bot: {e}")
                last_auto_activation_date = current_date_str
            
            if is_active == 1:
                # Attempt to authenticate with Kotak Neo if SDK is available and session is offline
                if KOTAK_SDK_AVAILABLE and get_neo_client() is None:
                    login_kotak_neo()
                    
                if not is_market_hours:
                    # Outside market hours: Auto Square Off and Sleep (only if INTRADAY)
                    if trade_duration == 'INTRADAY':
                        if active_position:
                            logger.info("Market Closed. Automatically squaring off open options positions.")
                            ticker = active_position['ticker']
                            opt_exit = get_live_price(ticker)
                            square_off_position(ticker, "MARKET_CLOSE", opt_exit)
                        for sym in list(active_positions.keys()):
                            logger.info(f"Market Closed. Squaring off open equity position in {sym}.")
                            square_off_equity_position(sym, "MARKET_CLOSE", mock_stocks[sym]['price'])
                        
                        if bot_running:
                            logger.info("Outside market hours. Intraday Bot is sleeping.")
                            bot_running = False
                    else:
                        if bot_running:
                            logger.info("Outside market hours. Swing Bot is sleeping (keeping positions open).")
                            bot_running = False
                    time.sleep(10)
                    continue
                
                # Check Stop Loss hits limit (custom limit)
                if sl_hits_count >= max_daily_sl:
                    if active_position:
                        logger.warning(f"Daily SL hits limit ({max_daily_sl}) reached. Squaring off open options positions.")
                        ticker = active_position['ticker']
                        opt_exit = get_live_price(ticker)
                        square_off_position(ticker, "MAX_SL_LIMIT", opt_exit)
                    for sym in list(active_positions.keys()):
                        logger.warning(f"Daily SL hits limit ({max_daily_sl}) reached. Squaring off open equity position in {sym}.")
                        square_off_equity_position(sym, "MAX_SL_LIMIT", mock_stocks[sym]['price'])
                    
                    if bot_running:
                        logger.error(f"Algo Bot halted for today: Maximum {max_daily_sl} Stop Losses reached.")
                        conn = get_db()
                        conn.execute("UPDATE settings SET is_active = 0 WHERE id = 1")
                        conn.commit()
                        conn.close()
                        bot_running = False
                    time.sleep(5)
                    continue
                
                # Bot is active and within market hours
                if not bot_running:
                    logger.info("Algo Trading Bot Started. Resetting SMA states for immediate entry.")
                    prev_sma_states.clear()
                    prev_sma_states["NIFTY50"] = {"fast": None, "slow": None}
                    prev_sma_states["BANKNIFTY"] = {"fast": None, "slow": None}
                    
                    try:
                        real_prices = fetch_real_prices()
                        if real_prices:
                            for ticker in ["NIFTY50", "BANKNIFTY"]:
                                if ticker in mock_stocks:
                                    mock_stocks[ticker]['price'] = real_prices[ticker]
                                    mock_stocks[ticker]['high'] = real_prices[ticker]
                                    mock_stocks[ticker]['low'] = real_prices[ticker]
                                else:
                                    mock_stocks[ticker] = {
                                        "price": real_prices[ticker],
                                        "high": real_prices[ticker],
                                        "low": real_prices[ticker],
                                        "trend": 0
                                    }
                            logger.info(f"Synchronized mock stocks with real prices on startup: NIFTY50={real_prices['NIFTY50']}, BANKNIFTY={real_prices['BANKNIFTY']}")
                    except Exception as e:
                        logger.warning(f"Could not synchronize starting prices: {e}")
                        
                    bot_running = True
                
                # ---- EQUITY MODE LOGIC ----
                if trade_mode == 'EQUITY' or trade_mode == 'BOTH':
                    radar = get_momentum_stocks()
                    # Pre-populate mock_stocks with current prices from radar to prevent KeyError
                    for item in (radar.get('gainers', []) + radar.get('losers', [])):
                        sym = item.get('symbol')
                        if sym and sym not in mock_stocks:
                            mock_stocks[sym] = {
                                "price": item['price'],
                                "high": item['price'],
                                "low": item['price']
                            }
                    active_symbols = [s['symbol'] for s in radar['gainers']] + [s['symbol'] for s in radar['losers']]
                    
                    for sym in list(active_positions.keys()):
                        if sym not in active_symbols:
                            active_symbols.append(sym)
                            
                    try:
                        conn_wl = get_db()
                        wl_symbols = [row['ticker'] for row in conn_wl.execute("SELECT ticker FROM watchlist").fetchall()]
                        conn_wl.close()
                        for sym in wl_symbols:
                            if sym not in active_symbols:
                                active_symbols.append(sym)
                    except Exception as e:
                        logger.warning(f"Could not query watchlist for active symbols: {e}")
                            
                    # Immediate Open Entry (once per day)
                    current_date_str = now.strftime("%Y-%m-%d")
                    if "09:15" <= current_time_str <= "15:30" and last_opening_trade_date != current_date_str:
                        try:
                            conn_reset = get_db()
                            conn_reset.execute("UPDATE settings SET sl_hits_count = 0 WHERE id = 1")
                            conn_reset.commit()
                            conn_reset.close()
                            sl_hits_count = 0
                            logger.info(f"New trading day detected ({current_date_str}). Resetting daily Stop Loss hits count to 0 in database.")
                        except Exception as e:
                            logger.error(f"Failed to reset daily SL hits count on new day: {e}")

                        nifty_change_pct = 0.0
                        try:
                            headers = {'User-Agent': 'Mozilla/5.0'}
                            r = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=1m&range=1d', headers=headers, timeout=3)
                            meta = r.json()['chart']['result'][0]['meta']
                            nifty_price = meta.get('regularMarketPrice')
                            prev_close = meta.get('chartPreviousClose')
                            if nifty_price and prev_close:
                                nifty_change_pct = ((nifty_price - prev_close) / prev_close) * 100
                        except Exception as e:
                            logger.warning(f"Failed to fetch Nifty 50 opening direction: {e}")
                            if 'NIFTY50' in mock_stocks:
                                nifty_change_pct = mock_stocks['NIFTY50']['price'] - nifty_start
                        
                        logger.info(f"Checking Market Open Entry: Nifty 50 Change = {nifty_change_pct:.2f}%")
                        
                        if nifty_change_pct > 0:
                            if len(radar['gainers']) > 0:
                                symbol = radar['gainers'][0]['symbol']
                                if symbol not in active_positions and len(active_positions) < 5:
                                    logger.info(f"Market Open BUY: Nifty is up ({nifty_change_pct:.2f}%). Entering top gainer: {symbol}")
                                    trigger_equity_signal(symbol, 'BUY', fast_period, slow_period)
                                    last_opening_trade_date = current_date_str
                        else:
                            if len(radar['losers']) > 0:
                                symbol = radar['losers'][0]['symbol']
                                if symbol not in active_positions and len(active_positions) < 5:
                                    logger.info(f"Market Open SELL: Nifty is down ({nifty_change_pct:.2f}%). Entering top loser: {symbol}")
                                    trigger_equity_signal(symbol, 'SELL', fast_period, slow_period)
                                    last_opening_trade_date = current_date_str
                    
                    # Fetch latest prices for active/position stocks
                    now_epoch = time.time()
                    synced_real = False
                    if now_epoch - last_real_fetch_time > 3:
                        try:
                            from concurrent.futures import ThreadPoolExecutor
                            import urllib.parse
                            def fetch_price(sym):
                                symbol_ns = f"{sym}.NS"
                                symbol_encoded = urllib.parse.quote(symbol_ns)
                                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
                                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1m&range=1d"
                                try:
                                    r = requests.get(url, headers=headers, timeout=3)
                                    if r.status_code == 200:
                                        res = r.json().get('chart', {}).get('result', [])[0]
                                        meta = res.get('meta', {})
                                        price = meta.get('regularMarketPrice')
                                        if price:
                                            return sym, price
                                except Exception:
                                    pass
                                return None

                            with ThreadPoolExecutor(max_workers=max(6, len(active_symbols))) as executor:
                                results = list(executor.map(fetch_price, active_symbols))
                                
                            for r in results:
                                if r:
                                    sym, price = r
                                    if sym in mock_stocks:
                                        mock_stocks[sym]['price'] = round(price, 2)
                                    else:
                                        mock_stocks[sym] = {"price": round(price, 2), "high": round(price, 2), "low": round(price, 2)}
                            last_real_fetch_time = now_epoch
                            synced_real = True
                        except Exception as e:
                            logger.warning(f"Could not fetch live stock prices from Yahoo: {e}")
                            
                    if not synced_real:
                        for sym in active_symbols:
                            if sym not in mock_stocks:
                                mock_stocks[sym] = {"price": 100.0, "high": 100.0, "low": 100.0}
                            price = mock_stocks[sym]['price']
                            change_pct = random.uniform(-0.0001, 0.0001)
                            new_price = round(price * (1 + change_pct), 2)
                            mock_stocks[sym]['price'] = new_price
                            if new_price > mock_stocks[sym].get('high', new_price):
                                mock_stocks[sym]['high'] = new_price
                            if new_price < mock_stocks[sym].get('low', new_price):
                                mock_stocks[sym]['low'] = new_price
                                
                    current_minute = now.minute
                    candle_closed = False
                    if last_candle_minute is not None and current_minute != last_candle_minute:
                        candle_closed = True
                        for sym in active_symbols:
                            if sym in mock_stocks:
                                if sym not in candle_history:
                                    candle_history[sym] = []
                                o_val = mock_stocks[sym].get('open', mock_stocks[sym]['price'])
                                h_val = mock_stocks[sym].get('high', mock_stocks[sym]['price'])
                                l_val = mock_stocks[sym].get('low', mock_stocks[sym]['price'])
                                c_val = mock_stocks[sym]['price']
                                candle_history[sym].append({
                                    'open': o_val,
                                    'high': h_val,
                                    'low': l_val,
                                    'close': c_val
                                })
                                candle_history[sym] = candle_history[sym][-100:]
                                mock_stocks[sym]['open'] = c_val
                                mock_stocks[sym]['high'] = c_val
                                mock_stocks[sym]['low'] = c_val
                        logger.info(f"1-Minute candle closed for Equity stocks. History updated.")
                    last_candle_minute = current_minute
                    
                    if candle_closed:
                        strategy_type = settings['strategy_type'] if 'strategy_type' in settings.keys() else 'SMA_CROSSOVER'
                        st_period = settings['st_period'] if 'st_period' in settings.keys() else 10
                        st_multiplier = settings['st_multiplier'] if 'st_multiplier' in settings.keys() else 3.0
                        rsi_period = settings['rsi_period'] if 'rsi_period' in settings.keys() else 14
                        rsi_overbought = settings['rsi_overbought'] if 'rsi_overbought' in settings.keys() else 70.0
                        rsi_oversold = settings['rsi_oversold'] if 'rsi_oversold' in settings.keys() else 30.0
                        macd_fast = settings['macd_fast'] if 'macd_fast' in settings.keys() else 12
                        macd_slow = settings['macd_slow'] if 'macd_slow' in settings.keys() else 26
                        macd_signal = settings['macd_signal'] if 'macd_signal' in settings.keys() else 9

                        for sym in active_symbols:
                            ensure_candle_history(sym)
                            candles = candle_history.get(sym, [])
                            
                            if strategy_type == 'SMA_CROSSOVER':
                                if not candles or len(candles) < slow_period + 1:
                                    continue
                                closes = [c['close'] for c in candles]
                                    
                                fast_sma_t = sum(closes[-fast_period:]) / fast_period
                                slow_sma_t = sum(closes[-slow_period:]) / slow_period
                                fast_sma_prev = sum(closes[-fast_period-1:-1]) / fast_period
                                slow_sma_prev = sum(closes[-slow_period-1:-1]) / slow_period
                                
                                is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                is_loser = sym in [s['symbol'] for s in radar['losers']]
                                
                                # A. Check for pending signal confirmation
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    if pending['type'] == 'BUY':
                                        if closes[-1] > closes[-2]:
                                            logger.info(f"✅ [EQUITY] Bullish crossover confirmed on {sym}: candle closed green ({closes[-1]} > {closes[-2]}). Entering trade.")
                                            trigger_equity_signal(sym, "BUY", fast_period, slow_period)
                                        else:
                                            logger.info(f"❌ [EQUITY] Bullish crossover confirmation failed on {sym}: candle closed bearish or flat ({closes[-1]} <= {closes[-2]}). Signal cancelled.")
                                    elif pending['type'] == 'SELL':
                                        if closes[-1] < closes[-2]:
                                            logger.info(f"✅ [EQUITY] Bearish crossover confirmed on {sym}: candle closed red ({closes[-1]} < {closes[-2]}). Entering trade.")
                                            trigger_equity_signal(sym, "SELL", fast_period, slow_period)
                                        else:
                                            logger.info(f"❌ [EQUITY] Bearish crossover confirmation failed on {sym}: candle closed bearish or flat ({closes[-1]} >= {closes[-2]}). Signal cancelled.")
                                    pending_signals[sym] = None
                                    
                                # B. Check for new crossover signals
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        if fast_sma_prev <= slow_sma_prev and fast_sma_t > slow_sma_t:
                                            if is_gainer:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Bullish crossover on {sym} skipped: Volatility ({get_recent_volatility(sym)}) below threshold.")
                                                else:
                                                    if enable_candle_confirm == 1:
                                                        logger.info(f"🔔 [EQUITY] Bullish Crossover detected on {sym}. Waiting for next candle confirmation...")
                                                        pending_signals[sym] = {'type': 'BUY'}
                                                    else:
                                                        logger.info(f"🚀 [EQUITY] Bullish Crossover detected on {sym} (No confirmation mode). Entering BUY.")
                                                        trigger_equity_signal(sym, "BUY", fast_period, slow_period)
                                        elif fast_sma_prev >= slow_sma_prev and fast_sma_t < slow_sma_t:
                                            if is_loser:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Bearish crossover on {sym} skipped: Volatility ({get_recent_volatility(sym)}) below threshold.")
                                                else:
                                                    if enable_candle_confirm == 1:
                                                        logger.info(f"🔔 [EQUITY] Bearish Crossover detected on {sym}. Waiting for next candle confirmation...")
                                                        pending_signals[sym] = {'type': 'SELL'}
                                                    else:
                                                        logger.info(f"⚠️ [EQUITY] Bearish Crossover detected on {sym} (No confirmation mode). Entering SELL.")
                                                        trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SMA_CROSSOVER')
                            
                            elif strategy_type == 'SUPERTREND_RSI_MACD':
                                if not candles or len(candles) < max(40, macd_slow + 2):
                                    continue
                                
                                st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                rsi_vals = calculate_rsi_new(candles, rsi_period)
                                macd_vals, signal_vals, hist_vals = calculate_macd(candles, macd_fast, macd_slow, macd_signal)
                                
                                trend_curr = trend_vals[-1]
                                trend_prev = trend_vals[-2]
                                rsi_curr = rsi_vals[-1]
                                hist_curr = hist_vals[-1]
                                hist_prev = hist_vals[-2]
                                
                                is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                is_loser = sym in [s['symbol'] for s in radar['losers']]
                                
                                # A. Check for pending confirmation
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    last_close_val = candles[-1]['close']
                                    prev_close_val = candles[-2]['close']
                                    if pending['type'] == 'BUY':
                                        if last_close_val > prev_close_val:
                                            logger.info(f"✅ [EQUITY] Supertrend+RSI+MACD BUY confirmed on {sym}: candle closed green ({last_close_val} > {prev_close_val}). Entering BUY.")
                                            trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        else:
                                            logger.info(f"❌ [EQUITY] Supertrend+RSI+MACD BUY confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    elif pending['type'] == 'SELL':
                                        if last_close_val < prev_close_val:
                                            logger.info(f"✅ [EQUITY] Supertrend+RSI+MACD SELL confirmed on {sym}: candle closed red ({last_close_val} < {prev_close_val}). Entering SELL.")
                                            trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        else:
                                            logger.info(f"❌ [EQUITY] Supertrend+RSI+MACD SELL confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    pending_signals[sym] = None
                                    
                                # B. Check for new signals
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        is_buy_trigger = (trend_prev == -1 and trend_curr == 1) or (hist_prev <= 0 and hist_curr > 0)
                                        is_buy_confirmed = (trend_curr == 1) and (hist_curr > 0) and (rsi_curr > rsi_oversold)
                                        
                                        if is_buy_trigger and is_buy_confirmed and is_gainer:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] Supertrend+RSI+MACD BUY on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] Supertrend+RSI+MACD BUY Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'BUY'}
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Supertrend+RSI+MACD BUY Crossover on {sym} (No confirmation mode). Entering BUY.")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                                    
                                        is_sell_trigger = (trend_prev == 1 and trend_curr == -1) or (hist_prev >= 0 and hist_curr < 0)
                                        is_sell_confirmed = (trend_curr == -1) and (hist_curr < 0) and (rsi_curr < rsi_overbought)
                                        
                                        if is_sell_trigger and is_sell_confirmed and is_loser:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] Supertrend+RSI+MACD SELL on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] Supertrend+RSI+MACD SELL Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'SELL'}
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Supertrend+RSI+MACD SELL Crossover on {sym} (No confirmation mode). Entering SELL.")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')

                            elif strategy_type == 'RSI_ONLY':
                                if not candles or len(candles) < rsi_period + 2:
                                    continue
                                    
                                rsi_vals = calculate_rsi_new(candles, rsi_period)
                                rsi_curr = rsi_vals[-1]
                                
                                # Use try/except for index errors on prev RSI
                                try:
                                    rsi_prev = rsi_vals[-2]
                                except IndexError:
                                    rsi_prev = rsi_curr
                                
                                is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                is_loser = sym in [s['symbol'] for s in radar['losers']]
                                
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    last_close_val = candles[-1]['close']
                                    prev_close_val = candles[-2]['close']
                                    if pending['type'] == 'BUY':
                                        if last_close_val > prev_close_val:
                                            logger.info(f"✅ [EQUITY] RSI Only BUY confirmed on {sym}: candle closed green ({last_close_val} > {prev_close_val}). Entering BUY.")
                                            trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='RSI_ONLY')
                                        else:
                                            logger.info(f"❌ [EQUITY] RSI Only BUY confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    elif pending['type'] == 'SELL':
                                        if last_close_val < prev_close_val:
                                            logger.info(f"✅ [EQUITY] RSI Only SELL confirmed on {sym}: candle closed red ({last_close_val} < {prev_close_val}). Entering SELL.")
                                            trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='RSI_ONLY')
                                        else:
                                            logger.info(f"❌ [EQUITY] RSI Only SELL confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    pending_signals[sym] = None
                                    
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        is_buy_trigger = (rsi_prev <= rsi_oversold and rsi_curr > rsi_oversold)
                                        if is_buy_trigger and is_gainer:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] RSI Only BUY on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] RSI Only BUY Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'BUY'}
                                                else:
                                                    logger.info(f"🚀 [EQUITY] RSI Only BUY Crossover on {sym} (No confirmation mode). Entering BUY.")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='RSI_ONLY')
                                                    
                                        is_sell_trigger = (rsi_prev >= rsi_overbought and rsi_curr < rsi_overbought)
                                        if is_sell_trigger and is_loser:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] RSI Only SELL on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] RSI Only SELL Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'SELL'}
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] RSI Only SELL Crossover on {sym} (No confirmation mode). Entering SELL.")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='RSI_ONLY')

                            elif strategy_type == 'MACD_ONLY':
                                if not candles or len(candles) < macd_slow + 2:
                                    continue
                                    
                                macd_vals, signal_vals, hist_vals = calculate_macd(candles, macd_fast, macd_slow, macd_signal)
                                hist_curr = hist_vals[-1]
                                
                                try:
                                    hist_prev = hist_vals[-2]
                                except IndexError:
                                    hist_prev = hist_curr
                                
                                is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                is_loser = sym in [s['symbol'] for s in radar['losers']]
                                
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    last_close_val = candles[-1]['close']
                                    prev_close_val = candles[-2]['close']
                                    if pending['type'] == 'BUY':
                                        if last_close_val > prev_close_val:
                                            logger.info(f"✅ [EQUITY] MACD Only BUY confirmed on {sym}: candle closed green ({last_close_val} > {prev_close_val}). Entering BUY.")
                                            trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='MACD_ONLY')
                                        else:
                                            logger.info(f"❌ [EQUITY] MACD Only BUY confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    elif pending['type'] == 'SELL':
                                        if last_close_val < prev_close_val:
                                            logger.info(f"✅ [EQUITY] MACD Only SELL confirmed on {sym}: candle closed red ({last_close_val} < {prev_close_val}). Entering SELL.")
                                            trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='MACD_ONLY')
                                        else:
                                            logger.info(f"❌ [EQUITY] MACD Only SELL confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    pending_signals[sym] = None
                                    
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        is_buy_trigger = (hist_prev <= 0 and hist_curr > 0)
                                        if is_buy_trigger and is_gainer:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] MACD Only BUY on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] MACD Only BUY Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'BUY'}
                                                else:
                                                    logger.info(f"🚀 [EQUITY] MACD Only BUY Crossover on {sym} (No confirmation mode). Entering BUY.")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='MACD_ONLY')
                                                    
                                        is_sell_trigger = (hist_prev >= 0 and hist_curr < 0)
                                        if is_sell_trigger and is_loser:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] MACD Only SELL on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] MACD Only SELL Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'SELL'}
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] MACD Only SELL Crossover on {sym} (No confirmation mode). Entering SELL.")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='MACD_ONLY')

                            elif strategy_type == 'SUPERTREND_ONLY':
                                if not candles or len(candles) < 20:
                                    continue
                                
                                st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                trend_curr = trend_vals[-1]
                                trend_prev = trend_vals[-2]
                                
                                is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                is_loser = sym in [s['symbol'] for s in radar['losers']]
                                
                                # A. Check pending signals
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    last_close_val = candles[-1]['close']
                                    prev_close_val = candles[-2]['close']
                                    if pending['type'] == 'BUY':
                                        if last_close_val > prev_close_val:
                                            logger.info(f"✅ [EQUITY] Supertrend Only BUY confirmed on {sym}: candle closed green ({last_close_val} > {prev_close_val}). Entering BUY.")
                                            trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        else:
                                            logger.info(f"❌ [EQUITY] Supertrend Only BUY confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    elif pending['type'] == 'SELL':
                                        if last_close_val < prev_close_val:
                                            logger.info(f"✅ [EQUITY] Supertrend Only SELL confirmed on {sym}: candle closed red ({last_close_val} < {prev_close_val}). Entering SELL.")
                                            trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        else:
                                            logger.info(f"❌ [EQUITY] Supertrend Only SELL confirmation failed on {sym}: candle closed bearish or flat. Signal cancelled.")
                                    pending_signals[sym] = None
                                    
                                # B. Check new crossover signals
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        is_buy_trigger = (trend_prev == -1 and trend_curr == 1)
                                        if is_buy_trigger and is_gainer:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] Supertrend Only BUY on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] Supertrend Only BUY Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'BUY'}
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Supertrend Only BUY Crossover on {sym} (No confirmation mode). Entering BUY.")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                                    
                                        is_sell_trigger = (trend_prev == 1 and trend_curr == -1)
                                        if is_sell_trigger and is_loser:
                                            if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                logger.info(f"⏳ [EQUITY] Supertrend Only SELL on {sym} skipped: Volatility below threshold.")
                                            else:
                                                if enable_candle_confirm == 1:
                                                    logger.info(f"🔔 [EQUITY] Supertrend Only SELL Crossover on {sym}. Waiting for confirmation...")
                                                    pending_signals[sym] = {'type': 'SELL'}
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Supertrend Only SELL Crossover on {sym} (No confirmation mode). Entering SELL.")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                                    
                        # 1b. Check Watchlist Breakouts (Rolling 3-Minute Green Candle Confirmation)
                        try:
                            conn_wl = get_db()
                            wl_items = conn_wl.execute("SELECT ticker, scanner_high FROM watchlist").fetchall()
                            conn_wl.close()
                            
                            if wl_items:
                                from concurrent.futures import ThreadPoolExecutor
                                def run_check(item):
                                    check_watchlist_breakout(item['ticker'], item['scanner_high'], fast_period, slow_period)
                                
                                with ThreadPoolExecutor(max_workers=max(3, len(wl_items))) as executor:
                                    list(executor.map(run_check, wl_items))
                        except Exception as e:
                            logger.error(f"Error in watchlist breakout scanning: {e}")
                                                     
                    # 2. Live ticks monitoring (Exits, Startup Entry, Trailing SL)
                    strategy_type = settings['strategy_type'] if 'strategy_type' in settings.keys() else 'SMA_CROSSOVER'
                    st_period = settings['st_period'] if 'st_period' in settings.keys() else 10
                    st_multiplier = settings['st_multiplier'] if 'st_multiplier' in settings.keys() else 3.0
                    rsi_period = settings['rsi_period'] if 'rsi_period' in settings.keys() else 14
                    rsi_overbought = settings['rsi_overbought'] if 'rsi_overbought' in settings.keys() else 70.0
                    rsi_oversold = settings['rsi_oversold'] if 'rsi_oversold' in settings.keys() else 30.0
                    macd_fast = settings['macd_fast'] if 'macd_fast' in settings.keys() else 12
                    macd_slow = settings['macd_slow'] if 'macd_slow' in settings.keys() else 26
                    macd_signal = settings['macd_signal'] if 'macd_signal' in settings.keys() else 9

                    for sym in active_symbols:
                        ensure_candle_history(sym)
                        candles = candle_history.get(sym, [])
                        
                        if strategy_type == 'SMA_CROSSOVER':
                            closes = [c['close'] for c in candles] + [mock_stocks[sym]['price']]
                            if len(closes) < slow_period:
                                continue
                                
                            fast_sma = sum(closes[-fast_period:]) / fast_period
                            slow_sma = sum(closes[-slow_period:]) / slow_period
                            
                            if sym not in prev_sma_states:
                                prev_sma_states[sym] = {"fast": None, "slow": None}
                            prev_state = prev_sma_states[sym]
                            
                            # A. Check for Reverse Crossover Exit (immediate)
                            if prev_state["fast"] is not None and prev_state["slow"] is not None:
                                if sym in active_positions:
                                    pos = active_positions[sym]
                                    if pos['type'] == 'BUY' and prev_state["fast"] >= prev_state["slow"] and fast_sma < slow_sma:
                                        logger.info(f"🔄 [EQUITY] Reverse crossover (Death Cross) on active BUY position {sym}! Squaring off.")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                                    elif pos['type'] == 'SELL' and prev_state["fast"] <= prev_state["slow"] and fast_sma > slow_sma:
                                        logger.info(f"🔄 [EQUITY] Reverse crossover (Golden Cross) on active SELL position {sym}! Squaring off.")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                            else:
                                if sym not in active_positions and len(active_positions) < 5:
                                    closes_only = [c['close'] for c in candles]
                                    if len(closes_only) >= slow_period + 1:
                                        last_fast_sma = sum(closes_only[-fast_period:]) / fast_period
                                        last_slow_sma = sum(closes_only[-slow_period:]) / slow_period
                                        
                                        is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                        is_loser = sym in [s['symbol'] for s in radar['losers']]
                                        
                                        if last_fast_sma > last_slow_sma and is_gainer:
                                            if enable_candle_confirm == 0 or closes_only[-1] > closes_only[-2]:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate entry on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate Golden Cross Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period)
                                        elif last_fast_sma < last_slow_sma and is_loser:
                                            if enable_candle_confirm == 0 or closes_only[-1] < closes_only[-2]:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate entry on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Immediate Death Cross Entry on {sym}!")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period)
                                                    
                            prev_sma_states[sym]["fast"] = fast_sma
                            prev_sma_states[sym]["slow"] = slow_sma
                            
                        elif strategy_type == 'SUPERTREND_RSI_MACD':
                            if sym in active_positions:
                                live_candles = list(candles)
                                live_candles.append({
                                    'open': mock_stocks[sym].get('open', mock_stocks[sym]['price']),
                                    'high': mock_stocks[sym].get('high', mock_stocks[sym]['price']),
                                    'low': mock_stocks[sym].get('low', mock_stocks[sym]['price']),
                                    'close': mock_stocks[sym]['price']
                                })
                                
                                if len(live_candles) >= max(40, macd_slow + 2):
                                    st_vals, trend_vals = calculate_supertrend(live_candles, st_period, st_multiplier)
                                    macd_vals, signal_vals, hist_vals = calculate_macd(live_candles, macd_fast, macd_slow, macd_signal)
                                    
                                    trend_curr = trend_vals[-1]
                                    hist_curr = hist_vals[-1]
                                    
                                    pos = active_positions[sym]
                                    if pos['type'] == 'BUY' and (trend_curr == -1 or hist_curr < 0):
                                        logger.info(f"🔄 [EQUITY] Supertrend+RSI+MACD Exit on active BUY {sym}! (Trend: {trend_curr}, Hist: {hist_curr})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                                    elif pos['type'] == 'SELL' and (trend_curr == 1 or hist_curr > 0):
                                        logger.info(f"🔄 [EQUITY] Supertrend+RSI+MACD Exit on active SELL {sym}! (Trend: {trend_curr}, Hist: {hist_curr})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                            else:
                                if sym not in active_positions and len(active_positions) < 5:
                                    if len(candles) >= max(40, macd_slow + 2):
                                        st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                        rsi_vals = calculate_rsi_new(candles, rsi_period)
                                        macd_vals, signal_vals, hist_vals = calculate_macd(candles, macd_fast, macd_slow, macd_signal)
                                        
                                        trend_curr = trend_vals[-1]
                                        rsi_curr = rsi_vals[-1]
                                        hist_curr = hist_vals[-1]
                                        
                                        is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                        is_loser = sym in [s['symbol'] for s in radar['losers']]
                                        
                                        if trend_curr == 1 and hist_curr > 0 and rsi_curr > rsi_oversold and is_gainer:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend+RSI+MACD BUY on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate Supertrend+RSI+MACD BUY Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        elif trend_curr == -1 and hist_curr < 0 and rsi_curr < rsi_overbought and is_loser:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend+RSI+MACD SELL on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Immediate Supertrend+RSI+MACD SELL Entry on {sym}!")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')

                        elif strategy_type == 'RSI_ONLY':
                            if sym in active_positions:
                                live_candles = list(candles)
                                live_candles.append({
                                    'open': mock_stocks[sym].get('open', mock_stocks[sym]['price']),
                                    'high': mock_stocks[sym].get('high', mock_stocks[sym]['price']),
                                    'low': mock_stocks[sym].get('low', mock_stocks[sym]['price']),
                                    'close': mock_stocks[sym]['price']
                                })
                                
                                if len(live_candles) >= rsi_period + 2:
                                    rsi_vals = calculate_rsi_new(live_candles, rsi_period)
                                    rsi_curr = rsi_vals[-1]
                                    
                                    pos = active_positions[sym]
                                    if pos['type'] == 'BUY' and rsi_curr >= rsi_overbought:
                                        logger.info(f"🔄 [EQUITY] RSI Overbought Exit on active BUY {sym}! (RSI: {rsi_curr:.1f})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                                    elif pos['type'] == 'SELL' and rsi_curr <= rsi_oversold:
                                        logger.info(f"🔄 [EQUITY] RSI Oversold Exit on active SELL {sym}! (RSI: {rsi_curr:.1f})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                            else:
                                if sym not in active_positions and len(active_positions) < 5:
                                    if len(candles) >= rsi_period + 2:
                                        rsi_vals = calculate_rsi_new(candles, rsi_period)
                                        rsi_curr = rsi_vals[-1]
                                        try:
                                            rsi_prev = rsi_vals[-2]
                                        except IndexError:
                                            rsi_prev = rsi_curr
                                            
                                        is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                        is_loser = sym in [s['symbol'] for s in radar['losers']]
                                        
                                        if rsi_prev <= rsi_oversold and rsi_curr > rsi_oversold and is_gainer:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate RSI BUY on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate RSI BUY Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='RSI_ONLY')
                                        elif rsi_prev >= rsi_overbought and rsi_curr < rsi_overbought and is_loser:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate RSI SELL on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Immediate RSI SELL Entry on {sym}!")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='RSI_ONLY')

                        elif strategy_type == 'MACD_ONLY':
                            if sym in active_positions:
                                live_candles = list(candles)
                                live_candles.append({
                                    'open': mock_stocks[sym].get('open', mock_stocks[sym]['price']),
                                    'high': mock_stocks[sym].get('high', mock_stocks[sym]['price']),
                                    'low': mock_stocks[sym].get('low', mock_stocks[sym]['price']),
                                    'close': mock_stocks[sym]['price']
                                })
                                
                                if len(live_candles) >= macd_slow + 2:
                                    macd_vals, signal_vals, hist_vals = calculate_macd(live_candles, macd_fast, macd_slow, macd_signal)
                                    hist_curr = hist_vals[-1]
                                    
                                    pos = active_positions[sym]
                                    if pos['type'] == 'BUY' and hist_curr < 0:
                                        logger.info(f"🔄 [EQUITY] MACD Exit on active BUY {sym}! (Hist: {hist_curr})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                                    elif pos['type'] == 'SELL' and hist_curr > 0:
                                        logger.info(f"🔄 [EQUITY] MACD Exit on active SELL {sym}! (Hist: {hist_curr})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                            else:
                                if sym not in active_positions and len(active_positions) < 5:
                                    if len(candles) >= macd_slow + 2:
                                        macd_vals, signal_vals, hist_vals = calculate_macd(candles, macd_fast, macd_slow, macd_signal)
                                        hist_curr = hist_vals[-1]
                                        try:
                                            hist_prev = hist_vals[-2]
                                        except IndexError:
                                            hist_prev = hist_curr
                                            
                                        is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                        is_loser = sym in [s['symbol'] for s in radar['losers']]
                                        
                                        if hist_prev <= 0 and hist_curr > 0 and is_gainer:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate MACD BUY on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate MACD BUY Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='MACD_ONLY')
                                        elif hist_prev >= 0 and hist_curr < 0 and is_loser:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate MACD SELL on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Immediate MACD SELL Entry on {sym}!")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='MACD_ONLY')

                        elif strategy_type == 'SUPERTREND_ONLY':
                            if sym in active_positions:
                                live_candles = list(candles)
                                live_candles.append({
                                    'open': mock_stocks[sym].get('open', mock_stocks[sym]['price']),
                                    'high': mock_stocks[sym].get('high', mock_stocks[sym]['price']),
                                    'low': mock_stocks[sym].get('low', mock_stocks[sym]['price']),
                                    'close': mock_stocks[sym]['price']
                                })
                                
                                if len(live_candles) >= 20:
                                    st_vals, trend_vals = calculate_supertrend(live_candles, st_period, st_multiplier)
                                    trend_curr = trend_vals[-1]
                                    
                                    pos = active_positions[sym]
                                    if pos['type'] == 'BUY' and trend_curr == -1:
                                        logger.info(f"🔄 [EQUITY] Supertrend Only Exit on active BUY {sym}! (Trend: {trend_curr})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                                    elif pos['type'] == 'SELL' and trend_curr == 1:
                                        logger.info(f"🔄 [EQUITY] Supertrend Only Exit on active SELL {sym}! (Trend: {trend_curr})")
                                        square_off_equity_position(sym, "REVERSE_CROSSOVER", mock_stocks[sym]['price'])
                            else:
                                if sym not in active_positions and len(active_positions) < 5:
                                    if len(candles) >= 20:
                                        st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                        trend_curr = trend_vals[-1]
                                        
                                        is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                        is_loser = sym in [s['symbol'] for s in radar['losers']]
                                        
                                        if trend_curr == 1 and is_gainer:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend Only BUY on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate Supertrend Only BUY Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        elif trend_curr == -1 and is_loser:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend Only SELL on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Immediate Supertrend Only SELL Entry on {sym}!")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                        
                    # B. Trailing SL & Target Checks for Active Equity Positions
                    for sym in list(active_positions.keys()):
                        pos = active_positions[sym]
                        curr_price = mock_stocks[sym]['price']
                        entry_price = pos['entry_price']
                        target_price = entry_price * (1 + target_pct) if pos['type'] == 'BUY' else entry_price * (1 - target_pct)
                        
                        if pos['type'] == 'BUY':
                            if curr_price > pos['max_price']:
                                pos['max_price'] = curr_price
                                new_sl = round(curr_price * (1 - trailing_sl_pct), 2)
                                pos['stop_loss'] = new_sl
                                logger.info(f"[EQUITY] Trailed SL UP to {new_sl} for {sym}")
                            
                            if curr_price >= target_price:
                                square_off_equity_position(sym, "TARGET_HIT", curr_price)
                            elif curr_price <= pos['stop_loss']:
                                increment_sl_hits()
                                square_off_equity_position(sym, "SL_HIT", curr_price)
                        else:  # SHORT
                            if curr_price < pos['min_price']:
                                pos['min_price'] = curr_price
                                new_sl = round(curr_price * (1 + trailing_sl_pct), 2)
                                pos['stop_loss'] = new_sl
                                logger.info(f"[EQUITY] Trailed SL DOWN to {new_sl} for {sym}")
                            
                            if curr_price <= target_price:
                                square_off_equity_position(sym, "TARGET_HIT", curr_price)
                            elif curr_price >= pos['stop_loss']:
                                increment_sl_hits()
                                square_off_equity_position(sym, "SL_HIT", curr_price)
                                
                # ---- OPTIONS MODE LOGIC ----
                if trade_mode == 'OPTIONS' or trade_mode == 'BOTH':
                    prev_nifty = mock_stocks["NIFTY50"]["price"]
                    prev_bank = mock_stocks["BANKNIFTY"]["price"]
                    
                    now_epoch = time.time()
                    synced_real = False
                    if now_epoch - last_real_fetch_time > 3:
                        real_prices = fetch_real_prices()
                        if real_prices:
                            for ticker in ["NIFTY50", "BANKNIFTY"]:
                                new_price = real_prices[ticker]
                                mock_stocks[ticker]['price'] = new_price
                                if new_price > mock_stocks[ticker]['high']:
                                    mock_stocks[ticker]['high'] = new_price
                                if new_price < mock_stocks[ticker]['low']:
                                    mock_stocks[ticker]['low'] = new_price
                            last_real_fetch_time = now_epoch
                            synced_real = True
                            
                    if not synced_real:
                        for ticker in ["NIFTY50", "BANKNIFTY"]:
                            price = mock_stocks[ticker]['price']
                            change_pct = random.uniform(-0.0002, 0.0002)
                            new_price = round(price * (1 + change_pct), 2)
                            mock_stocks[ticker]['price'] = new_price
                            if new_price > mock_stocks[ticker]['high']:
                                mock_stocks[ticker]['high'] = new_price
                            if new_price < mock_stocks[ticker]['low']:
                                mock_stocks[ticker]['low'] = new_price
                                
                    n_diff = mock_stocks["NIFTY50"]["price"] - prev_nifty
                    b_diff = mock_stocks["BANKNIFTY"]["price"] - prev_bank
                    
                    options_feed["NIFTY_CE"] = max(1.0, round(options_feed["NIFTY_CE"] + n_diff * 0.55, 2))
                    options_feed["NIFTY_PE"] = max(1.0, round(options_feed["NIFTY_PE"] - n_diff * 0.45, 2))
                    options_feed["BANK_CE"] = max(1.0, round(options_feed["BANK_CE"] + b_diff * 0.55, 2))
                    options_feed["BANK_PE"] = max(1.0, round(options_feed["BANK_PE"] - b_diff * 0.45, 2))
                    
                    current_minute = now.minute
                    candle_closed = False
                    if last_candle_minute is not None and current_minute != last_candle_minute:
                        candle_closed = True
                        for ticker in ["NIFTY50", "BANKNIFTY"]:
                            if ticker not in candle_history:
                                candle_history[ticker] = []
                            o_val = mock_stocks[ticker].get('open', mock_stocks[ticker]['price'])
                            h_val = mock_stocks[ticker].get('high', mock_stocks[ticker]['price'])
                            l_val = mock_stocks[ticker].get('low', mock_stocks[ticker]['price'])
                            c_val = mock_stocks[ticker]['price']
                            candle_history[ticker].append({
                                'open': o_val,
                                'high': h_val,
                                'low': l_val,
                                'close': c_val
                            })
                            candle_history[ticker] = candle_history[ticker][-100:]
                            mock_stocks[ticker]['open'] = c_val
                            mock_stocks[ticker]['high'] = c_val
                            mock_stocks[ticker]['low'] = c_val
                        logger.info(f"1-Minute candle closed for Indices. History updated.")
                    last_candle_minute = current_minute
                    
                    if candle_closed:
                        strategy_type = settings['strategy_type'] if 'strategy_type' in settings.keys() else 'SMA_CROSSOVER'
                        st_period = settings['st_period'] if 'st_period' in settings.keys() else 10
                        st_multiplier = settings['st_multiplier'] if 'st_multiplier' in settings.keys() else 3.0
                        rsi_period = settings['rsi_period'] if 'rsi_period' in settings.keys() else 14
                        rsi_overbought = settings['rsi_overbought'] if 'rsi_overbought' in settings.keys() else 70.0
                        rsi_oversold = settings['rsi_oversold'] if 'rsi_oversold' in settings.keys() else 30.0
                        macd_fast = settings['macd_fast'] if 'macd_fast' in settings.keys() else 12
                        macd_slow = settings['macd_slow'] if 'macd_slow' in settings.keys() else 26
                        macd_signal = settings['macd_signal'] if 'macd_signal' in settings.keys() else 9

                        for ticker in ["NIFTY50", "BANKNIFTY"]:
                            candles = candle_history.get(ticker, [])
                            
                            if strategy_type == 'SMA_CROSSOVER':
                                if not candles or len(candles) < slow_period + 1:
                                    continue
                                closes = [c['close'] for c in candles]
                                    
                                fast_sma_t = sum(closes[-fast_period:]) / fast_period
                                slow_sma_t = sum(closes[-slow_period:]) / slow_period
                                fast_sma_prev = sum(closes[-fast_period-1:-1]) / fast_period
                                slow_sma_prev = sum(closes[-slow_period-1:-1]) / slow_period
                                
                                pending = pending_signals.get(ticker)
                                if pending is not None:
                                    if pending['type'] == 'CE':
                                        if closes[-1] > closes[-2]:
                                            logger.info(f"✅ [OPTIONS] Bullish crossover confirmed on {ticker}: candle closed green ({closes[-1]} > {closes[-2]}). Entering CE.")
                                            trigger_entry_signal(ticker, "CE", fast_period, slow_period)
                                        else:
                                            logger.info(f"❌ [OPTIONS] Bullish crossover confirmation failed on {ticker}: candle closed bearish or flat ({closes[-1]} <= {closes[-2]}). Signal cancelled.")
                                    elif pending['type'] == 'PE':
                                        if closes[-1] < closes[-2]:
                                            logger.info(f"✅ [OPTIONS] Bearish crossover confirmed on {ticker}: candle closed red ({closes[-1]} < {closes[-2]}). Entering PE.")
                                            trigger_entry_signal(ticker, "PE", fast_period, slow_period)
                                        else:
                                            logger.info(f"❌ [OPTIONS] Bearish crossover confirmation failed on {ticker}: candle closed bearish or flat ({closes[-1]} >= {closes[-2]}). Signal cancelled.")
                                    pending_signals[ticker] = None
                                    
                                if not active_position:
                                    if fast_sma_prev <= slow_sma_prev and fast_sma_t > slow_sma_t:
                                        if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                            logger.info(f"⏳ [OPTIONS] Bullish crossover on {ticker} skipped: Volatility below threshold.")
                                        else:
                                            if enable_candle_confirm == 1:
                                                logger.info(f"🔔 [OPTIONS] Bullish Crossover detected on {ticker}. Waiting for next candle confirmation...")
                                                pending_signals[ticker] = {'type': 'CE'}
                                            else:
                                                logger.info(f"🚀 [OPTIONS] Bullish Crossover detected on {ticker} (No confirmation mode). Entering CE.")
                                                trigger_entry_signal(ticker, "CE", fast_period, slow_period)
                                    elif fast_sma_prev >= slow_sma_prev and fast_sma_t < slow_sma_t:
                                        if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                            logger.info(f"⏳ [OPTIONS] Bearish crossover on {ticker} skipped: Volatility below threshold.")
                                        else:
                                            if enable_candle_confirm == 1:
                                                logger.info(f"🔔 [OPTIONS] Bearish Crossover detected on {ticker}. Waiting for next candle confirmation...")
                                                pending_signals[ticker] = {'type': 'PE'}
                                            else:
                                                logger.info(f"⚠️ [OPTIONS] Bearish Crossover detected on {ticker} (No confirmation mode). Entering PE.")
                                                trigger_entry_signal(ticker, "PE", fast_period, slow_period)
                                                
                            elif strategy_type == 'SUPERTREND_RSI_MACD':
                                if not candles or len(candles) < max(40, macd_slow + 2):
                                    continue
                                    
                                st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                rsi_vals = calculate_rsi_new(candles, rsi_period)
                                macd_vals, signal_vals, hist_vals = calculate_macd(candles, macd_fast, macd_slow, macd_signal)
                                
                                trend_curr = trend_vals[-1]
                                trend_prev = trend_vals[-2]
                                rsi_curr = rsi_vals[-1]
                                hist_curr = hist_vals[-1]
                                hist_prev = hist_vals[-2]
                                
                                pending = pending_signals.get(ticker)
                                if pending is not None:
                                    last_close_val = candles[-1]['close']
                                    prev_close_val = candles[-2]['close']
                                    if pending['type'] == 'CE':
                                        if last_close_val > prev_close_val:
                                            logger.info(f"✅ [OPTIONS] Supertrend+RSI+MACD CE confirmed on {ticker}: candle closed green ({last_close_val} > {prev_close_val}). Entering CE.")
                                            trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        else:
                                            logger.info(f"❌ [OPTIONS] Supertrend+RSI+MACD CE confirmation failed on {ticker}: candle closed bearish or flat. Signal cancelled.")
                                    elif pending['type'] == 'PE':
                                        if last_close_val < prev_close_val:
                                            logger.info(f"✅ [OPTIONS] Supertrend+RSI+MACD PE confirmed on {ticker}: candle closed red ({last_close_val} < {prev_close_val}). Entering PE.")
                                            trigger_entry_signal(ticker, "PE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        else:
                                            logger.info(f"❌ [OPTIONS] Supertrend+RSI+MACD PE confirmation failed on {ticker}: candle closed bearish or flat. Signal cancelled.")
                                    pending_signals[ticker] = None
                                    
                                if not active_position:
                                    is_ce_trigger = (trend_prev == -1 and trend_curr == 1) or (hist_prev <= 0 and hist_curr > 0)
                                    is_ce_confirmed = (trend_curr == 1) and (hist_curr > 0) and (rsi_curr > rsi_oversold)
                                    
                                    if is_ce_trigger and is_ce_confirmed:
                                        if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                            logger.info(f"⏳ [OPTIONS] Supertrend+RSI+MACD CE on {ticker} skipped: Volatility below threshold.")
                                        else:
                                            if enable_candle_confirm == 1:
                                                logger.info(f"🔔 [OPTIONS] Supertrend+RSI+MACD CE Crossover on {ticker}. Waiting for confirmation...")
                                                pending_signals[ticker] = {'type': 'CE'}
                                            else:
                                                logger.info(f"🚀 [OPTIONS] Supertrend+RSI+MACD CE Crossover on {ticker} (No confirmation mode). Entering CE.")
                                                trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                                
                                    is_pe_trigger = (trend_prev == 1 and trend_curr == -1) or (hist_prev >= 0 and hist_curr < 0)
                                    is_pe_confirmed = (trend_curr == -1) and (hist_curr < 0) and (rsi_curr < rsi_overbought)
                                    
                                    if is_pe_trigger and is_pe_confirmed:
                                        if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                            logger.info(f"⏳ [OPTIONS] Supertrend+RSI+MACD PE on {ticker} skipped: Volatility below threshold.")
                                        else:
                                            if enable_candle_confirm == 1:
                                                logger.info(f"🔔 [OPTIONS] Supertrend+RSI+MACD PE Crossover on {ticker}. Waiting for confirmation...")
                                                pending_signals[ticker] = {'type': 'PE'}
                                            else:
                                                logger.info(f"⚠️ [OPTIONS] Supertrend+RSI+MACD PE Crossover on {ticker} (No confirmation mode). Entering PE.")
                                                trigger_entry_signal(ticker, "PE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')

                            elif strategy_type == 'SUPERTREND_ONLY':
                                if not candles or len(candles) < 20:
                                    continue
                                    
                                st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                trend_curr = trend_vals[-1]
                                trend_prev = trend_vals[-2]
                                
                                pending = pending_signals.get(ticker)
                                if pending is not None:
                                    last_close_val = candles[-1]['close']
                                    prev_close_val = candles[-2]['close']
                                    if pending['type'] == 'CE':
                                        if last_close_val > prev_close_val:
                                            logger.info(f"✅ [OPTIONS] Supertrend Only CE confirmed on {ticker}: candle closed green ({last_close_val} > {prev_close_val}). Entering CE.")
                                            trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        else:
                                            logger.info(f"❌ [OPTIONS] Supertrend Only CE confirmation failed on {ticker}: candle closed bearish or flat. Signal cancelled.")
                                    elif pending['type'] == 'PE':
                                        if last_close_val < prev_close_val:
                                            logger.info(f"✅ [OPTIONS] Supertrend Only PE confirmed on {ticker}: candle closed red ({last_close_val} < {prev_close_val}). Entering PE.")
                                            trigger_entry_signal(ticker, "PE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        else:
                                            logger.info(f"❌ [OPTIONS] Supertrend Only PE confirmation failed on {ticker}: candle closed bearish or flat. Signal cancelled.")
                                    pending_signals[ticker] = None
                                    
                                if not active_position:
                                    is_ce_trigger = (trend_prev == -1 and trend_curr == 1)
                                    if is_ce_trigger:
                                        if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                            logger.info(f"⏳ [OPTIONS] Supertrend Only CE on {ticker} skipped: Volatility below threshold.")
                                        else:
                                            if enable_candle_confirm == 1:
                                                logger.info(f"🔔 [OPTIONS] Supertrend Only CE Crossover on {ticker}. Waiting for confirmation...")
                                                pending_signals[ticker] = {'type': 'CE'}
                                            else:
                                                logger.info(f"🚀 [OPTIONS] Supertrend Only CE Crossover on {ticker} (No confirmation mode). Entering CE.")
                                                trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                                
                                    is_pe_trigger = (trend_prev == 1 and trend_curr == -1)
                                    if is_pe_trigger:
                                        if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                            logger.info(f"⏳ [OPTIONS] Supertrend Only PE on {ticker} skipped: Volatility below threshold.")
                                        else:
                                            if enable_candle_confirm == 1:
                                                logger.info(f"🔔 [OPTIONS] Supertrend Only PE Crossover on {ticker}. Waiting for confirmation...")
                                                pending_signals[ticker] = {'type': 'PE'}
                                            else:
                                                logger.info(f"⚠️ [OPTIONS] Supertrend Only PE Crossover on {ticker} (No confirmation mode). Entering PE.")
                                                trigger_entry_signal(ticker, "PE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                                
                    # 2. Live ticks monitoring (Startup Entry, Trailing SL)
                    strategy_type = settings['strategy_type'] if 'strategy_type' in settings.keys() else 'SMA_CROSSOVER'
                    for ticker in ["NIFTY50", "BANKNIFTY"]:
                        candles = candle_history.get(ticker, [])
                        
                        if strategy_type == 'SMA_CROSSOVER':
                            closes = [c['close'] for c in candles] + [mock_stocks[ticker]['price']]
                            if len(closes) < slow_period:
                                continue
                            fast_sma = sum(closes[-fast_period:]) / fast_period
                            slow_sma = sum(closes[-slow_period:]) / slow_period
                            
                            prev_state = prev_sma_states[ticker]
                            
                            # Startup entry
                            if prev_state["fast"] is None or prev_state["slow"] is None:
                                if not active_position:
                                    closes_only = [c['close'] for c in candles]
                                    if len(closes_only) >= slow_period + 1:
                                        last_fast_sma = sum(closes_only[-fast_period:]) / fast_period
                                        last_slow_sma = sum(closes_only[-slow_period:]) / slow_period
                                        
                                        if last_fast_sma > last_slow_sma:
                                            if enable_candle_confirm == 0 or closes_only[-1] > closes_only[-2]:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate entry on {ticker} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [OPTIONS] Immediate Golden Cross Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "CE", fast_period, slow_period)
                                        elif last_fast_sma < last_slow_sma:
                                            if enable_candle_confirm == 0 or closes_only[-1] < closes_only[-2]:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate entry on {ticker} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [OPTIONS] Immediate Death Cross Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "PE", fast_period, slow_period)
                                                     
                            prev_sma_states[ticker]["fast"] = fast_sma
                            prev_sma_states[ticker]["slow"] = slow_sma
                            
                        elif strategy_type == 'SUPERTREND_RSI_MACD':
                            if not active_position:
                                if prev_sma_states[ticker]["fast"] is None:
                                    if len(candles) >= max(40, macd_slow + 2):
                                        st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                        rsi_vals = calculate_rsi_new(candles, rsi_period)
                                        macd_vals, signal_vals, hist_vals = calculate_macd(candles, macd_fast, macd_slow, macd_signal)
                                        
                                        trend_curr = trend_vals[-1]
                                        rsi_curr = rsi_vals[-1]
                                        hist_curr = hist_vals[-1]
                                        
                                        if trend_curr == 1 and hist_curr > 0 and rsi_curr > rsi_oversold:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate Supertrend+RSI+MACD CE Entry skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [OPTIONS] Immediate Supertrend+RSI+MACD CE Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        elif trend_curr == -1 and hist_curr < 0 and rsi_curr < rsi_overbought:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate Supertrend+RSI+MACD PE Entry skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [OPTIONS] Immediate Supertrend+RSI+MACD PE Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "PE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                                    
                                    prev_sma_states[ticker]["fast"] = 1.0

                        elif strategy_type == 'SUPERTREND_ONLY':
                            if not active_position:
                                if prev_sma_states[ticker]["fast"] is None:
                                    if len(candles) >= 20:
                                        st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                        trend_curr = trend_vals[-1]
                                        
                                        if trend_curr == 1:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate Supertrend Only CE Entry skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [OPTIONS] Immediate Supertrend Only CE Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        elif trend_curr == -1:
                                            last_close_val = candles[-1]['close']
                                            prev_close_val = candles[-2]['close']
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate Supertrend Only PE Entry skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [OPTIONS] Immediate Supertrend Only PE Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "PE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                                    
                                    prev_sma_states[ticker]["fast"] = 1.0
                        
                    # B. Options Trailing SL & Target check
                    if active_position:
                        ticker = active_position['ticker']
                        curr_price = get_live_price(ticker)
                        
                        if curr_price > active_position['max_price']:
                            active_position['max_price'] = curr_price
                            new_sl = round(curr_price * (1 - trailing_sl_pct), 2)
                            active_position['stop_loss'] = new_sl
                            logger.info(f"Trailed SL UP to {new_sl} for {ticker}")
                            
                        target_price = active_position['entry_price'] * (1 + target_pct)
                        if curr_price >= target_price:
                            square_off_position(ticker, "TARGET_HIT", curr_price)
                        elif curr_price <= active_position['stop_loss']:
                            increment_sl_hits()
                            square_off_position(ticker, "SL_HIT", curr_price)
            else:
                if active_position:
                    ticker = active_position['ticker']
                    square_off_position(ticker, "BOT_STOPPED", get_live_price(ticker))
                for sym in list(active_positions.keys()):
                    square_off_equity_position(sym, "BOT_STOPPED", mock_stocks[sym]['price'])
                bot_running = False
                pending_signals.clear()
                
        except Exception as e:
            logger.error(f"Error in background bot loop: {e}", exc_info=True)
            
        time.sleep(2.0)

def start_bot_thread():
    t = threading.Thread(target=run_trading_bot, daemon=True)
    t.start()
    logger.info("Trading Bot background thread initialized and started.")
    return t

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Initializing strategy bot testing thread...")
    start_bot_thread()
    time.sleep(5)  # Let it run for 5 seconds to test loop setup
    logger.info("Test run finished.")
