"""Daemons and tmux sessions, using real processes and a private tmux socket."""

import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import helm.core.runtime as runtime
from helm.core.config_manager import get_state_dir
from helm.core.runtime import (
    child_arguments,
    child_environment,
    daemon_records,
    private_json,
    process_alive,
    process_start,
    session_name,
    start_daemon,
    stop_daemon,
)

SRC = str(Path(runtime.__file__).resolve().parents[2])


def test_child_environment_never_adds_working_directory(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert child_environment()["PYTHONPATH"] == SRC
    assert child_environment({"PYTHONPATH": ""})["PYTHONPATH"] == SRC
    assert child_environment({"PYTHONPATH": "/x"})["PYTHONPATH"] == SRC + os.pathsep + "/x"


def test_process_identity():
    assert process_start(os.getpid())
    assert process_alive(os.getpid()) is True
    assert process_alive(os.getpid(), process_start(os.getpid())) is True
    assert process_alive(os.getpid(), "different-start-time") is False
    assert process_alive(None) is False
    assert process_alive("bad") is False
    assert process_start(2**22 + 1) is None


def test_reaped_and_zombie_processes_are_not_alive():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 5
    while process_start(process.pid) is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert process_alive(process.pid) is False  # Zombie: exited, not yet reaped.
    process.wait()
    assert process_alive(process.pid) is False


def test_private_json_is_atomic_and_private(tmp_path):
    path = tmp_path / "sub" / "record.json"
    private_json(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [item.name for item in path.parent.iterdir()] == ["record.json"]


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["watch", "d", "--daemon"], ["watch", "d"]),
        (["--session", "a", "ui"], ["ui"]),
        (["--session=a", "ui", "--lite"], ["ui", "--lite"]),
    ],
)
def test_child_arguments(arguments, expected):
    assert child_arguments(arguments) == expected


@pytest.mark.parametrize("value", ["", "a b", "a;b", "x" * 41, "../x"])
def test_session_names_are_validated(value):
    with pytest.raises(ValueError):
        session_name(value)


def fake_cli(tmp_path, monkeypatch, body):
    package = tmp_path / "fake" / "helm"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "cli.py").write_text(body)
    monkeypatch.setattr(runtime, "child_environment", lambda: {**os.environ, "PYTHONPATH": str(tmp_path / "fake")})


def wait_for(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_daemon_lifecycle_with_graceful_stop(tmp_path, monkeypatch):
    marker = tmp_path / "stopped"
    fake_cli(
        tmp_path,
        monkeypatch,
        "import signal,sys,time\n"
        f"signal.signal(signal.SIGTERM, lambda *a: (open({str(marker)!r},'w').close(), sys.exit(0)))\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n",
    )
    identifier = start_daemon(["--daemon", "serve", "--port", "0"])
    record = next(item for item in daemon_records() if item["id"] == identifier)
    assert record["running"] is True
    assert record["command"] == "serve --port 0"
    log = Path(record["log"])
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    wait_for(lambda: "started" in log.read_text())
    assert stop_daemon(identifier) is True
    wait_for(marker.exists)
    wait_for(lambda: not process_alive(record["pid"], record["identity"]))
    assert stop_daemon(identifier) is False
    assert next(item for item in daemon_records() if item["id"] == identifier)["running"] is False


def test_forced_stop_kills_whole_process_group(tmp_path, monkeypatch):
    child_pid = tmp_path / "child"
    fake_cli(
        tmp_path,
        monkeypatch,
        "import signal,subprocess,sys,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
        f"open({str(child_pid)!r},'w').write(str(p.pid))\n"
        "time.sleep(60)\n",
    )
    identifier = start_daemon(["serve"])
    wait_for(lambda: child_pid.exists() and child_pid.read_text())
    assert stop_daemon(identifier, timeout=0.2) is True

    def gone():
        return process_start(int(child_pid.read_text())) is None

    wait_for(gone)


def test_immediate_daemon_failure_is_reported(tmp_path, monkeypatch):
    fake_cli(tmp_path, monkeypatch, "raise SystemExit(3)\n")
    # Give the child time to exit inside the normally brief failure window.
    clock = SimpleNamespace(time=time.time, sleep=lambda _: time.sleep(2))
    monkeypatch.setattr(runtime, "time", clock)
    with pytest.raises(RuntimeError, match="failed"):
        start_daemon(["serve"])


def test_stop_validates_identifier():
    with pytest.raises(ValueError):
        stop_daemon("../../etc/passwd")
    with pytest.raises(OSError):
        stop_daemon("0" * 12)


def test_process_without_identity_is_not_signalled(monkeypatch):
    directory = Path(get_state_dir()) / "daemons"
    identifier = "a" * 12
    private_json(directory / (identifier + ".json"), {"id": identifier, "pid": os.getpid(), "identity": None})
    try:
        with pytest.raises(RuntimeError, match="identity"):
            stop_daemon(identifier)
    finally:
        (directory / (identifier + ".json")).unlink()


def test_corrupt_records_are_skipped():
    directory = Path(get_state_dir()) / "daemons"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "corrupt.json").write_text("{")
    try:
        assert all(record.get("id") != "corrupt" for record in daemon_records())
    finally:
        (directory / "corrupt.json").unlink()


def test_missing_tmux_gives_install_hint(monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Install tmux"):
        runtime.tmux_command("list-sessions")


@pytest.fixture
def tmux_socket(monkeypatch):
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    name = "helm-test-" + uuid.uuid4().hex
    monkeypatch.setenv("HELM_TMUX_SOCKET", name)
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True)


def test_tmux_session_forwards_paths_to_existing_server(tmux_socket, tmp_path, monkeypatch):
    fake_cli(
        tmp_path,
        monkeypatch,
        "import json,os,sys,time\n"
        f"open({str(tmp_path / 'seen')!r},'w').write(json.dumps([sys.argv[1:], os.environ['HELM_STATE_DIR'], os.environ.get('HELM_SERVER_TOKEN')]))\n"
        "time.sleep(60)\n",
    )
    monkeypatch.setenv("HELM_SERVER_TOKEN", "must-not-be-forwarded")
    # A server started first has an older environment that must not win.
    subprocess.run(
        ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", "other", "sleep", "60"],
        env={**{k: v for k, v in os.environ.items() if k != "HELM_SERVER_TOKEN"}, "HELM_STATE_DIR": "/stale"},
        check=True,
    )
    monkeypatch.setenv("HELM_STATE_DIR", str(tmp_path / "state"))
    assert runtime.start_session("main", ["--session", "main", "--lite"]) == "helm-torrent-main"
    seen = tmp_path / "seen"
    wait_for(lambda: seen.exists() and seen.read_text())
    arguments, state, token = json.loads(seen.read_text())
    assert arguments == ["--lite", "ui"]
    assert state == str(tmp_path / "state")
    assert token is None
    with pytest.raises(ValueError, match="already exists"):
        runtime.start_session("main", [])


def test_attach_requires_existing_session_and_execs_tmux(tmux_socket, monkeypatch):
    with pytest.raises(ValueError, match="No running"):
        runtime.attach_session("missing")
    subprocess.run(
        ["tmux", "-L", tmux_socket, "new-session", "-d", "-s", "helm-torrent-main", "sleep", "60"], check=True
    )
    calls = []
    monkeypatch.setattr(runtime.os, "execvp", lambda file, arguments: calls.append(arguments))
    runtime.attach_session("main")
    assert calls == [["tmux", "-L", tmux_socket, "attach-session", "-t", "=helm-torrent-main"]]
