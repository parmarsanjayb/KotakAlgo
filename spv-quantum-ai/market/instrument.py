import asyncio
import csv
import io
import json
import pathlib
from typing import Any, Dict, List, Optional

class InstrumentManager:
    """
    Manages instrument specifications: tokens, segments, lot sizes, tick sizes.
    Every broker adapter must map its internal tokens to canonical symbol names here.
    """

    def __init__(self) -> None:
        self._instruments: Dict[str, Dict[str, Any]] = {
            # Index — token is the underlying index's pAssetCode from Kotak's scrip master
            "NIFTY50":      {"token": "26000",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 65,   "tick_size": 0.05,   "precision": 2},
            "BANKNIFTY":    {"token": "26009",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 30,   "tick_size": 0.05,   "precision": 2},
            "FINNIFTY":     {"token": "26037",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 60,   "tick_size": 0.05,   "precision": 2},
            "MIDCPNIFTY":   {"token": "26074",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 120,  "tick_size": 0.05,   "precision": 2},
            "SENSEX":       {"token": "1",      "exchange": "BSE", "segment": "bse_cm", "lot_size": 20,   "tick_size": 0.05,   "precision": 2},
            # Equity — verified against Kotak's real scrip master (nse_cm, EQ series)
            "RELIANCE":     {"token": "2885",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "TCS":          {"token": "11536",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "HDFCBANK":     {"token": "1333",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "INFY":         {"token": "1594",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "ICICIBANK":    {"token": "4963",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "SBIN":         {"token": "3045",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "ITC":          {"token": "1660",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "LT":           {"token": "11483",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "AXISBANK":     {"token": "5900",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "KOTAKBANK":    {"token": "1922",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "HINDUNILVR":   {"token": "1394",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "BHARTIARTL":   {"token": "10604",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "BAJFINANCE":   {"token": "317",    "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "MARUTI":       {"token": "10999",  "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            "WIPRO":        {"token": "3787",   "exchange": "NSE", "segment": "nse_cm", "lot_size": 1,    "tick_size": 0.05,   "precision": 2},
            # Currency
            "USDINR":       {"token": "usdinr", "exchange": "CDS", "segment": "cd_fo",  "lot_size": 1000, "tick_size": 0.0025, "precision": 4},
            # Commodity — nearest-expiry MCX futures contract, verified against Kotak's
            # real scrip master (the earlier placeholder string tokens were never real).
            "CRUDEOIL":     {"token": "520702", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 100,  "tick_size": 1.0,    "precision": 2},
            "NATURALGAS":   {"token": "538685", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 1250, "tick_size": 0.1,    "precision": 2},
            "GOLD":         {"token": "466583", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 1,    "tick_size": 1.0,    "precision": 2},
            "SILVER":       {"token": "471725", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 30,   "tick_size": 1.0,    "precision": 2},
            "COPPER":       {"token": "562048", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 2500, "tick_size": 0.05,   "precision": 2},
            "ZINC":         {"token": "562053", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 5,    "tick_size": 0.05,   "precision": 2},
            "ALUMINIUM":    {"token": "562047", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 5,    "tick_size": 0.05,   "precision": 2},
            "LEAD":         {"token": "562049", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 5,    "tick_size": 0.05,   "precision": 2},
            "NICKEL":       {"token": "562051", "exchange": "MCX", "segment": "mcx_fo", "lot_size": 250,  "tick_size": 0.1,    "precision": 2},
            # Crypto — not carried by Kotak Neo; no live feed for these.
            "BTCUSD":       {"token": "btc",    "exchange": "CRYPTO", "segment": "spot", "lot_size": 1,   "tick_size": 0.01,   "precision": 2},
            "ETHUSD":       {"token": "eth",    "exchange": "CRYPTO", "segment": "spot", "lot_size": 1,   "tick_size": 0.01,   "precision": 2},
        }
        # Auto-load previously resolved tokens from the instruments cache (fast path).
        # This means the Nifty 200 tokens are available immediately on day-2+ restarts
        # without waiting for the broker to log in and re-fetch the scrip master.
        _cache = pathlib.Path(__file__).resolve().parent.parent / "config" / "instruments.json"
        n = self._load_instruments_cache(_cache)
        if n:
            import logging
            logging.getLogger("instrument_manager").info(
                f"Loaded {n} instrument tokens from cache ({_cache.name})"
            )

        # Auto-load MCX options cache
        self._mcx_options: Dict[str, List[Dict[str, Any]]] = {}
        _mcx_cache = pathlib.Path(__file__).resolve().parent.parent / "config" / "mcx_options.json"
        if _mcx_cache.exists():
            try:
                with open(_mcx_cache, "r") as f:
                    self._mcx_options = json.load(f)
                import logging
                logging.getLogger("instrument_manager").info(
                    f"Loaded {sum(len(v) for v in self._mcx_options.values())} MCX options from cache ({_mcx_cache.name})"
                )
            except Exception as exc:
                import logging
                logging.getLogger("instrument_manager").warning(
                    f"Could not load MCX options cache {_mcx_cache}: {exc}"
                )


    def register(
        self,
        symbol:    str,
        token:     str,
        exchange:  str,
        segment:   str,
        lot_size:  int   = 1,
        tick_size: float = 0.01,
        precision: int   = 2,
    ) -> None:
        self._instruments[symbol] = {
            "token": token, "exchange": exchange, "segment": segment,
            "lot_size": lot_size, "tick_size": tick_size, "precision": precision,
        }

    def get(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._instruments.get(symbol)

    def get_token(self, symbol: str) -> Optional[str]:
        inst = self._instruments.get(symbol)
        return inst["token"] if inst else None

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        return self._instruments.copy()

    def get_by_token(self, token: str) -> Optional[str]:
        """Reverse lookup: broker token → canonical symbol name."""
        for sym, meta in self._instruments.items():
            if meta["token"] == token:
                return sym
        return None

    def _load_instruments_cache(self, cache_path: pathlib.Path) -> int:
        """
        Loads instrument tokens from a JSON cache file produced by
        load_from_scrip_master().  Only registers symbols that are NOT
        already present in the hardcoded dict (so hardcoded entries always
        take precedence).  Returns the number of new entries loaded.
        """
        if not cache_path.exists():
            return 0
        try:
            with open(cache_path, "r") as f:
                cached: Dict[str, Dict[str, Any]] = json.load(f)
            added = 0
            for sym, meta in cached.items():
                if sym not in self._instruments and meta.get("token"):
                    self._instruments[sym] = meta
                    added += 1
            return added
        except Exception as exc:
            import logging
            logging.getLogger("instrument_manager").warning(
                f"Could not load instruments cache {cache_path}: {exc}"
            )
            return 0

    async def load_from_scrip_master(
        self,
        client: Any,                   # authenticated neo_api_client.NeoAPI instance
        symbols_path: pathlib.Path,    # path to config/symbols.json (the 200-name list)
        cache_path:   pathlib.Path,    # path to write/read config/instruments.json
    ) -> int:
        """
        Fetches Kotak Neo's NSE CM scrip master (after login), resolves tokens
        for all symbols listed in symbols_path, registers them in
        InstrumentManager, and caches the result to cache_path.

        Returns the count of newly registered instruments.
        """
        import logging
        log = logging.getLogger("instrument_manager")

        # Load target symbol names
        if not symbols_path.exists():
            log.warning(f"symbols.json not found at {symbols_path}")
            return 0
        with open(symbols_path) as f:
            target_symbols: set = set(json.load(f).keys())

        # Ask SDK for the CSV file URL (requires edit_token set by 2FA login)
        try:
            csv_url = await asyncio.to_thread(client.scrip_master, "nse_cm")
        except Exception as exc:
            log.error(f"scrip_master() call failed: {exc}")
            return 0

        if not isinstance(csv_url, str) or not csv_url.startswith("http"):
            log.error(f"scrip_master() returned unexpected value: {csv_url!r}")
            return 0

        log.info(f"Downloading NSE CM scrip master from: {csv_url}")

        # Download the CSV
        try:
            import urllib.request
            raw_bytes = await asyncio.to_thread(
                lambda: urllib.request.urlopen(csv_url, timeout=30).read()
            )
            raw_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            log.error(f"Failed to download scrip master CSV: {exc}")
            return 0

        # Parse CSV and extract matching symbols (EQ series only)
        found: Dict[str, Dict[str, Any]] = {}
        try:
            reader = csv.DictReader(io.StringIO(raw_text))
            for row in reader:
                # Kotak's transformed CSV columns (verified from live file):
                #   pSymbolName = Standard symbol name (e.g. "RELIANCE")
                #   pTrdSymbol  = NSE trading symbol (e.g. "RELIANCE-EQ")
                #   pSymbol     = instrument token   (e.g. "2885")
                #   pGroup      = series / group     (e.g. "EQ", "BE", "SM" — empty for indices)
                #   pExchSeg    = exchange segment   (e.g. "nse_cm")
                sym_name = str(row.get("pSymbolName") or "").strip().upper()
                trd_sym  = str(row.get("pTrdSymbol") or "").strip().upper()
                token    = str(row.get("pSymbol")    or "").strip()
                series   = str(row.get("pGroup")     or "").strip().upper()
                seg      = str(row.get("pExchSeg")   or "").strip().lower()

                # Determine the lookup symbol key
                sym = sym_name
                if not sym:
                    if trd_sym.endswith("-EQ"):
                        sym = trd_sym[:-3]
                    else:
                        sym = trd_sym

                if sym in target_symbols and seg == "nse_cm" and series == "EQ" and token:
                    found[sym] = {
                        "token":    token,
                        "exchange": "NSE",
                        "segment":  "nse_cm",
                        "lot_size":  1,
                        "tick_size": 0.05,
                        "precision": 2,
                    }
        except Exception as exc:
            log.error(f"Error parsing scrip master CSV: {exc}")
            return 0

        not_found = sorted(target_symbols - set(found.keys()))
        log.info(f"Scrip master: found {len(found)} tokens, missing {len(not_found)}")
        if not_found:
            log.warning(f"No token found for: {not_found}")

        # Register each found symbol (skip if already hardcoded)
        added = 0
        for sym, meta in found.items():
            if sym not in self._instruments:
                self._instruments[sym] = meta
                added += 1
            else:
                # Update token if hardcoded entry was token-less
                if not self._instruments[sym].get("token"):
                    self._instruments[sym].update(meta)
                    added += 1

        # Persist the discovered tokens for fast reuse on next boot
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(found, f, indent=4)
            log.info(f"Instrument token cache saved → {cache_path} ({len(found)} entries)")
        except Exception as exc:
            log.warning(f"Could not write instrument cache: {exc}")

        return added

    async def load_mcx_options_from_scrip_master(
        self,
        client: Any,
        cache_path: pathlib.Path,
    ) -> int:
        """
        Downloads Kotak Neo's MCX FO scrip master, extracts all option contracts
        for target commodities, and caches them in config/mcx_options.json.
        """
        import logging
        log = logging.getLogger("instrument_manager")

        try:
            csv_url = await asyncio.to_thread(client.scrip_master, "mcx_fo")
        except Exception as exc:
            log.error(f"MCX scrip_master() call failed: {exc}")
            return 0

        if not isinstance(csv_url, str) or not csv_url.startswith("http"):
            log.error(f"MCX scrip_master() returned unexpected value: {csv_url!r}")
            return 0

        log.info(f"Downloading MCX FO scrip master from: {csv_url}")

        try:
            import urllib.request
            raw_bytes = await asyncio.to_thread(
                lambda: urllib.request.urlopen(csv_url, timeout=30).read()
            )
            raw_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            log.error(f"Failed to download MCX scrip master CSV: {exc}")
            return 0

        found: Dict[str, List[Dict[str, Any]]] = {}
        targets = {"GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}
        try:
            reader = csv.DictReader(io.StringIO(raw_text))
            for row in reader:
                sym_name = str(row.get("pSymbolName") or "").strip().upper()
                inst_type = str(row.get("pInstType") or "").strip().upper()
                
                if sym_name in targets and "OPT" in inst_type:
                    token = str(row.get("pSymbol") or "").strip()
                    trd_sym = str(row.get("pTrdSymbol") or "").strip().upper()
                    expiry = str(row.get("pExpiryDate") or row.get("lExpiryDate ") or "").strip()
                    strike_key = "dStrikePrice;" if "dStrikePrice;" in row else "dStrikePrice"
                    strike = float(row.get(strike_key) or 0.0)
                    
                    # Convert internal strike multiplier (e.g. 765000.00 -> 7650.00)
                    # Strike prices in Kotak Neo CSV for MCX Option are divided by 100 for standard value
                    if sym_name == "CRUDEOIL" or sym_name == "GOLD" or sym_name == "SILVER":
                        strike = strike / 100.0
                    elif sym_name == "NATURALGAS":
                        strike = strike / 100.0 # Standard multiplier check
                        
                    if sym_name not in found:
                        found[sym_name] = []
                    found[sym_name].append({
                        "symbol": trd_sym,
                        "token": token,
                        "strike": strike,
                        "expiry": expiry,
                        "inst_type": inst_type,
                        "lot_size": int(row.get("lLotSize") or row.get("iBoardLotQty ") or 1),
                        "tick_size": float(row.get("dTickSize ") or 0.05),
                    })
        except Exception as exc:
            log.error(f"Error parsing MCX scrip master: {exc}")
            return 0

        # Save to cache
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(found, f, indent=4)
            self._mcx_options = found
            log.info(f"MCX options token cache saved → {cache_path} ({sum(len(v) for v in found.values())} options)")
            return sum(len(v) for v in found.values())
        except Exception as exc:
            log.warning(f"Could not write MCX options cache: {exc}")
            return 0

