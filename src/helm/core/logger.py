import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from helm.core.config_manager import get_log_dir

LOG_DIR = get_log_dir()
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "helm.log")

# Migrate old log file if it exists
old_log = os.path.join(os.path.expanduser("~"), ".helm_data", "logs", "helm.log")
if os.path.exists(old_log) and not os.path.exists(LOG_FILE):
    try:
        shutil.move(old_log, LOG_FILE)
    except Exception:
        pass


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "module": record.name,
            "event": record.getMessage(),
        }
        if getattr(record, "error_code", None):
            log_obj["error_code"] = record.error_code
        if record.exc_info:
            log_obj["error_trace"] = self.formatException(record.exc_info)

        # Add any extra fields passed dynamically
        for key, value in record.__dict__.items():
            if key not in [
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
                "error_code",
            ]:
                log_obj[key] = value

        return json.dumps(log_obj)


class PlainTextFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(5)
        module = record.name
        message = record.getMessage()
        formatted = f"[{timestamp}] [{level}] [{module}] - {message}"
        if record.exc_info:
            formatted += f"\n{self.formatException(record.exc_info)}"
        return formatted


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(5)
        module = record.name
        message = record.getMessage()
        return f"[{timestamp}] [{level}] [{module}] - {message}"


def get_logger(name: str):
    logger = logging.getLogger(name)
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    use_json = os.environ.get("HELM_LOG_JSON", "false").lower() == "true"
    file_formatter = JSONFormatter() if use_json else PlainTextFormatter()
    console_formatter = ConsoleFormatter()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    is_verbose = "--verbose" in sys.argv or os.environ.get("HELM_VERBOSE", "false").lower() == "true"
    console_handler.setLevel(logging.DEBUG if is_verbose else logging.WARNING)
    # Use console formatter which strips tracebacks to keep TUI clean, unless verbose
    console_handler.setFormatter(file_formatter if is_verbose else console_formatter)

    # File Handler
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
