"""Authenticated local job API and range streaming for verified finished files."""

import hmac
import ipaddress
import json
import os
import re
import secrets
import signal
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from helm.core.config_manager import get_dl_dir
from helm.core.jobs import JobStore, cancel_job, public_job, queue_loop
from helm.core.logger import get_logger
from helm.core.secret_manager import get_secret, set_secrets
from helm.core.torrent_links import validate_magnet

logger = get_logger(__name__)


# Bounds how long a stalled client can hold a handler thread.
REQUEST_TIMEOUT = 30


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def byte_range(header, size):
    if header is None:
        return 0, max(0, size - 1), False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or size <= 0:
        raise ValueError("Invalid range")
    first, last = match.groups()
    if not first:
        if not last or int(last) <= 0:
            raise ValueError("Invalid suffix range")
        return max(0, size - int(last)), size - 1, True
    start = int(first)
    end = min(size - 1, int(last)) if last else size - 1
    if start > end or start >= size:
        raise ValueError("Range outside file")
    return start, end, True


def api_handler(store, token, read_only=False):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT

        def log_message(self, format, *args):
            logger.info("HTTP %s", format % args)

        def authorized(self):
            value = self.headers.get("Authorization", "")
            if not hmac.compare_digest(value.encode(), ("Bearer " + token).encode()):
                self.send_json(401, {"error": "Bearer authentication required"})
                self.close_connection = True
                return False
            return True

        def send_json(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_POST(self):
            if not self.authorized():
                return
            if read_only:
                self.send_json(403, {"error": "This file server is read-only"})
                self.close_connection = True
                return
            if urlsplit(self.path).path != "/jobs":
                self.send_json(404, {"error": "Not found"})
                self.close_connection = True
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    self.send_json(413, {"error": "JSON body must be between 1 and 65536 bytes"})
                    self.close_connection = True
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise ValueError("Expected application/json")
                data = json.loads(self.rfile.read(length))
                source = data.get("source")
                if not isinstance(source, str):
                    raise ValueError("A source string is required")
                scheme = urlsplit(source).scheme.lower()
                if scheme == "magnet":
                    validate_magnet(source)
                elif scheme not in ("http", "https") or not urlsplit(source).hostname:
                    raise ValueError("Only magnet and HTTP(S) links can be submitted remotely")
                job = store.enqueue(source, self.server.lite)
                self.send_json(202, public_job(job))
            except (ValueError, TypeError, AttributeError, RecursionError):
                # The body may be unread, so it cannot be left to parse as a request.
                self.close_connection = True
                self.send_json(400, {"error": "Invalid source or JSON body"})

        def do_DELETE(self):
            if not self.authorized():
                return
            identifier = urlsplit(self.path).path[len("/jobs/") :] if self.path.startswith("/jobs/") else ""
            if read_only:
                self.send_json(403, {"error": "This file server is read-only"})
                return
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                self.send_json(404, {"error": "Not found"})
                return
            try:
                changed = cancel_job(store, identifier)
                self.send_json(200 if changed else 409, {"cancelled": changed})
            except KeyError:
                self.send_json(404, {"error": "Job not found"})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self.authorized():
                return
            path = urlsplit(self.path).path
            if path == "/health":
                self.send_json(200, {"status": "ok", "read_only": read_only})
            elif path == "/jobs":
                self.send_json(200, [public_job(job) for job in store.list()])
            elif path.startswith("/jobs/"):
                job = store.get(path[len("/jobs/") :])
                self.send_json(200 if job else 404, public_job(job) if job else {"error": "Job not found"})
            elif path == "/files":
                self.send_json(200, store.files())
            elif path.startswith("/files/"):
                self.stream_file(unquote(path[len("/files/") :]))
            else:
                self.send_json(404, {"error": "Not found"})

        def stream_file(self, relative):
            root = Path(get_dl_dir()).resolve()
            file = root / relative
            try:
                resolved = file.resolve()
                if store.file_entry(relative) is None or root not in resolved.parents or file.is_symlink():
                    self.send_json(404, {"error": "Completed file not found"})
                    return
                # O_NOFOLLOW and fstat close the race between the checks and the open.
                descriptor = os.open(str(resolved), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    details = os.fstat(stream.fileno())
                    if not stat.S_ISREG(details.st_mode):
                        self.send_json(404, {"error": "Completed file not found"})
                        return
                    size = details.st_size
                    try:
                        start, end, partial = byte_range(self.headers.get("Range"), size)
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    count = 0 if size == 0 else end - start + 1
                    self.send_response(206 if partial else 200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(count))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + quote(resolved.name))
                    if partial:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    if self.command == "HEAD" or not count:
                        return
                    # A file truncated mid-send must not leave the client awaiting bytes.
                    if self.connection.sendfile(stream, start, count) != count:
                        self.close_connection = True
            except FileNotFoundError:
                self.send_json(404, {"error": "Completed file not found"})
            except OSError:
                self.close_connection = True

    return Handler


def make_server(host, port, store=None, token=None, lite=False, read_only=False):
    store = store or JobStore()
    token = token or get_secret("HELM_SERVER_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        set_secrets({"HELM_SERVER_TOKEN": token})
    if len(token) < 16:
        raise ValueError("HELM_SERVER_TOKEN must have at least 16 characters")
    server = ThreadingHTTPServer((host, port), api_handler(store, token, read_only))
    server.daemon_threads = True
    server.lite = lite
    return server, store


def run_server(host="127.0.0.1", port=8765, lite=False, read_only=False, stop=None):
    server, store = make_server(host, port, lite=lite, read_only=read_only)
    stop = stop or threading.Event()
    previous_signals = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_signals[signum] = signal.signal(signum, lambda *_: stop.set())
    worker = None
    if not read_only:
        worker = threading.Thread(target=queue_loop, args=(store, stop), name="helm-http-queue")
        worker.start()
    print(
        f"Listening on http://{host}:{server.server_port}; Bearer token: HELM_SERVER_TOKEN in secrets.env or environment",
        flush=True,
    )
    if not is_loopback(host):
        print("Warning: plain HTTP exposes the token on this network; use a VPN or TLS proxy.", file=sys.stderr)
    serving = threading.Thread(target=server.serve_forever, name="helm-http", daemon=True)
    serving.start()
    try:
        stop.wait()
    finally:
        stop.set()
        server.shutdown()
        serving.join()
        server.server_close()
        if worker:
            worker.join()
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)
    return 0
