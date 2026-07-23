import importlib
import inspect
from typing import Optional
from brokers.base import BaseBroker
from brokers.registry import BrokerRegistry

class BrokerFactory:
    """Dynamically instantiates a broker adapter class."""
    @staticmethod
    def create_broker(name: str, config_data: Optional[dict] = None) -> BaseBroker:
        class_path = BrokerRegistry.get_class_path(name)
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        
        sig = inspect.signature(cls)
        if "config_data" in sig.parameters:
            return cls(config_data=config_data)
        return cls()
