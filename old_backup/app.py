import os
import sqlite3
import threading
import time
import logging
import pyotp
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify

def get_ist_now():
    utc_now = datetime.now(timezone.utc)
    ist_timezone = timezone(timedelta(hours=5, minutes=30))
    return utc_now.astimezone(ist_timezone)

# Set up logging to both console and bot.log
log_format = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("KotakAlgo")

import requests

app = Flask(__name__)

# Database file location
DB_FILE = "database.db"

# Global SDK client session
neo_client = None

def login_kotak_neo():
    global neo_client
    try:
        conn = get_db()
        cursor = conn.cursor()
        creds = cursor.execute("SELECT * FROM credentials LIMIT 1").fetchone()
        conn.close()
        
        if not creds or not creds['consumer_key'] or not creds['mobile_number'] or not creds['ucc'] or not creds['mpin']:
            logger.info("Kotak Neo Credentials not fully configured. Using simulation mode.")
            return False
            
        logger.info("Attempting to authenticate with Kotak Neo API...")
        
        # Generate TOTP dynamically using pyotp
        totp_val = ""
        if creds['totp_secret']:
            try:
                totp_val = pyotp.TOTP(creds['totp_secret'].strip().replace(" ", "")).now()
            except Exception as e:
                logger.error(f"Failed to generate TOTP code: {e}")
                return False
                
        client = NeoAPI(
            consumer_key=creds['consumer_key'].strip(),
            environment='prod'
        )
        
        # Format mobile number to start with +91 if not already present
        mob_num = creds['mobile_number'].strip()
        if not mob_num.startswith("+"):
            if mob_num.startswith("91") and len(mob_num) == 12:
                mob_num = "+" + mob_num
            else:
                mob_num = "+91" + mob_num
        
        # First step login: TOTP
        r1 = client.totp_login(
            mobile_number=mob_num,
            ucc=creds['ucc'].strip(),
            totp=totp_val
        )
        logger.info(f"Kotak Neo TOTP Login Response: {r1}")
        if isinstance(r1, dict):
            if 'error' in r1:
                logger.error(f"Kotak Neo TOTP Login failed: {r1['error']}")
                return False
            if 'Error Message' in r1:
                logger.error(f"Kotak Neo TOTP Login failed: {r1['Error Message']}")
                return False
            if 'data' not in r1 and r1.get('stat') != 'Ok':
                logger.error(f"Kotak Neo TOTP Login failed: Invalid status response: {r1}")
                return False
        else:
            logger.error(f"Kotak Neo TOTP Login failed: Invalid response type: {type(r1)}")
            return False
            
        # Second step login: MPIN
        r2 = client.totp_validate(mpin=creds['mpin'].strip())
        logger.info(f"Kotak Neo MPIN validation Response: {r2}")
        if isinstance(r2, dict):
            if 'error' in r2:
                logger.error(f"Kotak Neo MPIN validation failed: {r2['error']}")
                return False
            if 'Error Message' in r2:
                logger.error(f"Kotak Neo MPIN validation failed: {r2['Error Message']}")
                return False
            if 'data' not in r2 and r2.get('stat') != 'Ok':
                logger.error(f"Kotak Neo MPIN validation failed: Invalid status response: {r2}")
                return False
        else:
            logger.error(f"Kotak Neo MPIN validation failed: Invalid response type: {type(r2)}")
            return False
        
        logger.info("Successfully authenticated with Kotak Neo API! Live connection established.")
        neo_client = client
        return True
    except Exception as e:
        logger.error(f"Kotak Neo Authentication failed: {e}", exc_info=True)
        neo_client = None
        return False

def fetch_real_prices_neo():
    global neo_client
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
        logger.warning(f"Could not fetch quotes from Kotak Neo: {e}. Re-authenticating...")
        neo_client = None
    return None

def fetch_real_prices():
    # Try Kotak Neo first if SDK is available and logged in
    if KOTAK_SDK_AVAILABLE and neo_client is not None:
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

def place_neo_order(ticker, action_type, qty, segment='nse_fo'):
    global neo_client
    
    # Resolve product type from database settings
    trade_duration = 'INTRADAY'
    try:
        conn = get_db()
        settings = conn.execute("SELECT trade_duration FROM settings WHERE id = 1").fetchone()
        conn.close()
        if settings and settings['trade_duration']:
            trade_duration = settings['trade_duration']
    except Exception as e:
        logger.warning(f"Could not retrieve trade_duration for order placement: {e}")
        
    product = 'MIS'
    if trade_duration == 'SWING':
        if segment == 'nse_cm':
            product = 'CNC'
        else:
            product = 'NRML'
            
    if not KOTAK_SDK_AVAILABLE or neo_client is None:
        logger.info(f"SDK Offline. Simulated Order executed for {action_type} {ticker} Qty {qty} ({product})")
        return True
        
    try:
        logger.info(f"Placing LIVE Kotak Neo Order: {action_type} {ticker} Qty {qty} Segment {segment} Product {product}")
        res = neo_client.place_order(
            trading_symbol=ticker,
            transaction_type=action_type,
            exchange_segment=segment,
            product=product,
            quantity=str(qty),
            price='0',
            order_type='MKT',
            validity='DAY'
        )
        logger.info(f"Kotak Neo Live Order Result: {res}")
        return True
    except Exception as e:
        logger.error(f"Failed to place order on Kotak Neo API: {e}", exc_info=True)
        return False

# Fetch live starting prices
try:
    real_prices = fetch_real_prices()
    if real_prices:
        nifty_start = real_prices["NIFTY50"]
        bank_start = real_prices["BANKNIFTY"]
        logger.info(f"Loaded real-time starting prices: NIFTY50={nifty_start}, BANKNIFTY={bank_start}")
    else:
        nifty_start = 23360.0
        bank_start = 54490.0
except Exception:
    nifty_start = 23360.0
    bank_start = 54490.0

# Simulated/Real Stock Feed for Paper Trading
mock_stocks = {
    "NIFTY50": {"price": nifty_start, "high": nifty_start, "low": nifty_start, "trend": 0},
    "BANKNIFTY": {"price": bank_start, "high": bank_start, "low": bank_start, "trend": 0}
}

options_feed = {
    "NIFTY_CE": 120.0,
    "NIFTY_PE": 120.0,
    "BANK_CE": 250.0,
    "BANK_PE": 250.0
}

def get_option_name(index_name, option_type):
    spot = mock_stocks[index_name]['price']
    if index_name == "NIFTY50":
        strike = round(spot / 100) * 100
        return f"NIFTY {strike} {option_type}"
    else:
        strike = round(spot / 100) * 100
        return f"BANKNIFTY {strike} {option_type}"

# In-memory bot execution states
active_position = None  # None or dict: {'ticker': ..., 'entry_price': ..., 'qty': ..., 'max_price': ..., 'stop_loss': ..., 'type': 'BUY'/'SELL'}
active_positions = {}  # for equity shares
pending_signals = {}  # pending SMA crossovers waiting for same-direction candle confirmation
last_tick_time = None
sl_hits_count = 0
bot_running = False
last_real_fetch_time = 0
last_opening_trade_date = None
last_auto_activation_date = None

# Try importing the official Kotak Securities SDK
KOTAK_SDK_AVAILABLE = False
try:
    from neo_api_client import NeoAPI
    KOTAK_SDK_AVAILABLE = True
    logger.info("Kotak Neo API Client SDK (neo-api-client) successfully imported.")
except ImportError:
    logger.warning("Kotak Neo API SDK not installed. Running in PAPER TRADING SIMULATION mode.")

