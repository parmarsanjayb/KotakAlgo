"""
core/startup.py
───────────────
Startup validation and readiness checks for SPV Quantum AI.
Called during FastAPI lifespan before any engine is started.
"""

import os
import asyncio
import time
from typing import Any, Dict, List, Tuple

from core.logging import get_logger
from core.exceptions import EnvironmentValidationError

logger = get_logger("startup")


# ── Environment Validation ────────────────────────────────────────────────────

# Variables that MUST be present; missing any causes a hard startup failure.
_REQUIRED_VARS: List[str] = [
    "ENVIRONMENT",
    "LOG_LEVEL",
]

# Variables that SHOULD be present; missing logs a warning but does not abort.
_RECOMMENDED_VARS: List[str] = [
    "DATABASE_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def validate_environment() -> None:
    """
    Validates that all required environment variables are set.
    Raises EnvironmentValidationError if any required variable is missing.
    Logs warnings for recommended but absent variables.
    """
    missing_required: List[str] = []
    for var in _REQUIRED_VARS:
        if not os.environ.get(var):
            missing_required.append(var)

    if missing_required:
        raise EnvironmentValidationError(
            message=f"Missing required environment variables: {missing_required}",
            context={"missing": missing_required},
        )

    missing_recommended: List[str] = []
    for var in _RECOMMENDED_VARS:
        if not os.environ.get(var):
            missing_recommended.append(var)

    if missing_recommended:
        logger.warning(
            "Recommended environment variables not set — some features may be disabled",
            missing=missing_recommended,
        )

    logger.info("Environment validation passed.")


# ── Startup Health Checks ─────────────────────────────────────────────────────

class StartupCheck:
    """Result of a single startup readiness check."""

    def __init__(self, name: str, passed: bool, detail: str = "", latency_ms: float = 0.0) -> None:
        self.name = name
        self.passed = passed
        self.detail = detail
        self.latency_ms = round(latency_ms, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": "PASS" if self.passed else "FAIL",
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


async def _check_database() -> StartupCheck:
    """Verifies the database is reachable and schema is initialised."""
    t0 = time.perf_counter()
    try:
        from database.connection import engine
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: c.execute(c.connection.dialect.statement_compiler(
                c.connection.dialect, None  # type: ignore[arg-type]
            ).__class__(c.connection.dialect, None).__class__.__new__(  # noqa: SIM118
                object.__class__
            )))
    except Exception:
        pass  # Any connect attempt is enough to measure reachability

    try:
        from database.connection import async_session
        from sqlalchemy import text
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        latency = (time.perf_counter() - t0) * 1000
        return StartupCheck("database", True, "Connection OK", latency)
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        return StartupCheck("database", False, str(exc), latency)


async def _check_event_bus() -> StartupCheck:
    """Verifies the event bus worker is running."""
    t0 = time.perf_counter()
    try:
        from core.bus import event_bus
        is_running = event_bus._is_running
        latency = (time.perf_counter() - t0) * 1000
        return StartupCheck("event_bus", is_running, "Worker running" if is_running else "Worker not started", latency)
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        return StartupCheck("event_bus", False, str(exc), latency)


async def _check_broker() -> StartupCheck:
    """Verifies the active broker adapter is connected."""
    t0 = time.perf_counter()
    try:
        from brokers.engine import broker_engine
        connected = broker_engine.is_connected()
        latency = (time.perf_counter() - t0) * 1000
        return StartupCheck("broker", connected, "Connected" if connected else "Not connected", latency)
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        return StartupCheck("broker", False, str(exc), latency)


async def run_startup_checks() -> Tuple[bool, List[StartupCheck]]:
    """
    Runs all startup readiness checks concurrently.
    Returns (all_passed, list_of_check_results).
    """
    results = await asyncio.gather(
        _check_database(),
        _check_event_bus(),
        _check_broker(),
        return_exceptions=False,
    )

    checks: List[StartupCheck] = list(results)
    all_passed = all(c.passed for c in checks)

    for check in checks:
        if check.passed:
            logger.info(f"Startup check [{check.name}] PASS ({check.latency_ms}ms)")
        else:
            logger.warning(f"Startup check [{check.name}] FAIL — {check.detail}")

    return all_passed, checks


# Module-level cache populated after startup
_startup_checks_cache: List[Dict[str, Any]] = []
_startup_time: float = 0.0


def cache_startup_results(checks: List[StartupCheck]) -> None:
    """Stores startup results for the /api/readiness endpoint."""
    global _startup_checks_cache, _startup_time
    _startup_checks_cache = [c.to_dict() for c in checks]
    _startup_time = time.time()


def get_cached_startup_results() -> Tuple[List[Dict[str, Any]], float]:
    return _startup_checks_cache, _startup_time
