import logging
from db import get_db
from kotak_auth import KOTAK_SDK_AVAILABLE, get_neo_client

logger = logging.getLogger("TradingBot.OrderManager")

def place_neo_order(ticker, action_type, qty, segment='nse_fo'):
    """
    Unified order placement function. Resolves order properties (MIS/CNC/NRML) from DB settings.
    If KOTAK_SDK_AVAILABLE is False or neo_client is None, executes as a simulated paper trade.
    """
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
            
    neo_client = get_neo_client()
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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Testing order placement function...")
    place_neo_order("NIFTY 24000 CE", "BUY", 65)
