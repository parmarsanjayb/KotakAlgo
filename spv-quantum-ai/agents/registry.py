import importlib
import inspect
import re
from pathlib import Path
from typing import Dict, List, Optional, Type
from core.agent import BaseAgent
from core.logging import get_logger

logger = get_logger("agent_registry")

def camel_to_snake(name: str) -> str:
    """Converts a PascalCase string to snake_case."""
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

class AgentRegistry:
    """
    Automated discovery system for AI Agent plugins.
    Converts PascalCase class names to snake_case keys for seamless config matching.
    """
    def __init__(self) -> None:
        self._registered_classes: Dict[str, Type[BaseAgent]] = {}

    def discover_agents(self) -> Dict[str, Type[BaseAgent]]:
        """
        Scans agents/ directory and discovers classes inheriting from BaseAgent.
        Returns:
            Dict mapping snake_case class names to their Type classes.
        """
        logger.info("Scanning agents directory for automatic discovery...")
        agents_dir = Path(__file__).resolve().parent
        
        # Traverse Python files in directory
        for path in agents_dir.glob("*.py"):
            if path.name in ["__init__.py", "manager.py", "registry.py"]:
                continue

            module_name = f"agents.{path.stem}"
            try:
                module = importlib.import_module(module_name)
                
                # Inspect all module attributes
                for name, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and issubclass(obj, BaseAgent)
                        and obj != BaseAgent
                    ):
                        # Convert MarketAgent to market_agent
                        agent_key = camel_to_snake(obj.__name__)
                        self._registered_classes[agent_key] = obj
                        logger.info(
                            "Discovered agent class",
                            module=module_name,
                            class_name=obj.__name__,
                            key=agent_key
                        )
            except Exception as e:
                logger.error("Failed to dynamically load module", module=module_name, error=str(e))

        return self._registered_classes

    def get_agent_class(self, key: str) -> Optional[Type[BaseAgent]]:
        """Retrieves registered Agent class by its snake_case lookup key."""
        return self._registered_classes.get(key.lower())

    def get_registered_keys(self) -> List[str]:
        """Returns list of registered agent lookup keys."""
        return list(self._registered_classes.keys())

# Singleton registry instance
agent_registry = AgentRegistry()