# ----------------- DATABASE UTILITIES -----------------

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Credentials table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        consumer_key TEXT,
        consumer_secret TEXT,
        mobile_number TEXT,
        ucc TEXT,
        mpin TEXT,
        totp_secret TEXT
    )
    """)
    
    # Run SQLite migration to add consumer_secret column if table exists without it
    try:
        cursor.execute("SELECT consumer_secret FROM credentials LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE credentials ADD COLUMN consumer_secret TEXT")
        conn.commit()
    
    # 2. Trades table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        action TEXT,
        entry_price REAL,
        exit_price REAL,
        quantity INTEGER,
        exit_reason TEXT,
        pnl REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Run SQLite migration to add strategy column if table exists without it
    try:
        cursor.execute("ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT 'SMA_CROSSOVER'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
        
    # 3. Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        virtual_balance REAL DEFAULT 100000.0,
        trailing_sl_pct REAL DEFAULT 1.0,
        target_pct REAL DEFAULT 2.0,
        trade_quantity INTEGER DEFAULT 65,
        nifty_qty INTEGER DEFAULT 65,
        banknifty_qty INTEGER DEFAULT 30,
        is_active INTEGER DEFAULT 0,
        sl_hits_count INTEGER DEFAULT 0,
        fast_period INTEGER DEFAULT 9,
        slow_period INTEGER DEFAULT 27,
        max_daily_sl INTEGER DEFAULT 3,
        enable_atr_filter INTEGER DEFAULT 0,
        min_atr_val REAL DEFAULT 1.5,
        trade_mode TEXT DEFAULT 'EQUITY',
        equity_allocation REAL DEFAULT 10000.0,
        trade_duration TEXT DEFAULT 'INTRADAY',
        enable_candle_confirm INTEGER DEFAULT 1
    )
    """)
    
    # Check if fast_period column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT fast_period FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN fast_period INTEGER DEFAULT 9")
        cursor.execute("ALTER TABLE settings ADD COLUMN slow_period INTEGER DEFAULT 27")
        conn.commit()

    # Check if nifty_qty column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT nifty_qty FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN nifty_qty INTEGER DEFAULT 65")
        cursor.execute("ALTER TABLE settings ADD COLUMN banknifty_qty INTEGER DEFAULT 30")
        conn.commit()
        
    # Check if max_daily_sl column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT max_daily_sl FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN max_daily_sl INTEGER DEFAULT 3")
        conn.commit()

    # Check if enable_atr_filter column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT enable_atr_filter FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN enable_atr_filter INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE settings ADD COLUMN min_atr_val REAL DEFAULT 1.5")
        conn.commit()
        
    # Check if trade_mode column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT trade_mode FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN trade_mode TEXT DEFAULT 'EQUITY'")
        cursor.execute("ALTER TABLE settings ADD COLUMN equity_allocation REAL DEFAULT 10000.0")
        conn.commit()
        
    # Check if trade_duration column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT trade_duration FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN trade_duration TEXT DEFAULT 'INTRADAY'")
        conn.commit()
        
    # Check if enable_candle_confirm column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT enable_candle_confirm FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN enable_candle_confirm INTEGER DEFAULT 1")
        conn.commit()

    # Check if strategy_type column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT strategy_type FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN strategy_type TEXT DEFAULT 'SMA_CROSSOVER'")
        cursor.execute("ALTER TABLE settings ADD COLUMN st_period INTEGER DEFAULT 10")
        cursor.execute("ALTER TABLE settings ADD COLUMN st_multiplier REAL DEFAULT 3.0")
        cursor.execute("ALTER TABLE settings ADD COLUMN rsi_period INTEGER DEFAULT 14")
        cursor.execute("ALTER TABLE settings ADD COLUMN rsi_overbought REAL DEFAULT 70.0")
        cursor.execute("ALTER TABLE settings ADD COLUMN rsi_oversold REAL DEFAULT 30.0")
        cursor.execute("ALTER TABLE settings ADD COLUMN macd_fast INTEGER DEFAULT 12")
        cursor.execute("ALTER TABLE settings ADD COLUMN macd_slow INTEGER DEFAULT 26")
        cursor.execute("ALTER TABLE settings ADD COLUMN macd_signal INTEGER DEFAULT 9")
        conn.commit()

    # Check if implied_volatility column exists, if not add it (DB Migration)
    try:
        cursor.execute("SELECT implied_volatility FROM settings LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE settings ADD COLUMN implied_volatility REAL DEFAULT 0.165")
        cursor.execute("ALTER TABLE settings ADD COLUMN expiry_date TEXT DEFAULT '2026-06-30'")
        conn.commit()
    
    # 4. Watchlist table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE,
        scanner_high REAL,
        added_date TEXT DEFAULT (date('now'))
    )
    """)
    
    # Check if settings row exists, if not seed it
    cursor.execute("SELECT COUNT(*) FROM settings")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO settings (virtual_balance, trailing_sl_pct, target_pct, trade_quantity, nifty_qty, banknifty_qty, is_active, sl_hits_count, fast_period, slow_period, max_daily_sl, enable_atr_filter, min_atr_val, trade_mode, equity_allocation, trade_duration, enable_candle_confirm, strategy_type, st_period, st_multiplier, rsi_period, rsi_overbought, rsi_oversold, macd_fast, macd_slow, macd_signal) VALUES (100000.0, 1.0, 2.0, 65, 65, 30, 0, 0, 9, 27, 3, 0, 1.5, 'EQUITY', 10000.0, 'INTRADAY', 1, 'SMA_CROSSOVER', 10, 3.0, 14, 70.0, 30.0, 12, 26, 9)")
        conn.commit()
    else:
        # Migrate old default lot size of 50 to 65
        cursor.execute("UPDATE settings SET trade_quantity = 65 WHERE trade_quantity = 50")
        # Ensure nifty_qty and banknifty_qty are populated if they are null
        cursor.execute("UPDATE settings SET nifty_qty = 65 WHERE nifty_qty IS NULL")
        cursor.execute("UPDATE settings SET banknifty_qty = 30 WHERE banknifty_qty IS NULL")
        cursor.execute("UPDATE settings SET max_daily_sl = 3 WHERE max_daily_sl IS NULL")
        cursor.execute("UPDATE settings SET enable_atr_filter = 0 WHERE enable_atr_filter IS NULL")
        cursor.execute("UPDATE settings SET min_atr_val = 1.5 WHERE min_atr_val IS NULL")
        cursor.execute("UPDATE settings SET trade_mode = 'EQUITY' WHERE trade_mode IS NULL")
        cursor.execute("UPDATE settings SET equity_allocation = 10000.0 WHERE equity_allocation IS NULL")
        cursor.execute("UPDATE settings SET trade_duration = 'INTRADAY' WHERE trade_duration IS NULL")
        cursor.execute("UPDATE settings SET enable_candle_confirm = 1 WHERE enable_candle_confirm IS NULL")
        
        # Ensure strategy fields are populated if null
        cursor.execute("UPDATE settings SET strategy_type = 'SMA_CROSSOVER' WHERE strategy_type IS NULL")
        cursor.execute("UPDATE settings SET st_period = 10 WHERE st_period IS NULL")
        cursor.execute("UPDATE settings SET st_multiplier = 3.0 WHERE st_multiplier IS NULL")
        cursor.execute("UPDATE settings SET rsi_period = 14 WHERE rsi_period IS NULL")
        cursor.execute("UPDATE settings SET rsi_overbought = 70.0 WHERE rsi_overbought IS NULL")
        cursor.execute("UPDATE settings SET rsi_oversold = 30.0 WHERE rsi_oversold IS NULL")
        cursor.execute("UPDATE settings SET macd_fast = 12 WHERE macd_fast IS NULL")
        cursor.execute("UPDATE settings SET macd_slow = 26 WHERE macd_slow IS NULL")
        cursor.execute("UPDATE settings SET macd_signal = 9 WHERE macd_signal IS NULL")
        cursor.execute("UPDATE settings SET implied_volatility = 0.165 WHERE implied_volatility IS NULL")
        cursor.execute("UPDATE settings SET expiry_date = '2026-06-30' WHERE expiry_date IS NULL")
        conn.commit()
        
    conn.close()
    logger.info("SQLite Database initialized successfully.")

# Initialize database on startup
init_db()

# Keep track of last 50 closed 1-minute candles for real-time SMA calculation
candle_history = {
    "NIFTY50": [],
    "BANKNIFTY": []
}

# Tracking previous SMA values for live crossover check
prev_sma_states = {
    "NIFTY50": {"fast": None, "slow": None},
    "BANKNIFTY": {"fast": None, "slow": None}
}

# Track candle minutes globally
last_candle_minute = None

def init_candle_history():
    global candle_history
    logger.info("Initializing 1-minute historical candles from Yahoo Finance...")
    for ticker, symbol in [("NIFTY50", "^NSEI"), ("BANKNIFTY", "^NSEBANK")]:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            r = requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d', headers=headers, timeout=5)
            chart_result = r.json()['chart']['result'][0]
            quote = chart_result['indicators']['quote'][0]
            opens = quote.get('open', [])
            highs = quote.get('high', [])
            lows = quote.get('low', [])
            closes = quote.get('close', [])
            
            valid_candles = []
            for o, h, l, c in zip(opens, highs, lows, closes):
                if o is not None and h is not None and l is not None and c is not None:
                    valid_candles.append({
                        'open': round(o, 2),
                        'high': round(h, 2),
                        'low': round(l, 2),
                        'close': round(c, 2)
                    })
            candle_history[ticker] = valid_candles[-100:]
            logger.info(f"Pre-populated {len(candle_history[ticker])} 1-minute historical candles for {ticker}")
        except Exception as e:
            logger.warning(f"Could not pre-populate candle history for {ticker}: {e}")
            candle_history[ticker] = []

# Call once on server startup
init_candle_history()

# Load Nifty 200 symbols from local JSON file
MOMENTUM_STOCK_SYMBOLS = []
try:
    import json
    if os.path.exists("nifty200_symbols.json"):
        with open("nifty200_symbols.json", "r") as f:
            raw_symbols = json.load(f)
            # Clean symbols (filter out placeholders like DUMMY)
            MOMENTUM_STOCK_SYMBOLS = [s for s in raw_symbols if s and not s.startswith("DUMMY")]
            logger.info(f"Successfully loaded {len(MOMENTUM_STOCK_SYMBOLS)} symbols from nifty200_symbols.json")
except Exception as e:
    logger.error(f"Error loading nifty200_symbols.json: {e}")

# Fallback if file not found or empty
if not MOMENTUM_STOCK_SYMBOLS:
    MOMENTUM_STOCK_SYMBOLS = [
        'RELIANCE', 'TCS', 'INFY', 'SBIN', 'HDFCBANK', 
        'ICICIBANK', 'BHARTIAIRTEL', 'LT', 'ITC', 'AXISBANK', 
        'KOTAKBANK', 'HINDUNILVR', 'TATASTEEL', 'TATAMOTORS', 'M&M', 
        'SUNPHARMA', 'MARUTI', 'POWERGRID', 'NTPC', 'ONGC', 
        'COALINDIA', 'ADANIENT', 'ADANIPORTS', 'JSWSTEEL', 'GRASIM', 
        'HCLTECH', 'WIPRO', 'TECHM', 'ULTRACEMCO', 'JIOFIN'
    ]
    logger.info("Using fallback Nifty 50 momentum symbols.")

# Cache for momentum radar to avoid rate limiting
momentum_radar_cache = {
    "gainers": [],
    "losers": [],
    "last_updated": 0
}

def get_momentum_stocks():
    global momentum_radar_cache
    now = time.time()
    if now - momentum_radar_cache["last_updated"] < 120 and len(momentum_radar_cache["gainers"]) > 0:
        return momentum_radar_cache
        
    try:
        from concurrent.futures import ThreadPoolExecutor
        import urllib.parse
        
        def fetch_single(symbol):
            symbol_ns = f"{symbol}.NS"
            symbol_encoded = urllib.parse.quote(symbol_ns)
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'}
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1m&range=1d"
            try:
                r = requests.get(url, headers=headers, timeout=3)
                if r.status_code == 200:
                    res = r.json().get('chart', {}).get('result', [])[0]
                    meta = res.get('meta', {})
                    price = meta.get('regularMarketPrice')
                    prev_close = meta.get('chartPreviousClose')
                    if price and prev_close:
                        change_pct = ((price - prev_close) / prev_close) * 100
                        return {
                            "symbol": symbol,
                            "price": round(price, 2),
                            "change_pct": round(change_pct, 2)
                        }
            except Exception:
                pass
            return None

        # Scan Nifty 200 stocks using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=25) as executor:
            results = list(executor.map(fetch_single, MOMENTUM_STOCK_SYMBOLS))
            
        stocks_data = [r for r in results if r is not None]
        
        if stocks_data:
            stocks_data.sort(key=lambda x: x['change_pct'], reverse=True)
            gainers = stocks_data[:3]
            losers = sorted(stocks_data, key=lambda x: x['change_pct'])[:3]
            
            momentum_radar_cache["gainers"] = gainers
            momentum_radar_cache["losers"] = losers
            momentum_radar_cache["last_updated"] = now
            logger.info(f"Momentum Radar scanned. Gainers: {[g['symbol'] for g in gainers]}, Losers: {[l['symbol'] for l in losers]}")
        else:
            logger.warning("Momentum Radar fetch returned no data.")
    except Exception as e:
        logger.error(f"Error scanning momentum stocks: {e}")
        
    return momentum_radar_cache

def get_recent_volatility(ticker, period=14):
    global candle_history
    candles = candle_history.get(ticker, [])
    if not candles or len(candles) < 2:
        return 0.0
    if isinstance(candles[0], dict):
        closes = [c['close'] for c in candles]
    else:
        closes = candles
    # Calculate average absolute differences between successive close prices
    diffs = [abs(closes[i] - closes[i-1]) for i in range(max(1, len(closes)-period), len(closes))]
    if not diffs:
        return 0.0
    return round(sum(diffs) / len(diffs), 2)

def ensure_candle_history(ticker):
    global candle_history
    if ticker in candle_history and len(candle_history[ticker]) >= 40:
        return
        
    symbol = f"{ticker}.NS" if not ticker.endswith(".NS") else ticker
    clean_ticker = ticker.replace(".NS", "")
    import urllib.parse
    symbol_encoded = urllib.parse.quote(symbol)
    logger.info(f"Pre-populating 1-minute historical candles for {clean_ticker}...")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1m&range=1d', headers=headers, timeout=5)
        chart_result = r.json()['chart']['result'][0]
        quote = chart_result['indicators']['quote'][0]
        opens = quote.get('open', [])
        highs = quote.get('high', [])
        lows = quote.get('low', [])
        closes = quote.get('close', [])
        
        valid_candles = []
        for o, h, l, c in zip(opens, highs, lows, closes):
            if o is not None and h is not None and l is not None and c is not None:
                valid_candles.append({
                    'open': round(o, 2),
                    'high': round(h, 2),
                    'low': round(l, 2),
                    'close': round(c, 2)
                })
        candle_history[clean_ticker] = valid_candles[-100:]
        logger.info(f"Loaded {len(candle_history[clean_ticker])} candles for {clean_ticker}")
    except Exception as e:
        logger.warning(f"Could not populate candle history for {clean_ticker}: {e}")
        candle_history[clean_ticker] = []

def trigger_entry_signal(index_name, option_type, fast_p, slow_p, strategy_type='SMA_CROSSOVER'):
    global active_position
    
    # Resolve parameters from DB
    conn = get_db()
    settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    
    trailing_sl_pct = settings['trailing_sl_pct'] / 100.0
    
    # Resolve lot size dynamically based on DB settings
    nifty_qty = settings['nifty_qty'] if settings['nifty_qty'] is not None else 65
    banknifty_qty = settings['banknifty_qty'] if settings['banknifty_qty'] is not None else 30
    qty = nifty_qty if index_name == "NIFTY50" else banknifty_qty
    
    opt_name = get_option_name(index_name, option_type)
    
    # Get current entry premium price using the dynamic pricing formula
    entry_price = get_live_price(opt_name)
        
    initial_sl = round(entry_price * (1 - trailing_sl_pct), 2)
    
    # If we already have an active position, check if we need to reverse it
    if active_position:
        curr_ticker = active_position['ticker']
        # If it's a different option type in same index, square off first
        if index_name in curr_ticker and option_type not in curr_ticker:
            logger.info(f"Reversal signal! Squaring off existing position in {curr_ticker} before entering {opt_name}")
            # Square off current
            exit_price = get_live_price(curr_ticker)
            square_off_position(curr_ticker, "REVERSAL_SIGNAL", exit_price)
        else:
            # Already in the same direction position, ignore
            return
            
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
        
        # Log to DB
        conn = get_db()
        conn.execute("""
        INSERT INTO trades (ticker, action, entry_price, quantity, exit_reason, timestamp, strategy)
        VALUES (?, 'BUY', ?, ?, 'OPEN', ?, ?)
        """, (opt_name, entry_price, qty, get_ist_now().strftime("%Y-%m-%d %H:%M:%S"), strategy_type))
        conn.commit()
        conn.close()

# Pure Python Technical Indicators for Supertrend, RSI, and MACD
def calculate_atr(candles, period=10):
    n = len(candles)
    if n < period:
        return [0.0] * n
    
    tr_list = []
    tr_list.append(candles[0]['high'] - candles[0]['low'])
    for i in range(1, n):
        h = candles[i]['high']
        l = candles[i]['low']
        prev_c = candles[i-1]['close']
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)
        
    atr_list = [0.0] * n
    first_atr = sum(tr_list[:period]) / period
    atr_list[period - 1] = first_atr
    
    curr_atr = first_atr
    for i in range(period, n):
        curr_atr = (curr_atr * (period - 1) + tr_list[i]) / period
        atr_list[i] = curr_atr
        
    return atr_list

def calculate_supertrend(candles, period=10, multiplier=3.0):
    n = len(candles)
    if n < period:
        return [0.0] * n, [1] * n
        
    atr_list = calculate_atr(candles, period)
    
    st_list = [0.0] * n
    trend = [1] * n
    
    upper_band = [0.0] * n
    lower_band = [0.0] * n
    
    hl2 = (candles[period - 1]['high'] + candles[period - 1]['low']) / 2.0
    upper_band[period - 1] = hl2 + multiplier * atr_list[period - 1]
    lower_band[period - 1] = hl2 - multiplier * atr_list[period - 1]
    st_list[period - 1] = upper_band[period - 1]
    trend[period - 1] = -1
    
    for i in range(period, n):
        hl2 = (candles[i]['high'] + candles[i]['low']) / 2.0
        basic_ub = hl2 + multiplier * atr_list[i]
        basic_lb = hl2 - multiplier * atr_list[i]
        
        if basic_ub < upper_band[i-1] or candles[i-1]['close'] > upper_band[i-1]:
            upper_band[i] = basic_ub
        else:
            upper_band[i] = upper_band[i-1]
            
        if basic_lb > lower_band[i-1] or candles[i-1]['close'] < lower_band[i-1]:
            lower_band[i] = basic_lb
        else:
            lower_band[i] = lower_band[i-1]
            
        if st_list[i-1] == upper_band[i-1]:
            if candles[i]['close'] > upper_band[i]:
                trend[i] = 1
                st_list[i] = lower_band[i]
            else:
                trend[i] = -1
                st_list[i] = upper_band[i]
        else:
            if candles[i]['close'] < lower_band[i]:
                trend[i] = -1
                st_list[i] = upper_band[i]
            else:
                trend[i] = 1
                st_list[i] = lower_band[i]
                
    return st_list, trend

def calculate_rsi_new(candles, period=14):
    n = len(candles)
    if n <= period:
        return [50.0] * n
    
    if isinstance(candles[0], dict):
        closes = [c['close'] for c in candles]
    else:
        closes = candles
        
    gains = [max(0.0, closes[i] - closes[i-1]) for i in range(1, n)]
    losses = [max(0.0, closes[i-1] - closes[i]) for i in range(1, n)]
    
    rsi_vals = [50.0] * n
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    if avg_loss == 0:
        rsi_vals[period] = 100.0
    else:
        rsi_vals[period] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
        
    for i in range(period + 1, n):
        g = gains[i-1]
        l = losses[i-1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            rsi_vals[i] = 100.0
        else:
            rsi_vals[i] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
            
    for i in range(period):
        rsi_vals[i] = rsi_vals[period]
        
    return rsi_vals

def calculate_ema(prices, period):
    n = len(prices)
    if n < period:
        return prices.copy()
    
    ema = [0.0] * n
    sma = sum(prices[:period]) / period
    for i in range(period):
        ema[i] = sma
        
    k = 2.0 / (period + 1)
    for i in range(period, n):
        ema[i] = prices[i] * k + ema[i-1] * (1.0 - k)
        
    return ema

def calculate_macd(candles, fast=12, slow=26, signal=9):
    n = len(candles)
    if isinstance(candles[0], dict):
        closes = [c['close'] for c in candles]
    else:
        closes = candles
        
    if n < slow:
        return [0.0] * n, [0.0] * n, [0.0] * n
        
    fast_ema = calculate_ema(closes, fast)
    slow_ema = calculate_ema(closes, slow)
    
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(n)]
    signal_line = calculate_ema(macd_line, signal)
    hist = [macd_line[i] - signal_line[i] for i in range(n)]
    
    return macd_line, signal_line, hist

def calculate_rsi(prices, period=14):
    if len(prices) <= period:
        return []
    gains = [max(0.0, prices[i] - prices[i-1]) for i in range(1, len(prices))]
    losses = [max(0.0, prices[i-1] - prices[i]) for i in range(1, len(prices))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = []
    if avg_loss == 0:
        rsi_vals.append(100.0)
    else:
        rsi_vals.append(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rsi_vals.append(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
    return rsi_vals

def downsample_to_weekly(timestamps, closes):
    weekly_closes = []
    current_week = None
    last_close = None
    for ts, close in zip(timestamps, closes):
        if ts is None or close is None:
            continue
        dt = datetime.fromtimestamp(ts)
        week_key = dt.isocalendar()[:2]
        if current_week is None:
            current_week = week_key
            last_close = close
        elif week_key == current_week:
            last_close = close
        else:
            weekly_closes.append(last_close)
            current_week = week_key
            last_close = close
    if last_close is not None:
        weekly_closes.append(last_close)
    return weekly_closes

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
                    # Trigger entry signal
                    trigger_equity_signal(ticker, 'BUY', fast_period, slow_period)
                    # Remove from watchlist
                    conn = get_db()
                    conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
                    conn.commit()
                    conn.close()
    except Exception as e:
        logger.warning(f"Error checking breakout for watchlist stock {ticker}: {e}")

# ----------------- CORE BOT LOGIC (BACKGROUND WORKER) -----------------

def run_trading_bot():
    global active_position, active_positions, sl_hits_count, bot_running, last_real_fetch_time, last_candle_minute, last_opening_trade_date, last_auto_activation_date, pending_signals
    logger.info("Background Algorithmic Trading Bot Thread Started.")
    
    import random
    
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
            utc_now = datetime.now(timezone.utc)
            ist_timezone = timezone(timedelta(hours=5, minutes=30))
            now = utc_now.astimezone(ist_timezone)
            
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
                # Attempt to authenticate with Kotak Neo if SDK is available and not already logged in
                if KOTAK_SDK_AVAILABLE and neo_client is None:
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
                        # Set active to 0 in DB
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
                    
                    # Fetch real prices to avoid sudden gap/jumps on options_feed calculation
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
                    
                    # Ensure active positions symbols are in active_symbols so they are fetched and checked
                    for sym in list(active_positions.keys()):
                        if sym not in active_symbols:
                            active_symbols.append(sym)
                            
                    # Ensure watchlist symbols are in active_symbols so they are fetched and checked
                    try:
                        conn_wl = get_db()
                        wl_symbols = [row['ticker'] for row in conn_wl.execute("SELECT ticker FROM watchlist").fetchall()]
                        conn_wl.close()
                        for sym in wl_symbols:
                            if sym not in active_symbols:
                                active_symbols.append(sym)
                    except Exception as e:
                        logger.warning(f"Could not query watchlist for active symbols: {e}")
                            
                    # Immediate Open Entry (anytime between 09:15 AM and 03:30 PM if not already executed today)
                    current_date_str = now.strftime("%Y-%m-%d")
                    if "09:15" <= current_time_str <= "15:30" and last_opening_trade_date != current_date_str:
                        # Reset daily stop loss hits count in DB and memory at the start of a new day
                        try:
                            conn_reset = get_db()
                            conn_reset.execute("UPDATE settings SET sl_hits_count = 0 WHERE id = 1")
                            conn_reset.commit()
                            conn_reset.close()
                            sl_hits_count = 0
                            logger.info(f"New trading day detected ({current_date_str}). Resetting daily Stop Loss hits count to 0 in database.")
                        except Exception as e:
                            logger.error(f"Failed to reset daily SL hits count on new day: {e}")

                        # Determine Nifty direction
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
                            # Buy Top 1 Gainer
                            if len(radar['gainers']) > 0:
                                symbol = radar['gainers'][0]['symbol']
                                if symbol not in active_positions and len(active_positions) < 5:
                                    logger.info(f"Market Open BUY: Nifty is up ({nifty_change_pct:.2f}%). Entering top gainer: {symbol}")
                                    trigger_equity_signal(symbol, 'BUY', fast_period, slow_period)
                                    last_opening_trade_date = current_date_str
                        else:
                            # Sell Top 1 Loser
                            if len(radar['losers']) > 0:
                                symbol = radar['losers'][0]['symbol']
                                if symbol not in active_positions and len(active_positions) < 5:
                                    logger.info(f"Market Open SELL: Nifty is down ({nifty_change_pct:.2f}%). Entering top loser: {symbol}")
                                    trigger_equity_signal(symbol, 'SELL', fast_period, slow_period)
                                    last_opening_trade_date = current_date_str
                    
                    # Fetch latest prices for all active and position stocks
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
                        # Simulate small micro-ticks
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
                                
                    # Update candle minutes and history
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
                    
                    # 1. Closed-candle Crossovers & Confirmation Logic
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
                                if isinstance(candles[0], dict):
                                    closes = [c['close'] for c in candles]
                                else:
                                    closes = candles
                                    
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
                                
                                # A. Check for pending signal confirmation
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                    prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                                    
                                # B. Check for new crossover signals
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        # BUY trigger & confirm
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
                                                    
                                        # SELL trigger & confirm
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

                            elif strategy_type == 'SUPERTREND_ONLY':
                                if not candles or len(candles) < 20:
                                    continue
                                
                                st_vals, trend_vals = calculate_supertrend(candles, st_period, st_multiplier)
                                trend_curr = trend_vals[-1]
                                trend_prev = trend_vals[-2]
                                
                                is_gainer = sym in [s['symbol'] for s in radar['gainers']]
                                is_loser = sym in [s['symbol'] for s in radar['losers']]
                                
                                # A. Check for pending signal confirmation
                                pending = pending_signals.get(sym)
                                if pending is not None:
                                    last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                    prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                                    
                                # B. Check for new crossover signals
                                if sym not in active_positions:
                                    if len(active_positions) < 5:
                                        # BUY trigger
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
                                                    
                                        # SELL trigger
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
                            if candles and isinstance(candles[0], dict):
                                closes = [c['close'] for c in candles] + [mock_stocks[sym]['price']]
                            else:
                                closes = candles + [mock_stocks[sym]['price']]
                                
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
                                # First tick since startup: Immediate entry
                                if sym not in active_positions and len(active_positions) < 5:
                                    if candles and isinstance(candles[0], dict):
                                        closes_only = [c['close'] for c in candles]
                                    else:
                                        closes_only = candles
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
                                live_candles = []
                                for c in candles:
                                    live_candles.append(c)
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
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend+RSI+MACD BUY on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate Supertrend+RSI+MACD BUY Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        elif trend_curr == -1 and hist_curr < 0 and rsi_curr < rsi_overbought and is_loser:
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
                                            if enable_candle_confirm == 0 or last_close_val < prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend+RSI+MACD SELL on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"⚠️ [EQUITY] Immediate Supertrend+RSI+MACD SELL Entry on {sym}!")
                                                    trigger_equity_signal(sym, "SELL", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')

                        elif strategy_type == 'SUPERTREND_ONLY':
                            if sym in active_positions:
                                live_candles = []
                                for c in candles:
                                    live_candles.append(c)
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
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(sym) < min_atr_val:
                                                    logger.info(f"⏳ [EQUITY] Immediate Supertrend Only BUY on {sym} skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [EQUITY] Immediate Supertrend Only BUY Entry on {sym}!")
                                                    trigger_equity_signal(sym, "BUY", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        elif trend_curr == -1 and is_loser:
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                    
                    # Micro-ticks / Yahoo Sync
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
                                
                    # Calculate changes and update option premiums
                    n_diff = mock_stocks["NIFTY50"]["price"] - prev_nifty
                    b_diff = mock_stocks["BANKNIFTY"]["price"] - prev_bank
                    
                    options_feed["NIFTY_CE"] = max(1.0, round(options_feed["NIFTY_CE"] + n_diff * 0.55, 2))
                    options_feed["NIFTY_PE"] = max(1.0, round(options_feed["NIFTY_PE"] - n_diff * 0.45, 2))
                    options_feed["BANK_CE"] = max(1.0, round(options_feed["BANK_CE"] + b_diff * 0.55, 2))
                    options_feed["BANK_PE"] = max(1.0, round(options_feed["BANK_PE"] - b_diff * 0.45, 2))
                    
                    # Candle history updates
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
                    
                    # 1. Closed-candle Crossovers & Confirmation Logic
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
                                if isinstance(candles[0], dict):
                                    closes = [c['close'] for c in candles]
                                else:
                                    closes = candles
                                    
                                fast_sma_t = sum(closes[-fast_period:]) / fast_period
                                slow_sma_t = sum(closes[-slow_period:]) / slow_period
                                fast_sma_prev = sum(closes[-fast_period-1:-1]) / fast_period
                                slow_sma_prev = sum(closes[-slow_period-1:-1]) / slow_period
                                
                                # A. Check for pending signal confirmation
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
                                    
                                # B. Check for new crossover signals
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
                                
                                # A. Check for pending signal confirmation
                                pending = pending_signals.get(ticker)
                                if pending is not None:
                                    last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                    prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                                    
                                # B. Check for new crossover signals
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
                                
                                # A. Check for pending signal confirmation
                                pending = pending_signals.get(ticker)
                                if pending is not None:
                                    last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                    prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                                    
                                # B. Check for new crossover signals
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
                            if candles and isinstance(candles[0], dict):
                                closes = [c['close'] for c in candles] + [mock_stocks[ticker]['price']]
                            else:
                                closes = candles + [mock_stocks[ticker]['price']]
                                
                            if len(closes) < slow_period:
                                continue
                                
                            fast_sma = sum(closes[-fast_period:]) / fast_period
                            slow_sma = sum(closes[-slow_period:]) / slow_period
                            
                            prev_state = prev_sma_states[ticker]
                            
                            # Startup entry
                            if prev_state["fast"] is None or prev_state["slow"] is None:
                                if not active_position:
                                    if candles and isinstance(candles[0], dict):
                                        closes_only = [c['close'] for c in candles]
                                    else:
                                        closes_only = candles
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
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate Supertrend+RSI+MACD CE Entry skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [OPTIONS] Immediate Supertrend+RSI+MACD CE Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_RSI_MACD')
                                        elif trend_curr == -1 and hist_curr < 0 and rsi_curr < rsi_overbought:
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
                                            if enable_candle_confirm == 0 or last_close_val > prev_close_val:
                                                if enable_atr_filter == 1 and get_recent_volatility(ticker) < min_atr_val:
                                                    logger.info(f"⏳ [OPTIONS] Immediate Supertrend Only CE Entry skipped: Volatility below threshold.")
                                                else:
                                                    logger.info(f"🚀 [OPTIONS] Immediate Supertrend Only CE Entry on {ticker}!")
                                                    trigger_entry_signal(ticker, "CE", fast_period, slow_period, strategy_type='SUPERTREND_ONLY')
                                        elif trend_curr == -1:
                                            last_close_val = candles[-1]['close'] if isinstance(candles[-1], dict) else candles[-1]
                                            prev_close_val = candles[-2]['close'] if isinstance(candles[-2], dict) else candles[-2]
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
                # Bot is toggled OFF
                if active_position:
                    ticker = active_position['ticker']
                    square_off_position(ticker, "BOT_STOPPED", get_live_price(ticker))
                for sym in list(active_positions.keys()):
                    square_off_equity_position(sym, "BOT_STOPPED", mock_stocks[sym]['price'])
                bot_running = False
                pending_signals = {}
                
        except Exception as e:
            logger.error(f"Error in background bot loop: {e}", exc_info=True)
            
        time.sleep(2.0)  # Tick rate: every 2 seconds

def square_off_position(ticker, reason, exit_price):
    global active_position
    if not active_position:
        return
        
    entry_price = active_position['entry_price']
    qty = active_position['qty']
    action_type = active_position['type']
    
    # Place live square off order if F&O
    place_neo_order(ticker, 'SELL', qty)
    
    # Calculate Profit & Loss
    if action_type == 'BUY':
        pnl = round((exit_price - entry_price) * qty, 2)
    else:  # SHORT
        pnl = round((entry_price - exit_price) * qty, 2)
        
    logger.info(f"🔴 Squaring off {action_type} {ticker}. Entry: {entry_price}, Exit: {exit_price}, P&L: {pnl} (Reason: {reason})")
    
    # Save exit log to DB
    conn = get_db()
    # Update the open trade
    conn.execute("""
    UPDATE trades 
    SET exit_price = ?, exit_reason = ?, pnl = ? 
    WHERE ticker = ? AND exit_reason = 'OPEN'
    """, (exit_price, reason, pnl, ticker))
    
    # Update virtual account balance
    conn.execute("UPDATE settings SET virtual_balance = virtual_balance + ? WHERE id = 1", (pnl,))
    conn.commit()
    conn.close()
    
    # Clear position in-memory
    active_position = None

token_cache = {}

def get_live_price(ticker):
    global token_cache, neo_client
    
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
                
                # 1. Try fetching live price from Kotak Neo API if SDK is online
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
                
                # 2. Fallback to Dynamic Option Pricing Formula
                spot = mock_stocks[index_name]["price"]
                
                # Calculate time to expiry in years
                try:
                    expiry_dt = datetime.strptime(expiry_db, '%Y-%m-%d')
                    # Get current date in IST timezone to calculate days remaining
                    utc_now = datetime.now(timezone.utc)
                    ist_timezone = timezone(timedelta(hours=5, minutes=30))
                    now_ist = utc_now.astimezone(ist_timezone).date()
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
            
        # Fallback to legacy options_feed if parsing fails
        if "NIFTY" in ticker:
            return options_feed["NIFTY_CE"] if "CE" in ticker else options_feed["NIFTY_PE"]
        else:
            return options_feed["BANK_CE"] if "CE" in ticker else options_feed["BANK_PE"]
            
    clean_ticker = ticker.split(" ")[0]
    if clean_ticker in mock_stocks:
        return mock_stocks[clean_ticker]["price"]
    return 100.0

def increment_sl_hits():
    conn = get_db()
    conn.execute("UPDATE settings SET sl_hits_count = sl_hits_count + 1 WHERE id = 1")
    conn.commit()
    conn.close()

def trigger_equity_signal(symbol, action_type, fast_p, slow_p, strategy_type='SMA_CROSSOVER'):
    global active_positions
    
    if len(active_positions) >= 5:
        logger.warning(f"Cannot enter position in {symbol}. Already holding maximum of 5 positions.")
        return
        
    if symbol in active_positions:
        return
        
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
    
    # Capital per position is equity_allocation / 5.0
    # With 5x leverage, buying power per position is (equity_allocation / 5.0) * 5.0 = equity_allocation
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
        
        logger.info(f"🔔 [EQUITY] Momentum Signal! {action_type} {symbol} Qty {qty} (1/5th allocation with 5x leverage) at {stock_price}. Initial SL: {initial_sl}")
        
        # Log to DB
        conn = get_db()
        conn.execute("""
        INSERT INTO trades (ticker, action, entry_price, quantity, exit_reason, timestamp, strategy)
        VALUES (?, ?, ?, ?, 'OPEN', ?, ?)
        """, (symbol, action_type, stock_price, qty, get_ist_now().strftime("%Y-%m-%d %H:%M:%S"), strategy_type))
        conn.commit()
        conn.close()

def square_off_equity_position(symbol, reason, exit_price):
    global active_positions
    if symbol not in active_positions:
        return
        
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

# Spawn background worker thread
threading.Thread(target=run_trading_bot, daemon=True).start()

# ----------------- FLASK WEB APIS & VIEWS -----------------

@app.route("/")
def index():
    conn = get_db()
    cursor = conn.cursor()
    settings = cursor.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    creds = cursor.execute("SELECT * FROM credentials LIMIT 1").fetchone()
    recent_trades = cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    
    return render_template(
        "dashboard.html",
        settings=settings,
        creds=creds,
        recent_trades=recent_trades,
        sdk_available=KOTAK_SDK_AVAILABLE
    )

@app.route("/save-credentials", methods=["POST"])
def save_credentials():
    consumer_key = request.form.get("consumer_key", "").strip()
    consumer_secret = request.form.get("consumer_secret", "").strip()
    mobile_number = request.form.get("mobile_number", "").strip()
    ucc = request.form.get("ucc", "").strip()
    mpin = request.form.get("mpin", "").strip()
    totp_secret = request.form.get("totp_secret", "").strip()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM credentials")
    count = cursor.fetchone()[0]
    
    if count == 0:
        cursor.execute("""
        INSERT INTO credentials (consumer_key, consumer_secret, mobile_number, ucc, mpin, totp_secret)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (consumer_key, consumer_secret, mobile_number, ucc, mpin, totp_secret))
    else:
        cursor.execute("""
        UPDATE credentials 
        SET consumer_key = ?, consumer_secret = ?, mobile_number = ?, ucc = ?, mpin = ?, totp_secret = ?
        WHERE id = 1
        """, (consumer_key, consumer_secret, mobile_number, ucc, mpin, totp_secret))
        
    conn.commit()
    conn.close()
    
    logger.info("Kotak Neo API Credentials updated in database.")
    return jsonify({"success": True, "message": "Credentials saved successfully!"})

