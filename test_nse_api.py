import requests

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
}

session = requests.Session()
# Visit home page first to get cookies
try:
    session.get("https://www.nseindia.com", headers=headers, timeout=5)
    r = session.get("https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY", headers=headers, timeout=5)
    print("Status Code:", r.status_code)
    data = r.json()
    records = data.get('records', {})
    expiry_dates = records.get('expiryDates', [])
    print("Available Expiries:", expiry_dates[:3])
    underlying_value = records.get('underlyingValue', 0)
    print("Underlying Value:", underlying_value)
except Exception as e:
    print("Error:", e)
