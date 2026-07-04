import logging
import pyotp
from db import get_db

logger = logging.getLogger("TradingBot.Auth")

# Global SDK client session
neo_client = None
KOTAK_SDK_AVAILABLE = False

try:
    from neo_api_client import NeoAPI
    KOTAK_SDK_AVAILABLE = True
    logger.info("Kotak Neo API Client SDK (neo-api-client) successfully imported.")
except ImportError:
    logger.warning("Kotak Neo API SDK not installed. Running in PAPER TRADING SIMULATION mode.")

def get_neo_client():
    global neo_client
    return neo_client

def set_neo_client(client):
    global neo_client
    neo_client = client

def login_kotak_neo():
    global neo_client
    if not KOTAK_SDK_AVAILABLE:
        logger.info("Kotak Neo SDK is not available. Using simulation mode.")
        return False
        
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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Testing Kotak Neo authentication...")
    success = login_kotak_neo()
    logger.info(f"Login success status: {success}")