@app.route("/save-settings", methods=["POST"])
def save_settings():
    def get_float(name, default):
        val = request.form.get(name, "").strip()
        try:
            return float(val) if val else default
        except ValueError:
            return default

    def get_int(name, default):
        val = request.form.get(name, "").strip()
        try:
            return int(val) if val else default
        except ValueError:
            return default

    trailing_sl_pct = get_float("trailing_sl_pct", 1.0)
    target_pct = get_float("target_pct", 2.0)
    nifty_qty = get_int("nifty_qty", 65)
    banknifty_qty = get_int("banknifty_qty", 30)
    trade_mode = request.form.get("trade_mode", "EQUITY").strip()
    equity_allocation = get_float("equity_allocation", 10000.0)
    fast_period = get_int("fast_period", 9)
    slow_period = get_int("slow_period", 27)
    max_daily_sl = get_int("max_daily_sl", 3)
    enable_atr_filter = 1 if request.form.get("enable_atr_filter") in ["on", "1", "true"] else 0
    min_atr_val = get_float("min_atr_val", 1.5)
    trade_duration = request.form.get("trade_duration", "INTRADAY").strip()
    enable_candle_confirm = 1 if request.form.get("enable_candle_confirm") in ["on", "1", "true"] else 0
    strategy_type = request.form.get("strategy_type", "SMA_CROSSOVER").strip()
    st_period = get_int("st_period", 10)
    st_multiplier = get_float("st_multiplier", 3.0)
    rsi_period = get_int("rsi_period", 14)
    rsi_overbought = get_float("rsi_overbought", 70.0)
    rsi_oversold = get_float("rsi_oversold", 30.0)
    macd_fast = get_int("macd_fast", 12)
    macd_slow = get_int("macd_slow", 26)
    macd_signal = get_int("macd_signal", 9)
    implied_volatility = get_float("implied_volatility", 16.5) / 100.0
    expiry_date = request.form.get("expiry_date", "2026-06-30").strip()
    
    conn = get_db()
    conn.execute("""
    UPDATE settings 
    SET trailing_sl_pct = ?, target_pct = ?, nifty_qty = ?, banknifty_qty = ?, trade_mode = ?, equity_allocation = ?, fast_period = ?, slow_period = ?, max_daily_sl = ?, enable_atr_filter = ?, min_atr_val = ?, trade_duration = ?, enable_candle_confirm = ?, strategy_type = ?, st_period = ?, st_multiplier = ?, rsi_period = ?, rsi_overbought = ?, rsi_oversold = ?, macd_fast = ?, macd_slow = ?, macd_signal = ?, implied_volatility = ?, expiry_date = ?
    WHERE id = 1
    """, (trailing_sl_pct, target_pct, nifty_qty, banknifty_qty, trade_mode, equity_allocation, fast_period, slow_period, max_daily_sl, enable_atr_filter, min_atr_val, trade_duration, enable_candle_confirm, strategy_type, st_period, st_multiplier, rsi_period, rsi_overbought, rsi_oversold, macd_fast, macd_slow, macd_signal, implied_volatility, expiry_date))
    conn.commit()
    conn.close()
    
    logger.info(f"Settings updated: Trailing SL: {trailing_sl_pct}%, Target: {target_pct}%, Mode: {trade_mode}, Allocation: {equity_allocation}, Nifty Qty: {nifty_qty}, Bank Qty: {banknifty_qty}, Fast SMA: {fast_period}, Slow SMA: {slow_period}, Max Daily SL: {max_daily_sl}, ATR Filter: {enable_atr_filter}, Min ATR: {min_atr_val}, Duration: {trade_duration}, Confirmation Toggle: {enable_candle_confirm}, Strategy: {strategy_type}, ST: {st_period} ({st_multiplier}x), RSI: {rsi_period} (OB: {rsi_overbought}, OS: {rsi_oversold}), MACD: {macd_fast}/{macd_slow}/{macd_signal}, IV: {implied_volatility*100.0}%, Expiry: {expiry_date}")
    return jsonify({"success": True, "message": "Configuration saved successfully!"})

