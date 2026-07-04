import os
import logging
import time
import requests
from datetime import datetime, timezone, timedelta
from db import get_db
from kotak_auth import KOTAK_SDK_AVAILABLE, get_neo_client

logger = logging.getLogger("TradingBot.MarketData")

# Timezone utility
def get_ist_now():
    utc_now = datetime.now(timezone.utc)
    ist_timezone = timezone(timedelta(hours=5, minutes=30))
    return utc_now.astimezone(ist_timezone)

# Token cache for Kotak Neo API script search
token_cache = {}

# Keep track of last 50-100 closed 1-minute candles for real-time calculation
candle_history = {
    "NIFTY50": [],
    "BANKNIFTY": []
}

# Tracking previous SMA states for live crossover check
prev_sma_states = {
    "NIFTY50": {"fast": None, "slow": None},
    "BANKNIFTY": {"fast": None, "slow": None}
}

# Track candle minutes globally
last_candle_minute = None

# Fallback/Default prices
options_feed = {
    "NIFTY_CE": 120.0,
    "NIFTY_PE": 120.0,
    "BANK_CE": 250.0,
    "BANK_PE": 250.0
}

# ----------------- REAL PRICE FETCHING -----------------

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
        logger.warning(f"Could not fetch quotes from Kotak Neo: {e}. Session might need renewal.")
    return None

def fetch_real_prices():
    # Try Kotak Neo first if SDK is available and logged in
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

# Load starting prices dynamically
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

# Live simulated/real stock prices mapping
mock_stocks = {
    "NIFTY50": {"price": nifty_start, "high": nifty_start, "low": nifty_start, "trend": 0},
    "BANKNIFTY": {"price": bank_start, "high": bank_start, "low": bank_start, "trend": 0}
}

# ----------------- LIVE OPTION/STOCK LTP LOOKUP -----------------

def get_option_name(index_name, option_type):
    spot = mock_stocks[index_name]['price']
    if index_name == "NIFTY50":
        strike = round(spot / 100) * 100
        return f"NIFTY {strike} {option_type}"
    else:
        strike = round(spot / 100) * 100
        return f"BANKNIFTY {strike} {option_type}"

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
                
                # 1. Try fetching live price from Kotak Neo API if SDK is online
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
                
                # 2. Fallback to Dynamic Option Pricing Formula
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
            
        # Fallback to legacy options_feed if parsing fails
        if "NIFTY" in ticker:
            return options_feed["NIFTY_CE"] if "CE" in ticker else options_feed["NIFTY_PE"]
        else:
            return options_feed["BANK_CE"] if "CE" in ticker else options_feed["BANK_PE"]
            
    clean_ticker = ticker.split(" ")[0]
    if clean_ticker in mock_stocks:
        return mock_stocks[clean_ticker]["price"]
    return 100.0

# ----------------- CANDLE HISTORY MANAGEMENT -----------------

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

# ----------------- MOMENTUM STOCK RADAR -----------------

MOMENTUM_STOCK_SYMBOLS = []
try:
    import json
    if os.path.exists("nifty200_symbols.json"):
        with open("nifty200_symbols.json", "r") as f:
            raw_symbols = json.load(f)
            MOMENTUM_STOCK_SYMBOLS = [s for s in raw_symbols if s and not s.startswith("DUMMY")]
            logger.info(f"Successfully loaded {len(MOMENTUM_STOCK_SYMBOLS)} symbols from nifty200_symbols.json")
except Exception as e:
    logger.error(f"Error loading nifty200_symbols.json: {e}")

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
    diffs = [abs(closes[i] - closes[i-1]) for i in range(max(1, len(closes)-period), len(closes))]
    if not diffs:
        return 0.0
    return round(sum(diffs) / len(diffs), 2)

# ----------------- TECHNICAL INDICATORS -----------------

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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Initializing candle history test...")
    init_candle_history()
    logger.info(f"NIFTY50 candles loaded: {len(candle_history['NIFTY50'])}")
    logger.info(f"BANKNIFTY candles loaded: {len(candle_history['BANKNIFTY'])}")
    
    logger.info("Testing option live price estimation...")
    ce_name = get_option_name("NIFTY50", "CE")
    price = get_live_price(ce_name)
    logger.info(f"Estimated price for {ce_name}: {price}")
