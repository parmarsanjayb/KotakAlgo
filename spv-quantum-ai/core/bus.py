import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union
from pydantic import BaseModel, Field
from core.logging import get_logger

logger = get_logger("event_bus")

class EventModel(BaseModel):
    """Pydantic model representing standard SPV Quantum AI Event."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: Optional[str] = Field(default=None, description="Propagated trace ID linking related events across the pipeline")
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_agent: str
    destination_agent: Optional[str] = None
    priority: int = 2  # Lower value = higher priority. e.g., 0: Critical, 1: High, 2: Normal, 3: Low
    payload: Dict[str, Any]
    status: str = "PENDING"

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, EventModel):
            return NotImplemented
        return self.priority < other.priority

# Type definition for event subscribers
EventCallback = Callable[[EventModel], Coroutine[Any, Any, None]]

class EventBus:
    """Production-ready asynchronous Event Bus using a Priority Queue."""
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventCallback]] = {}
        self._global_subscribers: List[EventCallback] = []
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running: bool = False
        self._ref_count: int = 0
        self._current_loop: Any = None
        # Track in-flight dispatch tasks to prevent leaks on shutdown
        self._inflight_tasks: set = set()

    def start(self) -> None:
        """Starts the background queue processing worker loop."""
        try:
            loop = asyncio.get_running_loop()
            if self._current_loop != loop:
                self._current_loop = loop
                self._ref_count = 0
                self._is_running = False
        except RuntimeError:
            pass

        self._ref_count += 1
        if self._is_running:
            return
        # Re-create queue to bind it to the currently running asyncio event loop
        self._queue = asyncio.PriorityQueue()
        self._is_running = True
        self._worker_task = asyncio.create_task(self._process_queue_loop())
        logger.info("EventBus priority queue worker loop started.")

    async def stop(self) -> None:
        """Stops the background queue processing worker loop cleanly."""
        if self._ref_count > 0:
            self._ref_count -= 1
        if self._ref_count > 0:
            return  # keep running for other active users/tests
            
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        # Await any in-flight dispatch tasks so callbacks are not orphaned
        if self._inflight_tasks:
            await asyncio.gather(*self._inflight_tasks, return_exceptions=True)
            self._inflight_tasks.clear()
        logger.info("EventBus priority queue worker loop stopped.")

    async def subscribe(self, event_type: str, callback: EventCallback) -> None:
        """Subscribes an async callback to a specific event type."""
        async with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)
                cb_name = getattr(callback, "__name__", str(callback))
                logger.debug("Subscribed callback", event_type=event_type, callback=cb_name)

    async def unsubscribe(self, event_type: str, callback: EventCallback) -> None:
        """Unsubscribes an async callback from an event type."""
        async with self._lock:
            if event_type in self._subscribers and callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
                cb_name = getattr(callback, "__name__", str(callback))
                logger.debug("Unsubscribed callback", event_type=event_type, callback=cb_name)

    async def subscribe_all(self, callback: EventCallback) -> None:
        """Registers a global callback to receive all events (broadcasting/logging)."""
        async with self._lock:
            if callback not in self._global_subscribers:
                self._global_subscribers.append(callback)
                logger.debug("Subscribed global callback")

    async def unsubscribe_all(self, callback: EventCallback) -> None:
        """Removes a global callback."""
        async with self._lock:
            if callback in self._global_subscribers:
                self._global_subscribers.remove(callback)
                logger.debug("Unsubscribed global callback")

    async def publish(
        self,
        event_or_type: Union[EventModel, str],
        sender: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        destination: Optional[str] = None,
        priority: int = 2
    ) -> None:
        """
        Publishes an event to the priority queue.
        Accepts EventModel directly or arguments to construct one.
        """
        if isinstance(event_or_type, EventModel):
            event = event_or_type
        else:
            event = EventModel(
                event_type=event_or_type,
                source_agent=sender or "system",
                destination_agent=destination,
                priority=priority,
                payload=payload or {}
            )
        
        await self._queue.put((event.priority, event))
        logger.debug("Enqueued event", event_type=event.event_type, priority=event.priority)

    async def broadcast(self, event: EventModel) -> None:
        """Broadcasts an event with top priority, skipping normal queue sorting."""
        event.priority = 0  # Max priority
        await self.publish(event)

    async def _process_queue_loop(self) -> None:
        """Internal background loop polling items out of priority queue."""
        while self._is_running:
            try:
                priority, event = await self._queue.get()
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in priority queue dispatch loop", error=str(e))
                await asyncio.sleep(0.1)

    async def _dispatch(self, event: EventModel) -> None:
        """Invokes registered callbacks for matching event type."""
        event.status = "PROCESSED"
        async with self._lock:
            targets = list(self._subscribers.get(event.event_type, []))
            globals_copy = list(self._global_subscribers)

        # Distribute concurrently, tracking tasks to prevent leaks
        for callback in (targets + globals_copy):
            task = asyncio.create_task(self._safe_invoke(callback, event))
            self._inflight_tasks.add(task)
            task.add_done_callback(self._inflight_tasks.discard)

    async def _safe_invoke(self, callback: EventCallback, event: EventModel) -> None:
        """Traps subscriber callback failures to preserve queue processing integrity."""
        try:
            await callback(event)
        except Exception as e:
            cb_name = getattr(callback, "__name__", str(callback))
            logger.error(
                "Subscriber execution error",
                event_type=event.event_type,
                callback=cb_name,
                error=str(e),
                source=event.source_agent
            )
            event.status = "FAILED"

# Singleton instance
event_bus = EventBus()
