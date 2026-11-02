"""Stable-file ingestion for the durable download queue."""

import signal
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from helm.config.secret_manager import redact_text
from helm.logger import get_logger
from helm.services.jobs import JobStore, process_one, queue_loop
from helm.torrents.torrent_links import MAX_METADATA

logger = get_logger(__name__)


def scan_directory(directory, store, observed, lite=False, settle=1, now=None):
    directory = Path(directory).resolve()
    now = time.monotonic() if now is None else now
    current = {}
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in (".torrent", ".magnet", ".txt"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue  # Removed between listing and stat.
        signature = (stat.st_size, stat.st_mtime_ns)
        previous = observed.get(str(path))
        since = previous[1] if previous and previous[0] == signature else now
        current[str(path)] = (signature, since)
        if now - since < settle:
            continue
        try:
            if stat.st_size > MAX_METADATA:
                raise ValueError("Watch input exceeds 8 MiB")
            if path.suffix.lower() == ".torrent":
                sources = [str(path)]
            else:
                sources = [
                    line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ]
                if not sources:
                    raise ValueError("Watch file contains no links")
                if any(urlsplit(source).scheme.lower() not in ("magnet", "http", "https") for source in sources):
                    raise ValueError("Watch files accept only magnet or HTTP(S) links")
            identifiers = [job["id"] for job in store.enqueue_many(sources, lite)]
            target = directory / ".processed"
            if target.is_symlink():
                raise ValueError("Processed directory must not be a symlink")
            target.mkdir(exist_ok=True)
            path.replace(target / (path.stem + "-" + uuid.uuid4().hex[:8] + path.suffix))
            logger.info("Queued %s download(s) from %s", len(identifiers), path.name)
            current.pop(str(path), None)
        except Exception as error:
            target = directory / ".failed"
            if target.is_symlink():
                logger.error("Failed directory must not be a symlink")
                continue
            target.mkdir(exist_ok=True)
            destination = target / (path.stem + "-" + uuid.uuid4().hex[:8] + path.suffix)
            if path.exists():
                path.replace(destination)
            destination.with_suffix(destination.suffix + ".error.txt").write_text(redact_text(error) + "\n")
            logger.warning("Watch input %s failed: %s", path.name, error)
            current.pop(str(path), None)
    return current


def safe_scan(directory, store, observed, lite, settle):
    """Keep a long-running watcher alive through a transient filesystem failure."""
    try:
        return scan_directory(directory, store, observed, lite, settle=settle)
    except OSError:
        logger.exception("Cannot scan watch directory %s", directory)
        return observed


def run_watch(directory, lite=False, interval=1, once=False, enqueue_only=False, stop=None):
    if interval <= 0:
        raise ValueError("Watch interval must be positive")
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    store = JobStore()
    stop = stop or threading.Event()
    previous_signals = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_signals[signum] = signal.signal(signum, lambda *_: stop.set())
    worker = None
    if not enqueue_only and not once:
        worker = threading.Thread(target=queue_loop, args=(store, stop), name="helm-download-queue")
        worker.start()
    observed = {}
    try:
        if once:
            observed = scan_directory(directory, store, observed, lite, settle=interval)
            stop.wait(interval)
            if not stop.is_set():
                scan_directory(directory, store, observed, lite, settle=interval)
            if not enqueue_only:
                while not stop.is_set() and process_one(store, stop):
                    pass
            return 0
        while not stop.is_set():
            observed = safe_scan(directory, store, observed, lite, interval)
            stop.wait(interval)
    finally:
        stop.set()
        if worker:
            worker.join()
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)
    return 0
