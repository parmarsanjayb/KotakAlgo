import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from core.agent import BaseAgent
from core.config import settings
from core.logging import get_logger
from agents.registry import agent_registry, camel_to_snake

logger = get_logger("agent_manager")

class AgentManager:
    """
    Supervisor manager orchestrating the lifecycles, health states,
    and automatic recovery of registered AI agent plugins.
    """
    def __init__(self) -> None:
        self.active_agents: Dict[str, BaseAgent] = {}
        self._is_running: bool = False
        self._monitor_task: Optional[asyncio.Task] = None

    def load_agents(self) -> None:
        """
        Triggers scans across agents directory, discovers classes extending BaseAgent,
        and registers/instantiates active ones.
        """
        logger.info("Initializing Agent registry scans...")
        # Discovery run
        agent_registry.discover_agents()

        # Read configs to instatiate enabled ones
        agents_config = settings.yaml_config.get("agents", {})

        for key, config in agents_config.items():
            # Check registry first
            agent_cls = agent_registry.get_agent_class(key)
            if agent_cls:
                try:
                    # Instantiate subclass
                    agent_instance = agent_cls()
                    # Apply config-driven enabled flag
                    agent_instance.enabled = config.get("enabled", True)
                    
                    self.active_agents[agent_instance.agent_name] = agent_instance
                    logger.info("Registered agent dynamically", agent=agent_instance.agent_name, enabled=agent_instance.enabled)
                except Exception as e:
                    logger.error("Failed to instantiate agent", key=key, error=str(e))
            else:
                logger.error("Agent class type not discovered in catalog", key=key)

    async def start_all(self) -> None:
        """Starts all instantiated agents and spins up health monitor task."""
        self._is_running = True
        logger.info("Starting active agents execution loops...")
        for name, agent in self.active_agents.items():
            if agent.enabled:
                try:
                    await agent.start()
                except Exception as e:
                    logger.exception("Failed starting agent", agent=name, error=str(e))

        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop_all(self) -> None:
        """Stops health supervisor and cleanly stops all running agent callbacks."""
        self._is_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        logger.info("Stopping active agents execution loops...")
        for name, agent in list(self.active_agents.items()):
            try:
                await agent.stop()
            except Exception as e:
                logger.error("Failed cleanly stopping agent", agent=name, error=str(e))
        self.active_agents.clear()

    async def enable_agent(self, name: str) -> bool:
        """Enables a disabled agent and starts its loop."""
        if name in self.active_agents:
            agent = self.active_agents[name]
            if not agent.enabled:
                agent.enabled = True
                await agent.start()
                logger.info("Agent enabled successfully", agent=name)
                return True
        return False

    async def disable_agent(self, name: str) -> bool:
        """Disables an active agent and stops its listener callbacks."""
        if name in self.active_agents:
            agent = self.active_agents[name]
            if agent.enabled:
                agent.enabled = False
                await agent.stop()
                logger.info("Agent disabled successfully", agent=name)
                return True
        return False

    async def _monitor_loop(self) -> None:
        """Supervisor loop checking heartbeats and health states every 5s."""
        logger.info("Supervisor monitor daemon started.")
        while self._is_running:
            try:
                await asyncio.sleep(5)
                await self.audit_health_and_supervise()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Crashed in monitor daemon loop", error=str(e))

    async def audit_health_and_supervise(self) -> None:
        """Pings agent health status and restarts any in FAILED state."""
        for name, agent in list(self.active_agents.items()):
            if not agent.enabled:
                continue

            health = await agent.health_check()
            
            # Restart if health has degraded/failed
            if agent.status == "FAILED" or health == "UNHEALTHY":
                logger.warning(
                    "Crashed or UNHEALTHY agent thread detected. Supervisor triggering restart...",
                    agent=name,
                    status=agent.status,
                    health=health
                )

                class_key = camel_to_snake(agent.__class__.__name__)
                agent_cls = agent_registry.get_agent_class(class_key)
                if not agent_cls:
                    logger.error("Cannot locate class mapping for restart", class_name=class_key)
                    continue

                # Stop old thread
                try:
                    await agent.stop()
                except Exception as e:
                    logger.error("Error stopping failed agent", agent=name, error=str(e))

                # Instantiate clean clone
                try:
                    cloned_agent = agent_cls()
                    cloned_agent.logs.append(
                        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"[SYSTEM] Hot-swapped by supervisor manager."
                    )
                    cloned_agent.enabled = True
                    
                    self.active_agents[name] = cloned_agent
                    await cloned_agent.start()
                    logger.info("Successfully hot-swapped crashed agent thread", agent=name)
                except Exception as e:
                    logger.exception("Failed hot-swapping agent thread", agent=name, error=str(e))
            else:
                # Log heartbeat OK
                logger.debug("Agent heartbeat OK", agent=name, health=health, status=agent.status)
