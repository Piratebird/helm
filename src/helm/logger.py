import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from helm.config.config_manager import get_log_dir
from helm.config.secret_manager import redact_config, redact_text


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
                "file_only",
            ]:
                log_obj[key] = value

        return json.dumps(redact_config(log_obj))


class FileOnlyFilter(logging.Filter):
    """Drops records flagged ``file_only=True`` from the console so they only reach the log file."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "file_only", False)


class PlainTextFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(5)
        module = record.name
        message = record.getMessage()
        formatted = f"[{timestamp}] [{level}] [{module}] - {message}"
        if record.exc_info:
            formatted += f"\n{self.formatException(record.exc_info)}"
        return redact_text(formatted)


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname.ljust(5)
        module = record.name
        message = record.getMessage()
        return redact_text(f"[{timestamp}] [{level}] [{module}] - {message}")


def get_logger(name: str):
    # Importing modules must not create files before CLI path overrides are known.
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def configure_logging(file_logging=True, verbose=None):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        if getattr(handler, "_helm_handler", False):
            root.removeHandler(handler)
            handler.close()
    formatter = JSONFormatter() if os.environ.get("HELM_LOG_JSON", "false").lower() == "true" else PlainTextFormatter()
    if verbose is None:
        verbose = os.environ.get("HELM_VERBOSE", "false").lower() == "true"
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(formatter if verbose else ConsoleFormatter())
    console.addFilter(FileOnlyFilter())
    handlers = [console]
    if file_logging:
        directory = get_log_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "helm.log")
        old = os.path.expanduser("~/.helm_data/logs/helm.log")
        if os.path.isfile(old) and not os.path.exists(path):
            try:
                shutil.move(old, path)
            except OSError:
                pass
        handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        handlers.append(handler)
    for handler in handlers:
        handler._helm_handler = True
        root.addHandler(handler)


def tail_log(path, lines=20, max_bytes=1024 * 1024):
    """Read a bounded suffix; never scan an entire large log to display its tail."""
    if lines < 1 or max_bytes < 1:
        return ""
    with open(path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        end = stream.tell()
        data = b""
        position = end
        while position and data.count(b"\n") <= lines and len(data) < max_bytes:
            amount = min(8192, position, max_bytes - len(data))
            position -= amount
            stream.seek(position)
            data = stream.read(amount) + data
    if position and b"\n" in data:
        data = data.split(b"\n", 1)[1]
    result = b"\n".join(data.splitlines()[-lines:]).decode("utf-8", errors="replace")
    return redact_text(result) + ("\n" if result else "")
