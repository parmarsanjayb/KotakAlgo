import os
import yaml
from typing import Dict, List, Optional
from scanner.models import ScannerConfig
from core.logging import get_logger

logger = get_logger("scanner_registry")

class ScannerRegistry:
    """
    Manages active scanner configurations loaded from disk/YAML.
    """
    def __init__(self, directory: str = "config/scanners") -> None:
        self.directory = directory
        self._scanners: Dict[str, ScannerConfig] = {}
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        if not os.path.exists(self.directory):
            os.makedirs(self.directory)
            logger.info(f"Created scanner configurations directory: {self.directory}")
            self._write_sample_configs()

    def _write_sample_configs(self) -> None:
        # Sample 1: Volume Spike
        vol_path = os.path.join(self.directory, "volume_spike.yaml")
        vol_data = {
            "name": "VolumeSpikeScanner",
            "enabled": True,
            "segment": "Equity",
            "filter_type": "VolumeSpike",
            "params": {"volume_multiplier": 2.5},
            "priority": 1
        }
        # Sample 2: Price Breakout
        brk_path = os.path.join(self.directory, "price_breakout.yaml")
        brk_data = {
            "name": "PriceBreakoutScanner",
            "enabled": True,
            "segment": "Equity",
            "filter_type": "PriceBreakout",
            "params": {"bandwidth_threshold": 4.0},
            "priority": 2
        }
        try:
            with open(vol_path, "w") as f:
                yaml.safe_dump(vol_data, f)
            with open(brk_path, "w") as f:
                yaml.safe_dump(brk_data, f)
            logger.info("Wrote sample scanner configs.")
        except Exception as e:
            logger.error(f"Failed to write sample scanner configs: {e}")

    def load_all(self) -> None:
        if not os.path.exists(self.directory):
            return

        loaded_names = []
        for filename in os.listdir(self.directory):
            if filename.endswith(".yaml") or filename.endswith(".yml"):
                filepath = os.path.join(self.directory, filename)
                try:
                    with open(filepath, "r") as f:
                        data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        cfg = ScannerConfig(**data)
                        self._scanners[cfg.name] = cfg
                        loaded_names.append(cfg.name)
                except Exception as e:
                    logger.error(f"Failed to load scanner from {filename}", error=str(e))

        # Remove deleted configs
        for name in list(self._scanners.keys()):
            if name not in loaded_names:
                del self._scanners[name]

    def hot_reload(self) -> None:
        self.load_all()

    def set_enabled(self, name: str, enabled: bool) -> bool:
        cfg = self._scanners.get(name)
        if cfg:
            cfg.enabled = enabled
            return True
        return False

    def get_scanner(self, name: str) -> Optional[ScannerConfig]:
        return self._scanners.get(name)

    def get_active(self) -> List[ScannerConfig]:
        return [c for c in self._scanners.values() if c.enabled]

    def get_all(self) -> List[ScannerConfig]:
        return list(self._scanners.values())
