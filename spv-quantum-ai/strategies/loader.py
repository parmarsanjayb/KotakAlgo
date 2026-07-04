import os
import yaml
from typing import Dict, List, Optional
from strategies.models import Strategy
from core.logging import get_logger

logger = get_logger("strategy_loader")

class StrategyRegistry:
    """
    In-memory registry of loaded strategies.
    Supports enable/disable toggle.
    """
    def __init__(self) -> None:
        # name -> Strategy
        self._strategies: Dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        self._strategies[strategy.name] = strategy
        logger.info(f"Registered strategy: {strategy.name} v{strategy.version} (Enabled: {strategy.enabled})")

    def unregister(self, name: str) -> None:
        if name in self._strategies:
            del self._strategies[name]
            logger.info(f"Unregistered strategy: {name}")

    def get_strategy(self, name: str) -> Optional[Strategy]:
        return self._strategies.get(name)

    def get_all(self) -> List[Strategy]:
        return list(self._strategies.values())

    def get_active(self) -> List[Strategy]:
        return [s for s in self._strategies.values() if s.enabled]

    def set_enabled(self, name: str, enabled: bool) -> bool:
        strat = self._strategies.get(name)
        if strat:
            strat.enabled = enabled
            logger.info(f"Strategy {name} enabled state set to {enabled}")
            return True
        return False


class StrategyLoader:
    """
    Loads and parses YAML strategy files.
    Supports hot-reloading from directories.
    """
    def __init__(self, registry: StrategyRegistry, directory: str = "config/strategies") -> None:
        self.registry = registry
        self.directory = directory
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        if not os.path.exists(self.directory):
            os.makedirs(self.directory)
            logger.info(f"Created strategies configuration directory: {self.directory}")
            self._write_sample_strategy()

    def _write_sample_strategy(self) -> None:
        sample_path = os.path.join(self.directory, "sample_trend_strategy.yaml")
        sample_data = {
            "name": "sample_trend_strategy",
            "version": "1.0.0",
            "description": "Golden Cross with RSI support in Bullish Regime",
            "enabled": True,
            "rules": {
                "operator": "AND",
                "conditions": [
                    {
                        "source": "indicator",
                        "key": "EMA_9",
                        "operator": "crosses_above",
                        "target": "EMA_20"
                    },
                    {
                        "source": "indicator",
                        "key": "RSI",
                        "operator": ">",
                        "value": 50.0
                    },
                    {
                        "source": "market_regime",
                        "operator": "==",
                        "value": "TRENDING_BULLISH"
                    }
                ]
            },
            "actions": {
                "matched": {
                    "action": "SIGNAL_BUY",
                    "confidence": 85.0,
                    "reason": "EMA_9 crossed above EMA_20, RSI is above 50, and Market is Trending Bullish."
                }
            }
        }
        try:
            with open(sample_path, "w") as f:
                yaml.safe_dump(sample_data, f, default_flow_style=False)
            logger.info(f"Wrote sample strategy configuration to {sample_path}")
        except Exception as e:
            logger.error(f"Failed to write sample strategy: {e}")

    def load_all(self) -> None:
        """Loads all YAML files from the target directory into the registry."""
        if not os.path.exists(self.directory):
            return

        loaded_names = []
        for filename in os.listdir(self.directory):
            if filename.endswith(".yaml") or filename.endswith(".yml"):
                filepath = os.path.join(self.directory, filename)
                try:
                    with open(filepath, "r") as f:
                        data = yaml.safe_load(f)
                    
                    if not isinstance(data, dict):
                        logger.warning(f"Invalid strategy format in file {filename}")
                        continue
                    
                    strategy = Strategy(**data)
                    self.registry.register(strategy)
                    loaded_names.append(strategy.name)
                except Exception as e:
                    logger.error(f"Failed to load strategy from {filename}", error=str(e))

        # Unregister strategies that are no longer present on disk
        for name in list(self.registry._strategies.keys()):
            if name not in loaded_names:
                self.registry.unregister(name)

    def hot_reload(self) -> None:
        """Hot-reloads all strategies from disk."""
        logger.info("Triggering strategy hot-reload...")
        self.load_all()
