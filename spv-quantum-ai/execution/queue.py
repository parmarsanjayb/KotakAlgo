import asyncio
from typing import Callable, Coroutine, Any, Optional
from execution.models import ExecutionOrder
from core.logging import get_logger

logger = get_logger("execution_queue")

class ExecutionQueue:
    """
    Asynchronous Execution Queue handling FIFO processing of orders.
    """
    def __init__(self) -> None:
        self._queue: asyncio.Queue[ExecutionOrder] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        self._process_cb: Optional[Callable[[ExecutionOrder], Coroutine[Any, Any, None]]] = None

    def set_callback(self, callback: Callable[[ExecutionOrder], Coroutine[Any, Any, None]]) -> None:
        self._process_cb = callback

    async def start(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            if getattr(self, "_current_loop", None) != loop:
                self._current_loop = loop
                self._running = False
                self._queue = asyncio.Queue()
        except RuntimeError:
            pass

        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._process_loop())
        logger.info("ExecutionQueue worker loop started.")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("ExecutionQueue worker loop stopped.")

    async def enqueue(self, order: ExecutionOrder) -> None:
        await self._queue.put(order)
        logger.info(f"Order enqueued: {order.order_id}")

    async def get_size(self) -> int:
        return self._queue.qsize()

    async def _process_loop(self) -> None:
        while self._running:
            try:
                order = await self._queue.get()
                if self._process_cb:
                    # Invoke processing callback (ExecutionEngine dispatch)
                    await self._process_cb(order)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ExecutionQueue loop: {e}")
                await asyncio.sleep(0.5)
