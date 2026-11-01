"""The installed entry point, run as real subprocesses with disposable state."""

import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

SRC = str(Path(__file__).resolve().parents[1] / "src")
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
TOKEN = "t" * 32


@pytest.fixture
def environment(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": SRC,
        "HELM_CONFIG_DIR": str(tmp_path / "config"),
        "HELM_STATE_DIR": str(tmp_path / "state"),
        "HELM_DL_DIR": str(tmp_path / "downloads"),
        "HELM_SERVER_TOKEN": TOKEN,
    }
    for key in ("QB_WEBUI", "QB_PASSWORD"):
        env.pop(key, None)
    return env


def helm(env, *arguments, timeout=30, stdin=subprocess.DEVNULL):
    return subprocess.run(
        [sys.executable, "-m", "helm.cli", *arguments],
        env=env,
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for_health(port, timeout=20):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return requests.get(
                f"http://127.0.0.1:{port}/health", headers={"Authorization": "Bearer " + TOKEN}, timeout=1
            )
        except requests.ConnectionError:
            assert time.monotonic() < deadline
            time.sleep(0.05)


def test_heavy_modules_stay_out_of_light_commands(environment):
    code = (
        "import sys, helm.cli\n"
        "print(sorted(m for m in ('requests', 'bs4', 'helm.ui.tui', 'helm.core.config_wizard') if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "[]"


def test_daemon_status_without_records(environment):
    result = helm(environment, "daemon", "status")
    assert result.returncode == 0
    assert json.loads(result.stdout) == []


def test_daemon_stop_needs_id(environment):
    assert helm(environment, "daemon", "stop").returncode == 2


def test_session_rejects_headless_command(environment):
    result = helm(environment, "--session", "a", "serve")
    assert result.returncode == 2 and "headless" in result.stderr


def test_background_search_needs_noninteractive_mode(environment):
    assert helm(environment, "search", "ubuntu", "--daemon").returncode == 2


def test_invalid_port(environment):
    assert helm(environment, "serve", "--port", "70000", "--lite").returncode == 2


def test_download_rejects_invalid_source_without_prompting(environment):
    result = helm(environment, "download", "magnet:?xt=urn:btih:12345")
    assert result.returncode == 1
    assert "info hash" in result.stderr


def test_download_failure_reports_job_status(environment, tmp_path):
    # No libtorrent session can complete this: the worker exits without success.
    environment["PYTHONPATH"] = str(tmp_path / "no-libtorrent") + os.pathsep + SRC
    stub = tmp_path / "no-libtorrent"
    stub.mkdir()
    (stub / "libtorrent.py").write_text("raise ImportError('stub')\n")
    result = helm(environment, "download", MAGNET, "--lite")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert "did not complete" in report["error"]


def test_watch_once_enqueue_only_uses_state_dir(environment, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.magnet").write_text(MAGNET)
    result = helm(environment, "watch", str(inbox), "--once", "--enqueue-only", "--interval", "0.1", "--lite")
    assert result.returncode == 0, result.stderr
    assert not (inbox / "a.magnet").exists()
    assert len(list((inbox / ".processed").iterdir())) == 1
    database = tmp_path / "state" / "jobs.sqlite3"
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_serve_daemon_lifecycle_and_job_submission(environment, tmp_path):
    port = free_port()
    started = helm(environment, "--daemon", "serve", "--port", str(port), "--lite")
    assert started.returncode == 0, started.stderr
    records = json.loads(helm(environment, "daemon", "status").stdout)
    assert len(records) == 1 and records[0]["running"] is True
    try:
        assert wait_for_health(port).json()["read_only"] is False
        response = requests.post(
            f"http://127.0.0.1:{port}/jobs",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"source": MAGNET},
            timeout=5,
        )
        assert response.status_code == 202
    finally:
        stopped = helm(environment, "daemon", "stop", records[0]["id"])
    assert json.loads(stopped.stdout) == {"stopped": True}
    assert json.loads(helm(environment, "daemon", "status").stdout)[0]["running"] is False
    log = Path(records[0]["log"]).read_text()
    assert TOKEN not in log


def test_files_server_is_read_only(environment):
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "helm.cli", "files", "--port", str(port)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for_health(port).json()["read_only"] is True
    finally:
        process.terminate()
    assert process.wait(10) == 0
