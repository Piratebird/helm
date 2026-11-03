"""Isolated Lite jobs with a shared deadline and bounded process cleanup."""

import concurrent.futures
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass

from helm.config.config_manager import worker_count
from helm.logger import get_logger
from helm.search.rss_fetcher import SearchResults, SourceOutcome, TorrentItem, source_status
from helm.services.runtime import child_environment

logger = get_logger(__name__)


@dataclass
class SearchJob:
    source: str
    kind: str
    target: str


def stop_worker(process):
    """Reap a worker and its POSIX process group, even after the leader exits."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    elif process.poll() is None:
        process.kill()
    try:
        process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=1)


def run_searches(jobs, query, content_type="video", timeout=30, max_workers=None):
    max_workers = worker_count(max_workers)
    if timeout <= 0:
        raise ValueError("Search budget must be positive")
    deadline = time.monotonic() + timeout
    cancelled = threading.Event()
    active = set()
    lock = threading.Lock()

    def run(job):
        if cancelled.is_set() or time.monotonic() >= deadline:
            return SearchResults([], [SourceOutcome(job.source, "budget_exhausted")])
        environment = child_environment()
        process = None
        try:
            with lock:
                if cancelled.is_set():
                    return SearchResults([], [SourceOutcome(job.source, "cancelled")])
                process = subprocess.Popen(
                    [sys.executable, "-m", "helm.search.search_runner", "--worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                    start_new_session=os.name == "posix",
                )
                active.add(process)
            payload = json.dumps({"job": asdict(job), "query": query, "content_type": content_type})
            while not cancelled.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return SearchResults([], [SourceOutcome(job.source, "timeout")])
                try:
                    stdout, stderr = process.communicate(input=payload, timeout=min(remaining, 0.1))
                    if stderr:
                        logger.debug("Worker %s: %s", job.source, stderr[-4096:])
                    if process.returncode:
                        raise RuntimeError("Search worker exited without results")
                    data = json.loads(stdout)
                    return SearchResults(
                        [TorrentItem(**item) for item in data["items"]],
                        [SourceOutcome(**outcome) for outcome in data["outcomes"]],
                    )
                except subprocess.TimeoutExpired:
                    payload = None  # communicate resumes the original input/output
            return SearchResults([], [SourceOutcome(job.source, "cancelled")])
        except Exception as error:
            logger.debug("Worker %s failed", job.source, exc_info=True)
            return SearchResults([], [SourceOutcome(job.source, source_status(error))])
        finally:
            if process is not None:
                stop_worker(process)
                with lock:
                    active.discard(process)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    futures = []
    try:
        futures = [pool.submit(run, job) for job in jobs]
        results = SearchResults()
        # Input order keeps source precedence stable between identical searches.
        for future in futures:
            results.extend(future.result())
        return results
    finally:
        cancelled.set()
        for future in futures:
            future.cancel()
        # Workers observe cancellation every 100ms. No abandoned executor threads.
        pool.shutdown(wait=True)
        with lock:
            for process in active:
                stop_worker(process)


def execute_job(job, query, content_type):
    if job.kind == "inline":
        from helm.search import lite_fetcher

        functions = {
            "apibay": lite_fetcher._fetch_apibay,
            "csv": lite_fetcher._fetch_torrents_csv,
            "nyaa": lite_fetcher._fetch_nyaa,
        }
        return functions[job.target](query)
    if job.kind == "plugin":
        from helm.search.lite_plugin_loader import _execute_plugin, load_plugin

        plugin = load_plugin(job.target)
        return _execute_plugin(plugin, query, content_type, path=job.target)
    raise ValueError("Unknown search job kind")


def worker():
    from helm.logger import configure_logging

    # The parent captures and files these diagnostics at DEBUG. Workers never
    # rotate a shared log file or write directly into the user's console.
    configure_logging(file_logging=False, verbose=True)
    request = json.load(sys.stdin)
    job = SearchJob(**request["job"])
    try:
        # Plugins must not interleave prints with the worker protocol.
        with contextlib.redirect_stdout(io.StringIO()):
            result = execute_job(job, request["query"], request["content_type"])
    except Exception as error:
        logger.debug("Job failed", exc_info=True)
        result = SearchResults([], [SourceOutcome(job.source, source_status(error))])
    json.dump({"items": [vars(item) for item in result], "outcomes": [asdict(o) for o in result.outcomes]}, sys.stdout)


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("This module is an internal search worker")
    worker()
