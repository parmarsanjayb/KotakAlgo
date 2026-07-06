import logging
import logging.handlers
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from core.config import settings

class JSONFormatter(logging.Formatter):
    """Formats log records as JSON for production ingestion."""
    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Merge extra fields if present
        extra_fields = getattr(record, "extra_fields", {})
        if extra_fields:
            log_data.update(extra_fields)
            
        return json.dumps(log_data)

class ConsoleFormatter(logging.Formatter):
    """Formats log records as readable string for development console."""
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        msg = record.getMessage()
        exc = ""
        if record.exc_info:
            exc = f"\n{self.formatException(record.exc_info)}"
        
        extra_fields = getattr(record, "extra_fields", {})
        extra = ""
        if extra_fields:
            extra = f" | fields={json.dumps(extra_fields)}"
            
        return f"[{timestamp}] [{level:7s}] {record.name}: {msg}{extra}{exc}"

def setup_logging() -> None:
    """Configures the logging system."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any existing default handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    if settings.ENVIRONMENT == "production":
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(ConsoleFormatter())
    console_handler.setLevel(log_level)
    root_logger.addHandler(console_handler)

    # File Handler for persistence
    root_dir = Path(__file__).resolve().parent.parent
    logs_dir = root_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    
    # Rotating File Handler for production persistence (10 MB max, 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "system.log",
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=5,               # keep 5 rotated files → max 50 MB total
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(logging.DEBUG)  # Keep verbose logs in file
    root_logger.addHandler(file_handler)

# Custom wrapper class for logging with additional dynamic parameters
class StructuredLogger:
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def log(self, level: int, msg: str, **kwargs: Any) -> None:
        if self.logger.isEnabledFor(level):
            record = self.logger.makeRecord(
                name=self.logger.name,
                level=level,
                fn="",
                lno=0,
                msg=msg,
                args=(),
                exc_info=None
            )
            # Inject dynamic extra fields
            record.extra_fields = kwargs  # type: ignore
            self.logger.handle(record)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self.log(logging.ERROR, msg, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        if self.logger.isEnabledFor(logging.ERROR):
            record = self.logger.makeRecord(
                name=self.logger.name,
                level=logging.ERROR,
                fn="",
                lno=0,
                msg=msg,
                args=(),
                exc_info=sys.exc_info()
            )
            record.extra_fields = kwargs  # type: ignore
            self.logger.handle(record)

def get_logger(name: str) -> StructuredLogger:
    """Returns a preconfigured StructuredLogger instance."""
    return StructuredLogger(name)

# Initial setup execution
setup_logging()
logger = get_logger("system")
logger.info("Logging initialized", environment=settings.ENVIRONMENT, log_level=settings.LOG_LEVEL)
