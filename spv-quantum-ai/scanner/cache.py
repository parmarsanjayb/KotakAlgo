import asyncio
from typing import Dict, List, Optional
from scanner.models import ScanResult

class ScannerCache:
    """
    Thread-safe in-memory cache for scanner matching opportunities.
    """
    def __init__(self) -> None:
        # scanner_name -> Dict[symbol, ScanResult]
        self._matches: Dict[str, Dict[str, ScanResult]] = {}
        self._lock = asyncio.Lock()

    async def store(self, result: ScanResult) -> None:
        async with self._lock:
            scanner_dict = self._matches.setdefault(result.scanner_name, {})
            scanner_dict[result.symbol] = result

    async def clear_scanner(self, scanner_name: str) -> None:
        async with self._lock:
            if scanner_name in self._matches:
                self._matches[scanner_name].clear()

    async def get_matches(self, scanner_name: str) -> List[ScanResult]:
        async with self._lock:
            return list(self._matches.get(scanner_name, {}).values())

    async def get_all_matches(self) -> Dict[str, List[ScanResult]]:
        async with self._lock:
            return {k: list(v.values()) for k, v in self._matches.items()}