@app.route("/toggle-bot", methods=["POST"])
def toggle_bot():
    conn = get_db()
    cursor = conn.cursor()
    settings = cursor.execute("SELECT is_active, sl_hits_count, max_daily_sl FROM settings WHERE id = 1").fetchone()
    
    current_state = settings['is_active']
    sl_hits = settings['sl_hits_count']
    max_daily_sl = settings['max_daily_sl'] if 'max_daily_sl' in settings.keys() else 3
    
    if current_state == 0 and sl_hits >= max_daily_sl:
        conn.close()
        return jsonify({"success": False, "message": f"Halted! {max_daily_sl} Stop Losses were hit today. Please Reset P&L to trade again."})
        
    new_state = 1 if current_state == 0 else 0
    conn.execute("UPDATE settings SET is_active = ? WHERE id = 1", (new_state,))
    conn.commit()
    conn.close()
    
    state_label = "STARTED" if new_state == 1 else "STOPPED"
    logger.info(f"Bot toggled to: {state_label}")
    return jsonify({"success": True, "state": new_state, "message": f"Algo Trading Bot {state_label}!"})

@app.route("/reset", methods=["POST"])
def reset_simulation():
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
    active_positions = {}
    pending_signals = {}
    
    return jsonify({"success": True, "message": "Simulation ledger reset successfully!"})

