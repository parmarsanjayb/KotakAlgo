import urllib.request
import json
import time
import os
import sys

base_url = "http://127.0.0.1:8000"

def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.loads(response.read().decode())
    except:
        return None

# Get tracked symbols once
symbols_data = get_json(f"{base_url}/api/market/symbols")
symbols = symbols_data.get("symbols", []) if symbols_data else []

if not symbols:
    print("Could not retrieve symbols. Make sure the server is running on http://127.0.0.1:8000")
    sys.exit(1)

# If we are running inside the agent task runner, let's limit it to 40 iterations (1 minute)
# so the task completes. If run directly, run forever.
max_iterations = 40 if len(sys.argv) > 1 and sys.argv[1] == "--limit" else None
iteration = 0

try:
    while True:
        os.system('cls' if os.name == 'nt' else 'clear')
        print("=" * 60)
        print("   🤖 SPV QUANTUM AI - LIVE TERMINAL PRICE TICKER 🤖")
        print("=" * 60)
        print(f"{'SYMBOL':<15} | {'LTP (Last Price)':<18} | {'VOLUME':<12}")
        print("-" * 60)
        
        for sym in symbols:
            price_data = get_json(f"{base_url}/api/market/price/{sym}")
            if price_data:
                ltp = price_data.get("ltp", 0.0)
                volume = price_data.get("volume", 0.0)
                print(f"{sym:<15} | {ltp:<18,.2f} | {volume:<12,.3f}")
            else:
                print(f"{sym:<15} | Disconnected...")
        print("-" * 60)
        if max_iterations:
            remaining = max_iterations - iteration
            print(f"Monitoring live (auto-closing in {remaining * 1.5:.0f}s)...")
        else:
            print("Press Ctrl+C to exit and stop live feed monitoring.")
        print("=" * 60)
        
        time.sleep(1.5)
        
        if max_iterations:
            iteration += 1
            if iteration >= max_iterations:
                break
except KeyboardInterrupt:
    print("\nLive price monitoring stopped.")
