import logging
import os
import sys
from datetime import datetime
from typing import Optional

# Whether we've already configured the root logger
_configured = False


def _supports_color() -> bool:
    """Detect if stdout likely supports ANSI colors."""
    if os.getenv("NO_COLOR"):
        return False
    force_color = os.getenv("FORCE_COLOR")
    if force_color and force_color not in ("0", "false", "False"):
        return True
    if os.getenv("TERM") in (None, "dumb"):
        return False
    return sys.stdout.isatty()


def _colorize(message: str, color: str) -> str:
    if not _supports_color():
        return message
    return f"{color}{message}\033[0m"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once."""
    global _configured
    if _configured:
        return

    class _Formatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            level = record.levelname
            msg = record.getMessage()
            if record.levelno >= logging.ERROR:
                level = _colorize(level, "\033[31m")  # red
                msg = _colorize(msg, "\033[31m")
            elif record.levelno >= logging.WARNING:
                level = _colorize(level, "\033[33m")  # yellow
            elif record.levelno == logging.INFO:
                level = _colorize(level, "\033[36m")  # cyan
            return f"[{timestamp}] [{level}] {msg}"

    handler = logging.StreamHandler()
    handler.setFormatter(_Formatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    _configured = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger, ensuring global configuration exists."""
    configure_logging()
    return logging.getLogger(name)


def log_section(title: str, char: str = "#", width: int = 60) -> None:
    """Print a standardized log banner for component start/end."""
    logger = get_logger("section")
    border = char * width
    logger.info("\n%s\n[%s]\n%s\n", border, title, border)


def log_success(message: str) -> None:
    logger = get_logger("success")
    logger.info(_colorize(message, "\033[32m"))


def log_failure(message: str) -> None:
    logger = get_logger("failure")
    logger.error(message)