@app.route("/square-off", methods=["POST"])
def manual_square_off():
    ticker = request.form.get("ticker")
    if not ticker:
        return jsonify({"success": False, "message": "Ticker not specified"})
        
    global active_positions, active_position
    
    if active_position and active_position['ticker'] == ticker:
        opt_exit = get_live_price(ticker)
        square_off_position(ticker, "MANUAL_SQUARE_OFF", opt_exit)
        return jsonify({"success": True, "message": f"Successfully squared off {ticker}"})
        
    elif ticker in active_positions:
        exit_price = mock_stocks[ticker]['price']
        square_off_equity_position(ticker, "MANUAL_SQUARE_OFF", exit_price)
        return jsonify({"success": True, "message": f"Successfully squared off {ticker}"})
        
    return jsonify({"success": False, "message": f"No active position found for {ticker}"})

@app.route("/api/status")
def get_status():
    conn = get_db()
    cursor = conn.cursor()
    settings = cursor.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    recent_trades_db = cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    
    recent_trades = []
    for t in recent_trades_db:
        recent_trades.append(dict(t))
    
    trade_mode = settings['trade_mode'] if ('trade_mode' in settings.keys() and settings['trade_mode']) else 'EQUITY'
    
    # Calculate open position unrealized P&L
    unrealized_pnl = 0.0
    pos_list = []
    pos_legacy = None
    
    target_pct = settings['target_pct'] / 100.0 if ('target_pct' in settings.keys() and settings['target_pct'] is not None) else 0.02
    
    # 1. Fetch Option Position if trade_mode is OPTIONS or BOTH
    if (trade_mode == 'OPTIONS' or trade_mode == 'BOTH') and active_position:
        ticker = active_position['ticker']
        curr_price = get_live_price(ticker)
        entry_price = active_position['entry_price']
        qty = active_position['qty']
        pnl = round((curr_price - entry_price) * qty, 2) if active_position['type'] == 'BUY' else round((entry_price - curr_price) * qty, 2)
        target_price = round(entry_price * (1 + target_pct), 2)
        
        pos_legacy = {
            'ticker': ticker,
            'type': active_position['type'],
            'entry_price': entry_price,
            'qty': qty,
            'current_price': curr_price,
            'stop_loss': active_position['stop_loss'],
            'target_price': target_price,
            'pnl': pnl,
            'entry_time': active_position['entry_time'],
            'strategy': active_position.get('strategy', 'SMA_CROSSOVER')
        }
        pos_list.append(pos_legacy)
        unrealized_pnl += pnl
        
    # 2. Fetch Equity Positions if trade_mode is EQUITY or BOTH
    if (trade_mode == 'EQUITY' or trade_mode == 'BOTH') and active_positions:
        for sym, pos in active_positions.items():
            curr_price = mock_stocks[sym]['price'] if sym in mock_stocks else pos['entry_price']
            entry_price = pos['entry_price']
            qty = pos['qty']
            pnl = round((curr_price - entry_price) * qty, 2) if pos['type'] == 'BUY' else round((entry_price - curr_price) * qty, 2)
            target_price = round(entry_price * (1 + target_pct) if pos['type'] == 'BUY' else entry_price * (1 - target_pct), 2)
            
            p_details = {
                'ticker': sym,
                'type': pos['type'],
                'entry_price': entry_price,
                'qty': qty,
                'current_price': curr_price,
                'stop_loss': pos['stop_loss'],
                'target_price': target_price,
                'pnl': pnl,
                'entry_time': pos['entry_time'],
                'strategy': pos.get('strategy', 'SMA_CROSSOVER')
            }
            pos_list.append(p_details)
            unrealized_pnl += pnl
            
        if len(pos_list) > 0 and pos_legacy is None:
            pos_legacy = pos_list[0]
            
    # Calculate strategy-wise realized P&L from trades table
    strategy_summary = {
        'SMA_CROSSOVER': 0.0,
        'SUPERTREND_RSI_MACD': 0.0,
        'SUPERTREND_ONLY': 0.0
    }
    try:
        conn = get_db()
        cursor = conn.cursor()
        strategy_pnl_db = cursor.execute("SELECT strategy, SUM(pnl) as total_pnl FROM trades WHERE exit_reason != 'OPEN' AND pnl IS NOT NULL GROUP BY strategy").fetchall()
        conn.close()
        for row in strategy_pnl_db:
            strat = row['strategy']
            if strat in strategy_summary:
                strategy_summary[strat] = round(row['total_pnl'], 2)
    except Exception as e:
        logger.error(f"Error compiling strategy summary: {e}")
            
    # Fetch momentum stocks list
    radar = get_momentum_stocks()
    
    return jsonify({
        'is_active': settings['is_active'],
        'virtual_balance': round(settings['virtual_balance'], 2),
        'equity': round(settings['virtual_balance'] + unrealized_pnl, 2),
        'sl_hits_count': settings['sl_hits_count'],
        'max_daily_sl': settings['max_daily_sl'] if 'max_daily_sl' in settings.keys() else 3,
        'enable_atr_filter': settings['enable_atr_filter'] if 'enable_atr_filter' in settings.keys() else 0,
        'min_atr_val': settings['min_atr_val'] if 'min_atr_val' in settings.keys() else 1.5,
        'stocks': mock_stocks,
        'options': {
            'NIFTY_CE_NAME': get_option_name("NIFTY50", "CE"),
            'NIFTY_CE_PRICE': get_live_price(get_option_name("NIFTY50", "CE")),
            'NIFTY_PE_NAME': get_option_name("NIFTY50", "PE"),
            'NIFTY_PE_PRICE': get_live_price(get_option_name("NIFTY50", "PE")),
            'BANK_CE_NAME': get_option_name("BANKNIFTY", "CE"),
            'BANK_CE_PRICE': get_live_price(get_option_name("BANKNIFTY", "CE")),
            'BANK_PE_NAME': get_option_name("BANKNIFTY", "PE"),
            'BANK_PE_PRICE': get_live_price(get_option_name("BANKNIFTY", "PE"))
        },
        'position': pos_legacy,
        'positions': pos_list,
        'momentum_radar': radar,
        'trade_mode': trade_mode,
        'trade_duration': settings['trade_duration'] if 'trade_duration' in settings.keys() else 'INTRADAY',
        'enable_candle_confirm': settings['enable_candle_confirm'] if 'enable_candle_confirm' in settings.keys() else 1,
        'strategy_type': settings['strategy_type'] if 'strategy_type' in settings.keys() else 'SMA_CROSSOVER',
        'st_period': settings['st_period'] if 'st_period' in settings.keys() else 10,
        'st_multiplier': settings['st_multiplier'] if 'st_multiplier' in settings.keys() else 3.0,
        'rsi_period': settings['rsi_period'] if 'rsi_period' in settings.keys() else 14,
        'rsi_overbought': settings['rsi_overbought'] if 'rsi_overbought' in settings.keys() else 70.0,
        'rsi_oversold': settings['rsi_oversold'] if 'rsi_oversold' in settings.keys() else 30.0,
        'macd_fast': settings['macd_fast'] if 'macd_fast' in settings.keys() else 12,
        'macd_slow': settings['macd_slow'] if 'macd_slow' in settings.keys() else 26,
        'macd_signal': settings['macd_signal'] if 'macd_signal' in settings.keys() else 9,
        'strategy_summary': strategy_summary,
        'recent_trades': recent_trades
    })

