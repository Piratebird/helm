"""Durable headless download queue with process-isolated, cancellable workers."""

import contextlib
import hashlib
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from helm.core.config_manager import get_dl_dir, get_state_dir
from helm.core.runtime import child_environment, process_alive, process_start
from helm.core.secret_manager import redact_text
from helm.core.torrent_links import MAX_METADATA, metadata_source, validate_magnet

TERMINAL = {"succeeded", "failed", "cancelled"}
RETRYABLE = ("failed", "cancelled")
# Seconds a submitted job may be absent from qBittorrent before it counts as removed.
MISSING_GRACE = 300


class JobStore:
    def __init__(self, path=None):
        self.path = Path(path or Path(get_state_dir()) / "jobs.sqlite3").absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create the file privately first: SQLite copies its mode to -wal/-shm.
        os.close(os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600))
        os.chmod(self.path, 0o600)
        with self.connect() as connection:
            # HTTP threads, the queue thread and worker processes share this file.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, source TEXT NOT NULL,
                    lite INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created REAL, updated REAL, owner_pid INTEGER, owner_start TEXT,
                    error TEXT, result TEXT
                );
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, job_id TEXT, size INTEGER, completed REAL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created);
            """)

    @contextlib.contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _prepare(self, source, lite):
        """Validate one source and return its stored form and dedupe fingerprint."""
        source = str(source).strip()
        parts = urlsplit(source)
        scheme = parts.scheme.lower()
        if scheme == "magnet":
            validate_magnet(source)
        elif scheme in ("http", "https"):
            if not parts.hostname:
                raise ValueError("HTTP(S) link has no host")
        else:
            source = self._spool(source)
        return source, hashlib.sha256((str(bool(lite)) + "\0" + source).encode()).hexdigest()

    def _spool(self, source):
        path = Path(source)
        if path.suffix.lower() != ".torrent" or not path.is_file():
            raise ValueError("Expected a magnet, HTTP(S) link or local .torrent file")
        with path.open("rb") as stream:
            data = stream.read(MAX_METADATA + 1)
        metadata_source(data)
        spool = self.path.parent / "spool"
        spool.mkdir(exist_ok=True)
        cached = spool / (hashlib.sha256(data).hexdigest() + ".torrent")
        if not cached.exists():
            temporary = spool / (uuid.uuid4().hex + ".tmp")
            fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                os.replace(temporary, cached)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return str(cached)

    def enqueue(self, source, lite=False):
        return self.enqueue_many([source], lite)[0]

    def enqueue_many(self, sources, lite=False):
        """Queue every source or none: validation happens before the transaction."""
        prepared = [self._prepare(source, lite) for source in sources]
        rows = []
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for source, fingerprint in prepared:
                connection.execute(
                    "INSERT OR IGNORE INTO jobs(id,fingerprint,source,lite,created,updated) VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex, fingerprint, source, int(lite), now, now),
                )
                row = connection.execute("SELECT * FROM jobs WHERE fingerprint=?", (fingerprint,)).fetchone()
                # A finished failure may be retried, but never while its worker can still write.
                if row["status"] in RETRYABLE and not process_alive(row["owner_pid"], row["owner_start"]):
                    connection.execute(
                        "UPDATE jobs SET status='pending',updated=?,error=NULL,result=NULL,"
                        "owner_pid=NULL,owner_start=NULL WHERE id=?",
                        (now, row["id"]),
                    )
                    row = connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
                rows.append(dict(row))
        return rows

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def list(self, limit=100):
        with self.connect() as connection:
            return [
                dict(row) for row in connection.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))
            ]

    def with_status(self, status):
        with self.connect() as connection:
            return [
                dict(row) for row in connection.execute("SELECT * FROM jobs WHERE status=? ORDER BY created", (status,))
            ]

    def update(self, identifier, status, error=None, result=None):
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?,updated=?,error=?,result=? WHERE id=? AND (status!='cancelled' OR ?='cancelled')",
                (status, time.time(), redact_text(error) if error else None, result, identifier, status),
            )

    def claim(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            running = connection.execute("SELECT id,owner_pid,owner_start FROM jobs WHERE status='running'").fetchall()
            for row in running:
                if not process_alive(row["owner_pid"], row["owner_start"]):
                    connection.execute(
                        "UPDATE jobs SET status='pending',owner_pid=NULL,owner_start=NULL WHERE id=?", (row["id"],)
                    )
            row = connection.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET status='running',owner_pid=?,owner_start=?,updated=? WHERE id=?",
                (os.getpid(), process_start(os.getpid()), time.time(), row["id"]),
            )
        return self.get(row["id"])

    def release(self, identifier):
        """Record that no worker process can still write to this job."""
        with self.connect() as connection:
            connection.execute("UPDATE jobs SET owner_pid=NULL,owner_start=NULL WHERE id=?", (identifier,))

    def cancel(self, identifier):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            if row["status"] in TERMINAL:
                return False
            connection.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=?", (time.time(), identifier))
        return True

    def register_files(self, identifier, paths):
        root = Path(get_dl_dir()).resolve()
        with self.connect() as connection:
            for value in paths:
                path = Path(value).resolve()
                if root not in path.parents or not path.is_file():
                    continue
                relative = str(path.relative_to(root))
                connection.execute(
                    "INSERT OR REPLACE INTO files(path,job_id,size,completed) VALUES(?,?,?,?)",
                    (relative, identifier, path.stat().st_size, time.time()),
                )

    def files(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM files ORDER BY path")]

    def file_entry(self, relative):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM files WHERE path=?", (relative,)).fetchone()
        return dict(row) if row else None


def public_job(job):
    return {
        key: redact_text(job[key]) if isinstance(job[key], str) else job[key]
        for key in ("id", "source", "status", "created", "updated", "error", "result")
    }


def job_tag(identifier):
    return "helm-job-" + identifier


def execute_job(store, identifier):
    from helm.core.lite_downloader import download_magnet
    from helm.core.qbittorrent_client import SHARED_TAG, add_magnet

    job = store.get(identifier)
    if job is None or job["status"] != "running":
        return 1
    try:
        if job["lite"]:
            if not download_magnet(job["source"], on_complete=lambda paths: store.register_files(identifier, paths)):
                raise RuntimeError("Built-in download did not complete")
            store.update(identifier, "succeeded", result="Download completed")
        else:
            add_magnet(job["source"], tag=SHARED_TAG + "," + job_tag(identifier))
            store.update(identifier, "submitted", result="Accepted by qBittorrent")
        return 0
    except Exception as error:
        store.update(identifier, "failed", error=error)
        return 1


def cancel_job(store, identifier):
    """Cancel a job, and its qBittorrent torrent when one may have been submitted."""
    from helm.core.logger import get_logger

    job = store.get(identifier)
    if job is None:
        raise KeyError(identifier)
    changed = store.cancel(identifier)
    if changed and not job["lite"] and job["status"] in ("running", "submitted"):
        from helm.core.qbittorrent_client import delete_tagged

        try:
            delete_tagged(job_tag(identifier))
        except Exception:
            get_logger(__name__).warning("Cannot remove cancelled job %s from qBittorrent", identifier, exc_info=True)
    return changed


def process_one(store, stop=None, cancel_poll=1.0):
    from helm.core.search_runner import stop_worker

    stop = stop or threading.Event()
    job = store.claim()
    if job is None:
        return False
    identifier = job["id"]
    logs = store.path.parent / "jobs"
    process = None
    try:
        logs.mkdir(exist_ok=True)
        fd = os.open(str(logs / (identifier + ".log")), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "wb") as stream:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "helm.core.jobs", "--worker", str(store.path), identifier],
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                    env=child_environment(),
                    start_new_session=os.name == "posix",
                )
            except OSError as error:
                store.update(identifier, "failed", error=error)
                return True
            next_poll = time.monotonic() + cancel_poll
            while process.poll() is None:
                if stop.wait(0.1):
                    stop_worker(process)
                    if store.get(identifier)["status"] == "running":
                        store.update(identifier, "pending", result="Interrupted; queued for resume")
                    return True
                if time.monotonic() >= next_poll:
                    next_poll += cancel_poll
                    if store.get(identifier)["status"] == "cancelled":
                        return True
            if store.get(identifier)["status"] == "running":
                store.update(identifier, "failed", error="Download worker exited without a result; see its job log")
    finally:
        if process is not None:
            stop_worker(process)
        store.release(identifier)
    return True


def host_path(save_path, name):
    """Map a path reported by qBittorrent, possibly inside its container, to this host."""
    from helm.core.config_manager import load_config

    mount = PurePosixPath(os.getenv("QB_DOWNLOADS_ROOT") or load_config().get("QB_DOWNLOADS_ROOT") or "/downloads")
    reported = PurePosixPath(save_path or mount) / name
    if mount in reported.parents:
        return Path(get_dl_dir()) / reported.relative_to(mount)
    return Path(reported)


def reconcile_qbittorrent(store):
    from helm.core.qbittorrent_client import qb_request, tagged_torrents

    pending = store.with_status("submitted")
    if not pending:
        return
    by_tag = {}
    for torrent in tagged_torrents():
        for tag in torrent.get("tags", "").split(","):
            by_tag.setdefault(tag.strip(), []).append(torrent)
    for job in pending:
        torrents = by_tag.get(job_tag(job["id"]), [])
        if not torrents:
            if time.time() - job["updated"] > MISSING_GRACE:
                store.update(job["id"], "failed", error="Torrent is no longer present in qBittorrent")
            continue
        if not all(torrent.get("progress", 0) >= 1 for torrent in torrents):
            continue
        paths = []
        for torrent in torrents:
            files = qb_request("GET", "/api/v2/torrents/files", params={"hash": torrent["hash"]}).json()
            paths.extend(
                str(host_path(torrent.get("save_path"), item["name"])) for item in files if item.get("progress") == 1
            )
        store.register_files(job["id"], paths)
        store.update(job["id"], "succeeded", result="qBittorrent download completed")


def queue_loop(store, stop, reconcile_every=5):
    from helm.core.logger import get_logger

    logger = get_logger(__name__)
    failures = 0
    next_reconcile = 0.0
    while not stop.is_set():
        try:
            busy = process_one(store, stop)
            failures = 0
        except Exception:
            # The service keeps accepting work, so its worker must outlive a bad job.
            failures += 1
            logger.exception("Download queue iteration failed")
            stop.wait(min(30, 2**failures))
            continue
        if stop.is_set():
            break
        if time.monotonic() >= next_reconcile:
            next_reconcile = time.monotonic() + reconcile_every
            try:
                reconcile_qbittorrent(store)
            except Exception:
                logger.debug("Cannot reconcile qBittorrent jobs", exc_info=True)
        if not busy:
            stop.wait(0.5)


def exit_with_parent(parent, interval=1):
    """Stop an orphaned worker: a force-killed service cannot reap its process group."""
    while os.getppid() == parent:
        time.sleep(interval)
    os._exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("Internal job worker")
    from helm.core.logger import configure_logging

    threading.Thread(target=exit_with_parent, args=(os.getppid(),), daemon=True).start()
    configure_logging(file_logging=False, verbose=True)
    raise SystemExit(execute_job(JobStore(sys.argv[2]), sys.argv[3]))
