import logging
import json
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Any

class LoggingManager:
    """Manages structured file logging and rotation for critical categories."""
    def __init__(self, log_dir: str = "logs", max_bytes: int = 5 * 1024 * 1024, backup_count: int = 5) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.loggers: Dict[str, logging.Logger] = {}

        # Initialize logging categories
        self._setup_category("error", "error.log")
        self._setup_category("audit", "audit.log")
        self._setup_category("trading", "trading.log")
        self._setup_category("performance", "performance.log")

    def _setup_category(self, category: str, filename: str) -> None:
        logger = logging.getLogger(f"health.{category}")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Clear existing handlers
        if logger.handlers:
            logger.handlers.clear()

        log_path = self.log_dir / filename
        handler = RotatingFileHandler(
            log_path, maxBytes=self.max_bytes, backupCount=self.backup_count, encoding="utf-8"
        )
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        self.loggers[category] = logger

    def log(self, category: str, message: str, level: str = "INFO", **kwargs: Any) -> None:
        """Logs a structured JSON message into the specified log file category."""
        logger = self.loggers.get(category)
        if not logger:
            return

        payload = {
            "message": message,
            "metadata": kwargs
        }
        log_str = json.dumps(payload)

        if level.upper() == "ERROR":
            logger.error(log_str)
        elif level.upper() == "WARNING":
            logger.warning(log_str)
        else:
            logger.info(log_str)