@app.route("/api/logs")
def get_logs():
    # Return last 40 lines of logs efficiently
    if not os.path.exists("bot.log"):
        return "No logs generated yet."
    try:
        with open("bot.log", "rb") as f:
            try:
                f.seek(-15000, os.SEEK_END)
            except IOError:
                f.seek(0)
            content = f.read().decode('utf-8', errors='ignore')
            lines = content.splitlines()
            return "\n".join(lines[-40:])
    except Exception as e:
        return f"Error reading logs: {e}"

@app.route("/api/chart-data")
def get_chart_data():
    ticker = request.args.get("ticker", "").strip()
    if not ticker:
        return jsonify({"success": False, "message": "Ticker not specified"})
        
    ticker_upper = ticker.upper()
    if "NIFTY" in ticker_upper:
        if "BANK" in ticker_upper:
            symbol = "^NSEBANK"
        else:
            symbol = "^NSEI"
    elif "BANKNIFTY" in ticker_upper:
        symbol = "^NSEBANK"
    else:
        symbol = ticker_upper.replace(".NS", "")
        symbol = f"{symbol}.NS"
        
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        import urllib.parse
        symbol_encoded = urllib.parse.quote(symbol)
        # Fetch last 2 days of 1-minute historical candles from Yahoo Finance
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1m&range=2d"
        logger.info(f"Fetching chart data for {ticker} using URL: {url}")
        r = requests.get(url, headers=headers, timeout=5)
        
        if r.status_code != 200:
            return jsonify({"success": False, "message": f"Yahoo Finance returned status {r.status_code}"})
            
        res = r.json().get('chart', {}).get('result', [])
        if not res:
            return jsonify({"success": False, "message": "No chart result from Yahoo Finance"})
            
        chart_data = res[0]
        timestamps = chart_data.get('timestamp', [])
        indicators = chart_data.get('indicators', {}).get('quote', [{}])[0]
        opens = indicators.get('open', [])
        highs = indicators.get('high', [])
        lows = indicators.get('low', [])
        closes = indicators.get('close', [])
        
        if not timestamps:
            return jsonify({"success": False, "message": "No price history found for this symbol"})
            
        # Group 1-minute candles into 3-minute candles
        groups = {}
        for ts, o, h, l, c in zip(timestamps, opens, highs, lows, closes):
            if ts is None or o is None or h is None or l is None or c is None:
                continue
            # Group by 3-minute boundary (180 seconds)
            group_key = ts - (ts % 180)
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append({"time": ts, "open": o, "high": h, "low": l, "close": c})
            
        candles_3m = []
        sorted_keys = sorted(groups.keys())
        for gkey in sorted_keys:
            items = groups[gkey]
            items.sort(key=lambda x: x['time'])
            candles_3m.append({
                "time": gkey, # Already in seconds
                "open": round(items[0]["open"], 2),
                "high": round(max(item["high"] for item in items), 2),
                "low": round(min(item["low"] for item in items), 2),
                "close": round(items[-1]["close"], 2)
            })
            
        # Calculate SMA 9 and SMA 27
        sma9_list = []
        sma27_list = []
        closes_3m = [c["close"] for c in candles_3m]
        
        for idx in range(len(candles_3m)):
            t = candles_3m[idx]["time"]
            # SMA 9
            if idx >= 8:
                val = round(sum(closes_3m[idx-8:idx+1]) / 9.0, 2)
                sma9_list.append({"time": t, "value": val})
            # SMA 27
            if idx >= 26:
                val = round(sum(closes_3m[idx-26:idx+1]) / 27.0, 2)
                sma27_list.append({"time": t, "value": val})
                
        # Calculate Supertrend if active
        conn = get_db()
        settings = conn.execute("SELECT strategy_type, st_period, st_multiplier FROM settings WHERE id = 1").fetchone()
        conn.close()
        
        supertrend_list = []
        strategy_type = settings['strategy_type'] if settings else 'SMA_CROSSOVER'
        
        if strategy_type == 'SUPERTREND_RSI_MACD':
            st_period = settings['st_period'] if settings['st_period'] is not None else 10
            st_multiplier = settings['st_multiplier'] if settings['st_multiplier'] is not None else 3.0
            
            st_vals, trend_vals = calculate_supertrend(candles_3m, st_period, st_multiplier)
            for idx in range(len(candles_3m)):
                if idx >= st_period - 1:
                    supertrend_list.append({
                        "time": candles_3m[idx]["time"],
                        "value": round(st_vals[idx], 2),
                        "color": "#10b981" if trend_vals[idx] == 1 else "#ef4444"
                    })
                    
        return jsonify({
            "success": True,
            "ticker": ticker_upper,
            "candles": candles_3m,
            "sma9": sma9_list,
            "sma27": sma27_list,
            "supertrend": supertrend_list
        })
        
    except Exception as e:
        logger.error(f"Error fetching chart data for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)})
        
# ----------------- WATCHLIST & SWING SCANNER ENDPOINTS -----------------

@app.route("/api/watchlist/add", methods=["POST"])
def watchlist_add():
    data = request.get_json() or {}
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"success": False, "message": "Ticker is required"})
    
    try:
        import urllib.parse
        symbol_ns = f"{ticker}.NS"
        symbol_encoded = urllib.parse.quote(symbol_ns)
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1d&range=5d"
        r = requests.get(url, headers=headers, timeout=4)
        if r.status_code != 200:
            return jsonify({"success": False, "message": f"Could not find ticker {ticker} on Yahoo Finance"})
        
        res = r.json()['chart']['result'][0]
        quote = res['indicators']['quote'][0]
        highs = [h for h in quote.get('high', []) if h is not None]
        if not highs:
            return jsonify({"success": False, "message": f"No high price data found for {ticker}"})
        
        scanner_high = round(highs[-1], 2)
        
        conn = get_db()
        try:
            conn.execute("INSERT INTO watchlist (ticker, scanner_high) VALUES (?, ?)", (ticker, scanner_high))
            conn.commit()
            success = True
            message = f"Added {ticker} to watchlist with Breakout Target High: {scanner_high}"
        except sqlite3.IntegrityError:
            conn.execute("UPDATE watchlist SET scanner_high = ? WHERE ticker = ?", (scanner_high, ticker))
            conn.commit()
            success = True
            message = f"Updated breakout high for {ticker} to {scanner_high}"
        conn.close()
        
        logger.info(message)
        return jsonify({"success": success, "message": message, "scanner_high": scanner_high})
    except Exception as e:
        logger.error(f"Error adding ticker {ticker} to watchlist: {e}")
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/watchlist/list", methods=["GET"])
def watchlist_list():
    try:
        conn = get_db()
        rows = conn.execute("SELECT * FROM watchlist ORDER BY id DESC").fetchall()
        conn.close()
        
        items = []
        for row in rows:
            ticker = row['ticker']
            live_price = None
            if ticker in mock_stocks:
                live_price = mock_stocks[ticker]['price']
            
            items.append({
                "id": row['id'],
                "ticker": ticker,
                "scanner_high": row['scanner_high'],
                "added_date": row['added_date'],
                "live_price": live_price
            })
        return jsonify({"success": True, "watchlist": items})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/watchlist/remove", methods=["POST"])
