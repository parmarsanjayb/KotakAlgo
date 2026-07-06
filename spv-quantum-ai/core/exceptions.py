"""
core/exceptions.py
──────────────────
Structured Exception Hierarchy for SPV Quantum AI.
Provides typed exceptions for each subsystem so that callers can catch
specific error categories without bare `except Exception` blocks.
"""

from typing import Any, Dict, Optional


# ── Base ─────────────────────────────────────────────────────────────────────

class SPVBaseException(Exception):
    """Root exception for all SPV Quantum AI errors."""

    def __init__(
        self,
        message: str,
        code: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__
        self.context: Dict[str, Any] = context or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "context": self.context,
        }


# ── Configuration ─────────────────────────────────────────────────────────────

class ConfigurationError(SPVBaseException):
    """Raised when required configuration values are missing or invalid."""


class EnvironmentValidationError(ConfigurationError):
    """Raised on startup when critical environment variables are absent."""


# ── Database ──────────────────────────────────────────────────────────────────

class DatabaseError(SPVBaseException):
    """Raised on database connection or query failures."""


class DatabaseTransactionError(DatabaseError):
    """Raised when a database transaction cannot be committed or rolled back."""


# ── Broker ────────────────────────────────────────────────────────────────────

class BrokerException(SPVBaseException):
    """Raised on broker adapter errors."""


class BrokerConnectionError(BrokerException):
    """Raised when broker connection or authentication fails."""


class BrokerSessionExpiredError(BrokerException):
    """Raised when the broker session token has expired."""


class BrokerOrderError(BrokerException):
    """Raised when a broker order placement, modification, or cancellation fails."""


# ── Execution ─────────────────────────────────────────────────────────────────

class ExecutionException(SPVBaseException):
    """Raised by the Execution Engine on pipeline failures."""


class DuplicateOrderError(ExecutionException):
    """Raised when a duplicate order is submitted within the dedup window."""


class OrderValidationError(ExecutionException):
    """Raised when an order fails pre-submission validation."""


# ── Risk ──────────────────────────────────────────────────────────────────────

class RiskException(SPVBaseException):
    """Raised by the Risk Engine on constraint violations."""


class RiskLimitBreached(RiskException):
    """Raised when a risk limit (drawdown, daily loss, exposure) is breached."""


# ── Safety ────────────────────────────────────────────────────────────────────

class SafetyException(SPVBaseException):
    """Raised by the Safety Engine on emergency conditions."""


class KillSwitchActivatedError(SafetyException):
    """Raised when the emergency kill switch is active and blocks trading."""


# ── Market Data ───────────────────────────────────────────────────────────────

class MarketDataError(SPVBaseException):
    """Raised on market data feed failures."""


class StaleDataError(MarketDataError):
    """Raised when market data has not been refreshed within the expected window."""


# ── Strategy ──────────────────────────────────────────────────────────────────

class StrategyException(SPVBaseException):
    """Raised on strategy evaluation errors."""


# ── API / Validation ──────────────────────────────────────────────────────────

class APIValidationError(SPVBaseException):
    """Raised when an API request fails schema or business-rule validation."""


class UnauthorizedError(SPVBaseException):
    """Raised on authentication or authorization failures."""
