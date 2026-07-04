from typing import Optional
from core.config import settings

class BrokerResolver:
    """Resolves the configured active broker name."""
    @staticmethod
    def resolve_active_name() -> str:
        return settings.yaml_config.get("brokers", {}).get("active", "paper_broker")