def watchlist_remove():
    data = request.get_json() or {}
    ticker = data.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"success": False, "message": "Ticker is required"})
    
    try:
        conn = get_db()
        conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        conn.commit()
        conn.close()
        logger.info(f"Removed {ticker} from watchlist.")
        return jsonify({"success": True, "message": f"Removed {ticker} from watchlist."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/run-scan", methods=["POST"])
def run_scan():
    data = request.get_json() or {}
    weekly_rsi_thresh = float(data.get("weekly_rsi", 60.0))
    daily_rsi_thresh = float(data.get("daily_rsi", 40.0))
    hourly_rsi_thresh = float(data.get("hourly_rsi", 40.0))
    crossover = data.get("crossover", True)
    
    try:
        universe = data.get("universe", "nifty200")
        if universe == "nifty500":
            symbols_file = "nifty500_symbols.json"
        else:
            symbols_file = "nifty200_symbols.json"
            
        symbols_path = os.path.join(os.path.dirname(__file__), symbols_file)
        if not os.path.exists(symbols_path):
            return jsonify({"success": False, "message": f"{symbols_file} not found"})
        
        import json
        with open(symbols_path, "r") as f:
            symbols = json.load(f)
            
        from concurrent.futures import ThreadPoolExecutor
        
        # First Pass: Daily and Weekly
        candidates = []
        def scan_daily_wk(sym):
            try:
                import urllib.parse
                symbol_ns = f"{sym}.NS"
                symbol_encoded = urllib.parse.quote(symbol_ns)
                headers = {'User-Agent': 'Mozilla/5.0'}
                r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1d&range=1y", headers=headers, timeout=3)
                if r.status_code != 200:
                    return None
                res_d = r.json()['chart']['result'][0]
                closes_d = res_d['indicators']['quote'][0]['close']
                timestamps_d = res_d['timestamp']
                
                valid_daily = [(ts, c) for ts, c in zip(timestamps_d, closes_d) if ts is not None and c is not None]
                if not valid_daily:
                    return None
                ts_clean, closes_clean = zip(*valid_daily)
                
                rsi_d = calculate_rsi(closes_clean)
                if not rsi_d:
                    return None
                d_rsi = rsi_d[-1]
                
                closes_wk = downsample_to_weekly(ts_clean, closes_clean)
                rsi_wk = calculate_rsi(closes_wk)
                if len(rsi_wk) < 2:
                    return None
                wk_rsi_prev = rsi_wk[-2]
                wk_rsi_curr = rsi_wk[-1]
                
                quote_d = res_d['indicators']['quote'][0]
                volumes = [v for v in quote_d.get('volume', []) if v is not None]
                highs = [h for h in quote_d.get('high', []) if h is not None]
                vol = volumes[-1] if volumes else 0
                high = highs[-1] if highs else closes_clean[-1]
                
                # Check weekly breakout
                weekly_pass = False
                if crossover:
                    if wk_rsi_curr >= weekly_rsi_thresh and wk_rsi_prev <= weekly_rsi_thresh:
                        weekly_pass = True
                else:
                    if wk_rsi_curr >= weekly_rsi_thresh:
                        weekly_pass = True
                        
                # Daily pullback
                daily_pass = (d_rsi <= daily_rsi_thresh)
                
                if weekly_pass and daily_pass:
                    return {
                        "symbol": sym,
                        "weekly_rsi": round(wk_rsi_curr, 2),
                        "weekly_rsi_prev": round(wk_rsi_prev, 2),
                        "daily_rsi": round(d_rsi, 2),
                        "volume": vol,
                        "scanner_high": round(high, 2),
                        "price": round(closes_clean[-1], 2)
                    }
            except Exception:
                pass
            return None
            
        with ThreadPoolExecutor(max_workers=35) as executor:
            results = list(executor.map(scan_daily_wk, symbols))
        candidates = [r for r in results if r is not None]
        
        # Second Pass: Hourly for candidates
        final_results = []
        def scan_hourly(cand):
            sym = cand['symbol']
            try:
                import urllib.parse
                symbol_ns = f"{sym}.NS"
                symbol_encoded = urllib.parse.quote(symbol_ns)
                headers = {'User-Agent': 'Mozilla/5.0'}
                r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol_encoded}?interval=1h&range=1mo", headers=headers, timeout=3)
                if r.status_code == 200:
                    res_h = r.json()['chart']['result'][0]
                    closes_h = res_h['indicators']['quote'][0]['close']
                    closes_h = [c for c in closes_h if c is not None]
                    rsi_h = calculate_rsi(closes_h)
                    if rsi_h:
                        h_rsi = rsi_h[-1]
                        if h_rsi < hourly_rsi_thresh:
                            cand['hourly_rsi'] = round(h_rsi, 2)
                            return cand
            except Exception:
                pass
            return None
            
        if candidates:
            with ThreadPoolExecutor(max_workers=min(20, len(candidates))) as executor:
                hourly_results = list(executor.map(scan_hourly, candidates))
            final_results = [r for r in hourly_results if r is not None]
            
        # Sort by volume descending
        final_results.sort(key=lambda x: x['volume'], reverse=True)
        
        return jsonify({"success": True, "results": final_results})
    except Exception as e:
        logger.error(f"Error running scan: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)})

# ----------------- BACKTESTING ENGINE -----------------

@app.route("/backtest", methods=["POST"])
def run_backtest():
    fast_period = int(request.form.get("fast_period", 20))
    slow_period = int(request.form.get("slow_period", 50))
    ticker = request.form.get("ticker", "NIFTY50")
    period = request.form.get("period", "1y")
    
    conn = get_db()
    settings = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    
    trailing_sl_pct = float(request.form.get("trailing_sl_pct", settings['trailing_sl_pct'] if settings else 1.0)) / 100.0
    target_pct = float(request.form.get("target_pct", settings['target_pct'] if settings else 2.0)) / 100.0
    enable_candle_confirm = int(request.form.get("enable_candle_confirm", settings['enable_candle_confirm'] if (settings and 'enable_candle_confirm' in settings.keys() and settings['enable_candle_confirm'] is not None) else 1))
    
    is_equity = ticker not in ["NIFTY50", "BANKNIFTY"]
    
    # Resolve correct trade qty / allocation
    if not is_equity:
        nifty_qty = settings['nifty_qty'] if (settings and settings['nifty_qty'] is not None) else 65
        banknifty_qty = settings['banknifty_qty'] if (settings and settings['banknifty_qty'] is not None) else 30
        trade_qty = nifty_qty if ticker == "NIFTY50" else banknifty_qty
        capital_per_trade = 100.0 * trade_qty
    else:
        equity_allocation = settings['equity_allocation'] if (settings and 'equity_allocation' in settings.keys() and settings['equity_allocation']) else 10000.0
        capital_per_trade = equity_allocation
        trade_qty = 10
        
    logger.info(f"Running historical backtest simulation on {ticker} (Fast SMA: {fast_period}, Slow SMA: {slow_period}, Period: {period})...")
    
    # Fetch real historical data from Yahoo Finance
    symbol = "^NSEI" if ticker == "NIFTY50" else ("^NSEBANK" if ticker == "BANKNIFTY" else f"{ticker}.NS")
    
    prices = []
    dates = []
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={period}&interval=1d"
        r = requests.get(url, headers=headers, timeout=5)
        res = r.json()['chart']['result'][0]
        timestamps = res.get('timestamp', [])
        closes = res['indicators']['quote'][0]['close']
        
        for ts, val in zip(timestamps, closes):
            if val is not None:
                prices.append(round(val, 2))
                dates.append(datetime.fromtimestamp(ts).strftime('%Y-%m-%d'))
                
        logger.info(f"Successfully fetched {len(prices)} price points for {ticker} backtest.")
    except Exception as e:
        logger.warning(f"Failed to fetch historical data from Yahoo: {e}")
        # Fallback
        start_price = 23300.0 if ticker == "NIFTY50" else (54400.0 if ticker == "BANKNIFTY" else 600.0)
        import random
        random.seed(42)
        prices = [start_price]
        dates = []
        volatility = 0.012 if ticker == "NIFTY50" else 0.018
        for j in range(500):
            change = random.uniform(-volatility, volatility)
            prices.append(round(prices[-1] * (1 + change), 2))
        base_date = datetime(2025, 1, 1)
        from datetime import timedelta
        for j in range(len(prices)):
            dates.append((base_date + timedelta(days=j)).strftime('%Y-%m-%d'))
            
    # Calculate SMA
    fast_sma = []
    slow_sma = []
    for i in range(len(prices)):
        if i >= fast_period:
            fast_sma.append(sum(prices[i-fast_period:i])/fast_period)
        else:
            fast_sma.append(None)
        if i >= slow_period:
            slow_sma.append(sum(prices[i-slow_period:i])/slow_period)
        else:
            slow_sma.append(None)
            
    # Backtest Loop
    balance = 100000.0
    position = None
    pending_backtest = None
    trades_list = []
    trades_count = 0
    wins = 0
    total_pnl = 0.0
    
    for i in range(1, len(prices)):
        if fast_sma[i] is None or slow_sma[i] is None or fast_sma[i-1] is None or slow_sma[i-1] is None:
            continue
            
        prev_fast = fast_sma[i-1]
        prev_slow = slow_sma[i-1]
        curr_fast = fast_sma[i]
        curr_slow = slow_sma[i]
        
        if position is not None:
            # ACTIVE POSITION CHECKS
            if not is_equity:
                # F&O Options Mode
                spot_change = prices[i] - position['entry_spot']
                if position['type'] == 'CE':
                    current_premium = round(position['entry_premium'] + spot_change * 0.55, 2)
                else:
                    current_premium = round(position['entry_premium'] - spot_change * 0.45, 2)
                current_premium = max(1.0, current_premium)
                
                if current_premium > position['max_premium']:
                    position['max_premium'] = current_premium
                    position['stop_loss'] = round(current_premium * (1 - trailing_sl_pct), 2)
                    
                target_price = position['entry_premium'] * (1 + target_pct)
                
                # Exits
                if current_premium >= target_price:
                    # Target Hit
                    pnl = round((target_price - position['entry_premium']) * position['qty'], 2)
                    balance += pnl
                    total_pnl += pnl
                    trades_count += 1
                    if pnl > 0: wins += 1
                    trades_list.append({
                        'trade_num': len(trades_list) + 1,
                        'type': position['type'],
                        'entry_date': position['entry_date'],
                        'exit_date': dates[i],
                        'entry_spot': position['entry_spot'],
                        'exit_spot': prices[i],
                        'entry_premium': position['entry_premium'],
                        'exit_premium': round(target_price, 2),
                        'qty': position['qty'],
                        'investment': round(position['entry_premium'] * position['qty'], 2),
                        'pnl': pnl,
                        'pnl_pct': round((pnl / (position['entry_premium'] * position['qty'])) * 100, 2),
                        'reason': 'TARGET_HIT'
                    })
                    position = None
                elif current_premium <= position['stop_loss']:
                    # SL Hit
                    exit_premium = position['stop_loss']
                    pnl = round((exit_premium - position['entry_premium']) * position['qty'], 2)
                    balance += pnl
                    total_pnl += pnl
                    trades_count += 1
                    if pnl > 0: wins += 1
                    trades_list.append({
                        'trade_num': len(trades_list) + 1,
                        'type': position['type'],
                        'entry_date': position['entry_date'],
                        'exit_date': dates[i],
                        'entry_spot': position['entry_spot'],
                        'exit_spot': prices[i],
                        'entry_premium': position['entry_premium'],
                        'exit_premium': exit_premium,
                        'qty': position['qty'],
                        'investment': round(position['entry_premium'] * position['qty'], 2),
                        'pnl': pnl,
                        'pnl_pct': round((pnl / (position['entry_premium'] * position['qty'])) * 100, 2),
                        'reason': 'SL_HIT'
                    })
                    position = None
                elif prev_fast >= prev_slow and curr_fast < curr_slow and position['type'] == 'CE':
                    # Crossover Reversal
                    pnl = round((current_premium - position['entry_premium']) * position['qty'], 2)
                    balance += pnl
                    total_pnl += pnl
                    trades_count += 1
                    if pnl > 0: wins += 1
                    trades_list.append({
                        'trade_num': len(trades_list) + 1,
                        'type': 'CE',
                        'entry_date': position['entry_date'],
                        'exit_date': dates[i],
                        'entry_spot': position['entry_spot'],
                        'exit_spot': prices[i],
                        'entry_premium': position['entry_premium'],
                        'exit_premium': current_premium,
                        'qty': position['qty'],
                        'investment': round(position['entry_premium'] * position['qty'], 2),
                        'pnl': pnl,
                        'pnl_pct': round((pnl / (position['entry_premium'] * position['qty'])) * 100, 2),
                        'reason': 'CROSSOVER_REVERSAL'
                    })
                    # Enter PE
                    position = {
                        'type': 'PE',
                        'entry_date': dates[i],
                        'entry_spot': prices[i],
                        'entry_premium': 100.0,
                        'max_premium': 100.0,
                        'stop_loss': round(100.0 * (1 - trailing_sl_pct), 2),
                        'qty': trade_qty
                    }
                elif prev_fast <= prev_slow and curr_fast > curr_slow and position['type'] == 'PE':
                    # Crossover Reversal
                    pnl = round((current_premium - position['entry_premium']) * position['qty'], 2)
                    balance += pnl
                    total_pnl += pnl
                    trades_count += 1
                    if pnl > 0: wins += 1
                    trades_list.append({
                        'trade_num': len(trades_list) + 1,
                        'type': 'PE',
                        'entry_date': position['entry_date'],
                        'exit_date': dates[i],
                        'entry_spot': position['entry_spot'],
                        'exit_spot': prices[i],
                        'entry_premium': position['entry_premium'],
                        'exit_premium': current_premium,
                        'qty': position['qty'],
                        'investment': round(position['entry_premium'] * position['qty'], 2),
                        'pnl': pnl,
                        'pnl_pct': round((pnl / (position['entry_premium'] * position['qty'])) * 100, 2),
                        'reason': 'CROSSOVER_REVERSAL'
                    })
                    # Enter CE
                    position = {
                        'type': 'CE',
                        'entry_date': dates[i],
                        'entry_spot': prices[i],
                        'entry_premium': 100.0,
                        'max_premium': 100.0,
                        'stop_loss': round(100.0 * (1 - trailing_sl_pct), 2),
                        'qty': trade_qty
                    }
            else:
                # Equity Shares Mode
                curr_price = prices[i]
                if position['type'] == 'BUY':
                    if curr_price > position['max_price']:
                        position['max_price'] = curr_price
                        position['stop_loss'] = round(curr_price * (1 - trailing_sl_pct), 2)
                        
                    target_price = position['entry_price'] * (1 + target_pct)
                    
                    if curr_price >= target_price:
                        # Target Hit
                        pnl = round((target_price - position['entry_price']) * position['qty'], 2)
                        balance += pnl
                        total_pnl += pnl
                        trades_count += 1
                        if pnl > 0: wins += 1
                        trades_list.append({
                            'trade_num': len(trades_list) + 1,
                            'type': 'BUY',
                            'entry_date': position['entry_date'],
                            'exit_date': dates[i],
                            'entry_spot': position['entry_price'],
                            'exit_spot': curr_price,
                            'entry_premium': position['entry_price'],
                            'exit_premium': target_price,
                            'qty': position['qty'],
                            'investment': round(position['entry_price'] * position['qty'], 2),
                            'pnl': pnl,
                            'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                            'reason': 'TARGET_HIT'
                        })
                        position = None
                    elif curr_price <= position['stop_loss']:
                        # SL Hit
                        pnl = round((position['stop_loss'] - position['entry_price']) * position['qty'], 2)
                        balance += pnl
                        total_pnl += pnl
                        trades_count += 1
                        if pnl > 0: wins += 1
                        trades_list.append({
                            'trade_num': len(trades_list) + 1,
                            'type': 'BUY',
                            'entry_date': position['entry_date'],
                            'exit_date': dates[i],
                            'entry_spot': position['entry_price'],
                            'exit_spot': curr_price,
                            'entry_premium': position['entry_price'],
                            'exit_premium': position['stop_loss'],
                            'qty': position['qty'],
                            'investment': round(position['entry_price'] * position['qty'], 2),
                            'pnl': pnl,
                            'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                            'reason': 'SL_HIT'
                        })
                        position = None
                    elif prev_fast >= prev_slow and curr_fast < curr_slow:
                        # Crossover Reversal (Exit Buy, Enter Sell)
                        pnl = round((curr_price - position['entry_price']) * position['qty'], 2)
                        balance += pnl
                        total_pnl += pnl
                        trades_count += 1
                        if pnl > 0: wins += 1
                        trades_list.append({
                            'trade_num': len(trades_list) + 1,
                            'type': 'BUY',
                            'entry_date': position['entry_date'],
                            'exit_date': dates[i],
                            'entry_spot': position['entry_price'],
                            'exit_spot': curr_price,
                            'entry_premium': position['entry_price'],
                            'exit_premium': curr_price,
                            'qty': position['qty'],
                            'investment': round(position['entry_price'] * position['qty'], 2),
                            'pnl': pnl,
                            'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                            'reason': 'CROSSOVER_REVERSAL'
                        })
                        
                        # Enter SELL (Short)
                        new_qty = int((equity_allocation * 5.0) / curr_price)
                        if new_qty <= 0: new_qty = 1
                        position = {
                            'type': 'SELL',
                            'entry_date': dates[i],
                            'entry_price': curr_price,
                            'max_price': curr_price,
                            'min_price': curr_price,
                            'stop_loss': round(curr_price * (1 + trailing_sl_pct), 2),
                            'qty': new_qty
                        }
                elif position['type'] == 'SELL':
                    if curr_price < position['min_price']:
                        position['min_price'] = curr_price
                        position['stop_loss'] = round(curr_price * (1 + trailing_sl_pct), 2)
                        
                    target_price = position['entry_price'] * (1 - target_pct)
                    
                    if curr_price <= target_price:
                        # Target Hit
                        pnl = round((position['entry_price'] - target_price) * position['qty'], 2)
                        balance += pnl
                        total_pnl += pnl
                        trades_count += 1
                        if pnl > 0: wins += 1
                        trades_list.append({
                            'trade_num': len(trades_list) + 1,
                            'type': 'SELL',
                            'entry_date': position['entry_date'],
                            'exit_date': dates[i],
                            'entry_spot': position['entry_price'],
                            'exit_spot': curr_price,
                            'entry_premium': position['entry_price'],
                            'exit_premium': target_price,
                            'qty': position['qty'],
                            'investment': round(position['entry_price'] * position['qty'], 2),
                            'pnl': pnl,
                            'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                            'reason': 'TARGET_HIT'
                        })
                        position = None
                    elif curr_price >= position['stop_loss']:
                        # SL Hit
                        pnl = round((position['entry_price'] - position['stop_loss']) * position['qty'], 2)
                        balance += pnl
                        total_pnl += pnl
                        trades_count += 1
                        if pnl > 0: wins += 1
                        trades_list.append({
                            'trade_num': len(trades_list) + 1,
                            'type': 'SELL',
                            'entry_date': position['entry_date'],
                            'exit_date': dates[i],
                            'entry_spot': position['entry_price'],
                            'exit_spot': curr_price,
                            'entry_premium': position['entry_price'],
                            'exit_premium': position['stop_loss'],
                            'qty': position['qty'],
                            'investment': round(position['entry_price'] * position['qty'], 2),
                            'pnl': pnl,
                            'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                            'reason': 'SL_HIT'
                        })
                        position = None
                    elif prev_fast <= prev_slow and curr_fast > curr_slow:
                        # Crossover Reversal (Exit Sell, Enter Buy)
                        pnl = round((position['entry_price'] - curr_price) * position['qty'], 2)
                        balance += pnl
                        total_pnl += pnl
                        trades_count += 1
                        if pnl > 0: wins += 1
                        trades_list.append({
                            'trade_num': len(trades_list) + 1,
                            'type': 'SELL',
                            'entry_date': position['entry_date'],
                            'exit_date': dates[i],
                            'entry_spot': position['entry_price'],
                            'exit_spot': curr_price,
                            'entry_premium': position['entry_price'],
                            'exit_premium': curr_price,
                            'qty': position['qty'],
                            'investment': round(position['entry_price'] * position['qty'], 2),
                            'pnl': pnl,
                            'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                            'reason': 'CROSSOVER_REVERSAL'
                        })
                        
                        # Enter BUY (Long)
                        new_qty = int((equity_allocation * 5.0) / curr_price)
                        if new_qty <= 0: new_qty = 1
                        position = {
                            'type': 'BUY',
                            'entry_date': dates[i],
                            'entry_price': curr_price,
                            'max_price': curr_price,
                            'min_price': curr_price,
                            'stop_loss': round(curr_price * (1 - trailing_sl_pct), 2),
                            'qty': new_qty
                        }
        else:
            # NO ACTIVE POSITION: CHECK CONFIRMATION OR NEW CROSSOVER
            if enable_candle_confirm == 1:
                # 1. Check pending signal confirmation
                if pending_backtest is not None:
                    is_confirm = False
                    if pending_backtest['type'] in ['CE', 'BUY']:
                        # Bullish confirmation: current closed price > previous closed price
                        if prices[i] > prices[i-1]:
                            is_confirm = True
                    else:
                        # Bearish confirmation: current closed price < previous closed price
                        if prices[i] < prices[i-1]:
                            is_confirm = True
                            
                    if is_confirm:
                        # Enter trade
                        if not is_equity:
                            position = {
                                'type': pending_backtest['type'],
                                'entry_date': dates[i],
                                'entry_spot': prices[i],
                                'entry_premium': 100.0,
                                'max_premium': 100.0,
                                'stop_loss': round(100.0 * (1 - trailing_sl_pct), 2),
                                'qty': trade_qty
                            }
                        else:
                            new_qty = int((equity_allocation * 5.0) / prices[i])
                            if new_qty <= 0: new_qty = 1
                            position = {
                                'type': pending_backtest['type'],
                                'entry_date': dates[i],
                                'entry_price': prices[i],
                                'max_price': prices[i],
                                'min_price': prices[i],
                                'stop_loss': round(prices[i] * (1 - trailing_sl_pct) if pending_backtest['type'] == 'BUY' else prices[i] * (1 + trailing_sl_pct), 2),
                                'qty': new_qty
                            }
                    # Clear pending signal
                    pending_backtest = None
                    
                # 2. Check for new crossover signals
                if position is None:
                    if prev_fast <= prev_slow and curr_fast > curr_slow:
                        # Golden Cross
                        pending_backtest = {'type': 'CE' if not is_equity else 'BUY'}
                    elif prev_fast >= prev_slow and curr_fast < curr_slow:
                        # Death Cross
                        pending_backtest = {'type': 'PE' if not is_equity else 'SELL'}
            else:
                # Immediate entry on crossover (original behavior)
                if prev_fast <= prev_slow and curr_fast > curr_slow:
                    # Golden Cross
                    if not is_equity:
                        position = {
                            'type': 'CE',
                            'entry_date': dates[i],
                            'entry_spot': prices[i],
                            'entry_premium': 100.0,
                            'max_premium': 100.0,
                            'stop_loss': round(100.0 * (1 - trailing_sl_pct), 2),
                            'qty': trade_qty
                        }
                    else:
                        new_qty = int((equity_allocation * 5.0) / prices[i])
                        if new_qty <= 0: new_qty = 1
                        position = {
                            'type': 'BUY',
                            'entry_date': dates[i],
                            'entry_price': prices[i],
                            'max_price': prices[i],
                            'min_price': prices[i],
                            'stop_loss': round(prices[i] * (1 - trailing_sl_pct), 2),
                            'qty': new_qty
                        }
                elif prev_fast >= prev_slow and curr_fast < curr_slow:
                    # Death Cross
                    if not is_equity:
                        position = {
                            'type': 'PE',
                            'entry_date': dates[i],
                            'entry_spot': prices[i],
                            'entry_premium': 100.0,
                            'max_premium': 100.0,
                            'stop_loss': round(100.0 * (1 - trailing_sl_pct), 2),
                            'qty': trade_qty
                        }
                    else:
                        new_qty = int((equity_allocation * 5.0) / prices[i])
                        if new_qty <= 0: new_qty = 1
                        position = {
                            'type': 'SELL',
                            'entry_date': dates[i],
                            'entry_price': prices[i],
                            'max_price': prices[i],
                            'min_price': prices[i],
                            'stop_loss': round(prices[i] * (1 + trailing_sl_pct), 2),
                            'qty': new_qty
                        }
                    
    # CLOSE OPEN POSITION AT END OF DATA
    if position is not None:
        curr_price = prices[-1]
        if not is_equity:
            spot_change = curr_price - position['entry_spot']
            if position['type'] == 'CE':
                current_premium = round(position['entry_premium'] + spot_change * 0.55, 2)
            else:
                current_premium = round(position['entry_premium'] - spot_change * 0.45, 2)
            current_premium = max(1.0, current_premium)
            pnl = round((current_premium - position['entry_premium']) * position['qty'], 2)
            balance += pnl
            total_pnl += pnl
            trades_count += 1
            if pnl > 0: wins += 1
            trades_list.append({
                'trade_num': len(trades_list) + 1,
                'type': position['type'],
                'entry_date': position['entry_date'],
                'exit_date': dates[-1],
                'entry_spot': position['entry_spot'],
                'exit_spot': curr_price,
                'entry_premium': position['entry_premium'],
                'exit_premium': current_premium,
                'qty': position['qty'],
                'investment': round(position['entry_premium'] * position['qty'], 2),
                'pnl': pnl,
                'pnl_pct': round((pnl / (position['entry_premium'] * position['qty'])) * 100, 2),
                'reason': 'END_OF_DATA'
            })
        else:
            if position['type'] == 'BUY':
                pnl = round((curr_price - position['entry_price']) * position['qty'], 2)
            else:
                pnl = round((position['entry_price'] - curr_price) * position['qty'], 2)
            balance += pnl
            total_pnl += pnl
            trades_count += 1
            if pnl > 0: wins += 1
            trades_list.append({
                'trade_num': len(trades_list) + 1,
                'type': position['type'],
                'entry_date': position['entry_date'],
                'exit_date': dates[-1],
                'entry_spot': position['entry_price'],
                'exit_spot': curr_price,
                'entry_premium': position['entry_price'],
                'exit_premium': curr_price,
                'qty': position['qty'],
                'investment': round(position['entry_price'] * position['qty'], 2),
                'pnl': pnl,
                'pnl_pct': round((pnl / (position['entry_price'] * position['qty'])) * 100, 2),
                'reason': 'END_OF_DATA'
            })
            
    win_rate = (wins / trades_count * 100) if trades_count > 0 else 0.0
    roi = (total_pnl / capital_per_trade * 100) if capital_per_trade > 0 else 0.0
    
    return jsonify({
        'success': True,
        'ticker': ticker,
        'trades_count': trades_count,
        'net_pnl': round(total_pnl, 2),
        'win_rate': round(win_rate, 2),
        'final_balance': round(balance, 2),
        'capital_per_trade': round(capital_per_trade, 2),
        'roi': round(roi, 2),
        'trades_list': trades_list
    })

if __name__ == "__main__":
    # Ensure port 5000 is open. In Flask, debug=False is recommended when running background threads.
    app.run(host="0.0.0.0", port=5000, debug=False)
