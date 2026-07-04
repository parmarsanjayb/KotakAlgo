from typing import Any, Dict, Optional, Set

class SymbolRegistry:
    """
    Central catalogue of all symbols that the Market Data Engine is tracking.
    No broker or agent may subscribe to data without first registering here.
    """

    def __init__(self) -> None:
        # Default universe
        self._symbols: Set[str] = {"NIFTY50", "BANKNIFTY", "BTCUSD", "ETHUSD"}
        self._meta: Dict[str, Dict[str, Any]] = {
            "NIFTY50":    {"exchange": "NSE", "segment": "INDEX", "lot_size": 50,  "tick_size": 0.05},
            "BANKNIFTY":  {"exchange": "NSE", "segment": "INDEX", "lot_size": 15,  "tick_size": 0.05},
            "BTCUSD":     {"exchange": "CRYPTO", "segment": "SPOT", "lot_size": 1, "tick_size": 0.01},
            "ETHUSD":     {"exchange": "CRYPTO", "segment": "SPOT", "lot_size": 1, "tick_size": 0.01},
        }

    def register(self, symbol: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self._symbols.add(symbol)
        if meta:
            self._meta[symbol] = meta

    def unregister(self, symbol: str) -> None:
        self._symbols.discard(symbol)
        self._meta.pop(symbol, None)

    def is_registered(self, symbol: str) -> bool:
        return symbol in self._symbols

    def get_symbols(self) -> Set[str]:
        return self._symbols.copy()

    def get_meta(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._meta.get(symbol)

    def get_all_meta(self) -> Dict[str, Dict[str, Any]]:
        return self._meta.copy()
