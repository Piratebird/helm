"""Detached commands and reconnectable terminal sessions without shell expansion."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from helm.config.config_manager import get_state_dir
from helm.config.secret_manager import redact_text

# Paths and settings a tmux server started earlier would otherwise not see. Secrets
# are excluded because new-session arguments are visible in the process list.
SESSION_ENVIRONMENT = (
    "PYTHONPATH",
    "HELM_CONFIG_DIR",
    "HELM_STATE_DIR",
    "HELM_DL_DIR",
    "HELM_LITE_WORKERS",
    "HELM_LOG_JSON",
    "HELM_VERBOSE",
)


def child_environment(base=None):
    """Return an environment that can import this package in a subprocess."""
    environment = dict(os.environ if base is None else base)
    root = str(Path(__file__).resolve().parents[2])
    inherited = environment.get("PYTHONPATH")
    # An empty entry would put the working directory on the child's import path.
    environment["PYTHONPATH"] = root + os.pathsep + inherited if inherited else root
    return environment


def process_start(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text.rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19]
    except (OSError, IndexError):
        return None


def process_alive(pid, identity=None):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    actual = process_start(pid)
    if Path("/proc").is_dir():
        return actual is not None and (identity is None or actual == identity)
    return identity is None


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def child_arguments(arguments):
    result = []
    skip = False
    for argument in arguments:
        if skip:
            skip = False
            continue
        if argument == "--daemon":
            continue
        if argument == "--session":
            skip = True
            continue
        if argument.startswith("--session="):
            continue
        result.append(argument)
    return result


def start_daemon(arguments):
    directory = Path(get_state_dir()) / "daemons"
    directory.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex[:12]
    log = directory / (identifier + ".log")
    descriptor = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    environment = child_environment()
    environment["HELM_DAEMONIZED"] = "1"
    with os.fdopen(descriptor, "wb") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "helm.cli", *child_arguments(arguments)],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=os.name == "posix",
            env=environment,
        )
    identity = process_start(process.pid)
    private_json(
        directory / (identifier + ".json"),
        {
            "id": identifier,
            "pid": process.pid,
            "identity": identity,
            "created": time.time(),
            "command": redact_text(" ".join(child_arguments(arguments))),
            "log": str(log),
        },
    )
    # Reap immediate failures so a failed detach is not reported as success.
    time.sleep(0.1)
    code = process.poll()
    if code is not None and code != 0:
        raise RuntimeError(f"Background command failed ({code}); see {log}")
    return identifier


def daemon_records():
    directory = Path(get_state_dir()) / "daemons"
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text())
            record["running"] = process_alive(record["pid"], record["identity"])
            records.append(record)
        except (OSError, ValueError, KeyError):
            continue
    return records


def prune_daemons():
    """Delete the records (and logs) of daemons that are no longer running; return their ids."""
    directory = Path(get_state_dir()) / "daemons"
    removed = []
    for record in daemon_records():
        if record["running"]:
            continue
        identifier = record.get("id")
        if not identifier or not re.fullmatch(r"[a-f0-9]{12}", identifier):
            continue
        for path in (directory / f"{identifier}.json", directory / f"{identifier}.log"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        removed.append(identifier)
    return removed


def stop_daemon(identifier, timeout=5):
    if not re.fullmatch(r"[a-f0-9]{12}", identifier):
        raise ValueError("Invalid daemon id")
    path = Path(get_state_dir()) / "daemons" / (identifier + ".json")
    record = json.loads(path.read_text())
    pid, identity = record["pid"], record["identity"]
    if not process_alive(pid, identity):
        return False
    if identity is None:
        raise RuntimeError("Cannot verify this process identity; stop it through the operating system")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while process_alive(pid, identity) and time.monotonic() < deadline:
        time.sleep(0.05)
    if process_alive(pid, identity):
        try:
            # The daemon leads its session; job workers notice their parent is gone.
            if os.name == "posix":
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True


def session_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
        raise ValueError("Session names must contain 1–40 letters, digits, hyphens or underscores")
    return "helm-torrent-" + value


def tmux_command(*arguments, check=True):
    if shutil.which("tmux") is None:
        raise RuntimeError("Install tmux to create or attach a terminal session (Termux: pkg install tmux)")
    return subprocess.run(
        ["tmux", "-L", os.environ.get("HELM_TMUX_SOCKET", "helm-torrent"), *arguments],
        check=check,
        capture_output=True,
        text=True,
        timeout=10,
    )


def start_session(name, arguments):
    target = session_name(name)
    exists = tmux_command("has-session", "-t", "=" + target, check=False)
    if exists.returncode == 0:
        raise ValueError("Session already exists; use helm-torrent attach " + name)
    command = [sys.executable, "-m", "helm.cli", *child_arguments(arguments)]
    if not any(argument in ("ui", "search", "indexers") for argument in command[3:]):
        command.append("ui")
    environment = child_environment()
    forwarded = [
        item for key in SESSION_ENVIRONMENT if key in environment for item in ("-e", key + "=" + environment[key])
    ]
    tmux_command("new-session", "-d", "-s", target, "-c", os.getcwd(), *forwarded, "--", *command)
    return target


def attach_session(name):
    target = session_name(name)
    result = tmux_command("has-session", "-t", "=" + target, check=False)
    if result.returncode:
        raise ValueError("No running Helm session named " + name)
    os.execvp(
        "tmux", ["tmux", "-L", os.environ.get("HELM_TMUX_SOCKET", "helm-torrent"), "attach-session", "-t", "=" + target]
    )
