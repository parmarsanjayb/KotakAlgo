from typing import Dict, Any
from core.config import settings
from core.logging import get_logger

logger = get_logger("score_weights")

class WeightManager:
    """
    Manages scoring weights and parameter limits loaded from YAML config.
    """
    def __init__(self) -> None:
        self.config = settings.yaml_config.get("decision_scoring", {})
        
        # Load weights
        self.weights = self.config.get("weights", {
            "market_analysis": 0.30,
            "market_regime": 0.20,
            "strategy_match": 0.25,
            "risk_status": 0.25
        })
        
        # Normalize weights if they don't sum to 1.0
        total_w = sum(self.weights.values())
        if total_w > 0 and abs(total_w - 1.0) > 0.001:
            logger.info("Scoring weights do not sum to 1.0. Normalizing...")
            self.weights = {k: v / total_w for k, v in self.weights.items()}

        # Load parameters
        self.min_confidence_threshold = float(self.config.get("min_confidence_threshold", 60.0))
        self.bonus_aligned_signals = float(self.config.get("bonus_aligned_signals", 5.0))
        self.penalty_conflicting_signals = float(self.config.get("penalty_conflicting_signals", 15.0))

    def get_weight(self, component: str) -> float:
        return float(self.weights.get(component, 0.0))
