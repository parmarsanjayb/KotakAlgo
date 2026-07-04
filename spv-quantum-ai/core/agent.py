from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timezone
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Union
import psutil
from pydantic import BaseModel, Field
from core.bus import EventModel, event_bus
from core.logging import get_logger, StructuredLogger

class AgentResultModel(BaseModel):
    """Pydantic model representing standard SPV Quantum AI Agent execution output."""
    agent_name: str
    signal: str  # BUY, SELL, HOLD, NONE
    confidence: float
    reason: str
    processing_time: float  # ms
    metadata: Dict[str, Any] = Field(default_factory=dict)

class BaseAgent(ABC):
    """
    Common Base Class for all AI Agents in SPV Quantum AI.
    Handles framework lifecycles, execution telemetry, and middleware logging.
    """
    def __init__(self, name: str, description: str = "", priority: int = 2) -> None:
        self.agent_id: str = str(uuid.uuid4())
        self.agent_name: str = name
        self.description: str = description
        self.status: str = "IDLE"  # IDLE, RUNNING, FAILED, STOPPED
        self.confidence_score: float = 0.0
        self.enabled: bool = True
        self.priority: int = priority
        self.created_at: datetime = datetime.now(timezone.utc)
        self.last_execution: Optional[datetime] = None
        self.execution_time: float = 0.0  # Last execution duration in ms
        self.logger: StructuredLogger = get_logger(f"agent.{name}")
        self.logs: List[str] = []
        self.last_decision: Optional[AgentResultModel] = None

        # System resources hook
        self._process = psutil.Process(os.getpid())
        self._background_task: Optional[asyncio.Task] = None

    @abstractmethod
    async def initialize(self) -> None:
        """Pre-start configurations and database setups. Override in subclasses."""
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Teardown connections and database resources. Override in subclasses."""
        pass

    async def start(self) -> None:
        """Attaches subscriptions and initializes background loops."""
        if not self.enabled:
            self.status = "STOPPED"
            self.log_info("Agent is disabled. Skipping start.")
            return

        await self.initialize()
        self.status = "RUNNING"

        # Register event bus listeners
        for event_type in self.input_event_types:
            await event_bus.subscribe(event_type, self.receive_event)
            self.log_info(f"Subscribed callback to event type: {event_type}")

        # Start background loop if custom tick is configured
        if self.get_tick_interval() > 0:
            self._background_task = asyncio.create_task(self._run_loop())

        self.log_info("Agent started successfully.")

    async def stop(self) -> None:
        """Removes event subscriptions and shuts down loops cleanly."""
        self.status = "STOPPED"

        # Unsubscribe callbacks
        for event_type in self.input_event_types:
            await event_bus.unsubscribe(event_type, self.receive_event)
            self.log_info(f"Unsubscribed from event type: {event_type}")

        # Terminate ticker task
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
            self._background_task = None

        await self.shutdown()
        self.log_info("Agent stopped.")

    async def receive_event(self, event: EventModel) -> None:
        """
        Log Middleware wrapper around analyze execution.
        Saves CPU, Memory delta, and processing execution speed metrics.
        """
        if not self.enabled or (self.status != "RUNNING" and self.status != "IDLE"):
            return

        # Direct addressing verification
        if event.destination_agent and event.destination_agent != self.agent_name:
            return

        start_time = time.perf_counter()
        self.last_execution = datetime.now(timezone.utc)

        # Telemetry before
        try:
            mem_before = self._process.memory_info().rss / (1024 * 1024)
            cpu_before = self._process.cpu_percent(interval=None)
        except Exception:
            mem_before, cpu_before = 0.0, 0.0

        try:
            # Invoke concrete analysis
            result = await self.analyze(event)

            if result is not None:
                if not isinstance(result, AgentResultModel):
                    raise ValueError(f"Agent analyze() must return AgentResultModel, got {type(result)}")
                
                self.last_decision = result
                self.confidence_score = result.confidence
        except Exception as e:
            self.status = "FAILED"
            self.log_error(f"Execution crash during analysis: {str(e)}")
            await event_bus.publish(
                EventModel(
                    event_type="system_status",
                    source_agent=self.agent_name,
                    payload={"event_type": "AGENT_CRASH", "error": str(e)},
                    priority=0
                )
            )
            return

        # Telemetry after
        end_time = time.perf_counter()
        self.execution_time = round((end_time - start_time) * 1000.0, 3)

        try:
            mem_after = self._process.memory_info().rss / (1024 * 1024)
            cpu_after = self._process.cpu_percent(interval=None)
            mem_usage = round(mem_after - mem_before, 4)
            cpu_usage = round(cpu_after, 2)
        except Exception:
            mem_usage, cpu_usage = 0.0, 0.0

        # Middleware log prints
        self.log_info(
            f"Executed Event type '{event.event_type}' in {self.execution_time}ms | "
            f"CPU: {cpu_usage}% | Memory Delta: {mem_usage}MB"
        )

    @abstractmethod
    async def analyze(self, event: EventModel) -> Optional[AgentResultModel]:
        """
        Executes trading assessments or indicator parsing.
        Must return AgentResultModel or None.
        """
        pass

    async def publish_result(self, event_or_type: Union[EventModel, str], payload: Optional[Dict[str, Any]] = None, priority: int = 2) -> None:
        """Publishes payload results back onto the event bus."""
        if isinstance(event_or_type, EventModel):
            await event_bus.publish(event_or_type)
        else:
            if event_or_type not in self.output_event_types:
                self.log_warning(f"Publishing to event type '{event_or_type}' which is not listed in output_event_types.")
            await event_bus.publish(
                event_or_type,
                sender=self.agent_name,
                payload=payload,
                priority=priority
            )

    async def health_check(self) -> str:
        """Evaluates health conditions. Returns HEALTHY, DEGRADED, or UNHEALTHY."""
        if self.status == "FAILED":
            return "UNHEALTHY"
        return "HEALTHY"

    @property
    @abstractmethod
    def input_event_types(self) -> List[str]:
        """Event types subscribed to on the bus."""
        pass

    @property
    @abstractmethod
    def output_event_types(self) -> List[str]:
        """Event types published to the bus."""
        pass

    def get_tick_interval(self) -> float:
        """Ticks in seconds for periodic heartbeats. Defaults to 0 (no ticking)."""
        return 0.0

    async def on_tick(self) -> None:
        """Ticks callback. Override in subclass."""
        pass

    async def _run_loop(self) -> None:
        """Asynchronous execution ticker loop."""
        try:
            while self.status == "RUNNING" and self.enabled:
                await self.on_tick()
                await asyncio.sleep(self.get_tick_interval())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.status = "FAILED"
            self.log_error(f"Ticker execution crash: {e}")

    # Local logger helpers
    def _add_log(self, level: str, message: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        entry = f"[{timestamp}] [{level:7s}] {message}"
        self.logs.append(entry)
        if len(self.logs) > 200:
            self.logs.pop(0)

    def log_info(self, message: str, **kwargs: Any) -> None:
        self._add_log("INFO", message)
        self.logger.info(message, **kwargs)

    def log_warning(self, message: str, **kwargs: Any) -> None:
        self._add_log("WARNING", message)
        self.logger.warning(message, **kwargs)

    def log_error(self, message: str, **kwargs: Any) -> None:
        self._add_log("ERROR", message)
        self.logger.error(message, **kwargs)
