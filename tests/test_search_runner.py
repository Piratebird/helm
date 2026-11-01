import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helm.core.search_runner import SearchJob, run_searches


def make_plugin(tmp_path, name, body):
    path = tmp_path / (name + ".py")
    path.write_text(
        "from novaprinter import prettyPrinter\nclass "
        + name
        + ":\n    name = '"
        + name
        + "'\n    def search(self, query, cat='all'):\n"
        + "\n".join("        " + line for line in body.splitlines())
        + "\n"
    )
    return SearchJob(name, "plugin", str(path))


def test_worker_roundtrip_and_category_propagation(tmp_path):
    job = make_plugin(
        tmp_path, "fixture", "prettyPrinter({'name': query, 'link': 'magnet:?xt=urn:btih:' + 'a'*40, 'seeds': 12})"
    )
    result = run_searches([job], "generated", timeout=5)
    assert len(result) == 1
    assert result[0].title == "generated"
    assert [(o.source, o.status, o.count) for o in result.outcomes] == [("fixture", "success", 1)]


def test_stalled_worker_cannot_delay_process_exit(tmp_path):
    stalled = make_plugin(tmp_path, "stalled", "import time\nwhile True: time.sleep(1)")
    healthy = make_plugin(
        tmp_path, "healthy", "prettyPrinter({'name': 'healthy', 'link': 'magnet:?xt=urn:btih:' + 'b'*40})"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    code = """import json
from helm.core.search_runner import SearchJob, run_searches
jobs = [SearchJob(**job) for job in json.loads(__import__('sys').argv[1])]
result = run_searches(jobs, 'generated', timeout=1)
print(json.dumps({'titles': [i.title for i in result], 'outcomes': [(o.source, o.status) for o in result.outcomes]}))
"""
    from dataclasses import asdict

    started = time.monotonic()
    process = subprocess.run(
        [sys.executable, "-c", code, json.dumps([asdict(stalled), asdict(healthy)])],
        env=environment,
        capture_output=True,
        text=True,
        timeout=4,
    )
    assert process.returncode == 0, process.stderr
    assert time.monotonic() - started < 4
    data = json.loads(process.stdout)
    assert data["titles"] == ["healthy"]
    assert ["stalled", "timeout"] in data["outcomes"]


def test_plugin_import_is_isolated_and_bounded(tmp_path):
    path = tmp_path / "bad.py"
    path.write_text("import time\nwhile True: time.sleep(1)\n")
    started = time.monotonic()
    result = run_searches([SearchJob("bad", "plugin", str(path))], "generated", timeout=0.3)
    assert result == []
    assert result.outcomes[0].status == "timeout"
    assert time.monotonic() - started < 2


def test_worker_exception_and_noise_are_contained(tmp_path):
    job = make_plugin(
        tmp_path, "noisy", "import sys\nprint('noise')\nsys.stdout.write('more noise')\nraise ValueError('bad parse')"
    )
    result = run_searches([job], "generated", timeout=5)
    assert result == []
    assert result.outcomes[0].status == "parse_error"


def test_queued_jobs_share_the_deadline(tmp_path):
    job = make_plugin(tmp_path, "stalled", "import time\ntime.sleep(20)")
    result = run_searches([job, job, job], "generated", timeout=0.3, max_workers=1)
    assert [outcome.status for outcome in result.outcomes] == ["timeout", "budget_exhausted", "budget_exhausted"]


@pytest.mark.parametrize("timeout,workers", [(0, 1), (1, 0)])
def test_invalid_worker_limits(timeout, workers):
    with pytest.raises(ValueError):
        run_searches([], "generated", timeout=timeout, max_workers=workers)


def test_keyboard_interrupt_reaps_workers(monkeypatch, tmp_path):
    import concurrent.futures

    import helm.core.search_runner as runner

    job = make_plugin(tmp_path, "stalled", "import time\ntime.sleep(20)")
    original = concurrent.futures.Future.result
    interrupted = False

    def interrupt_once(self, *args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            time.sleep(0.15)
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(concurrent.futures.Future, "result", interrupt_once)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        runner.run_searches([job], "generated", timeout=20)
    assert time.monotonic() - started < 3


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cleanup")
def test_worker_descendants_are_stopped_even_after_leader_exits(tmp_path):
    pid_file = tmp_path / "child.pid"
    job = make_plugin(
        tmp_path,
        "spawner",
        "import subprocess, sys\nfrom pathlib import Path\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\nPath(query).write_text(str(child.pid))",
    )
    result = run_searches([job], str(pid_file), timeout=5)
    assert result.outcomes[0].status == "empty"
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            stat = Path(f"/proc/{child}/stat").read_text()
        except FileNotFoundError:
            return
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return  # terminated; waiting for the OS init process to reap it
        time.sleep(0.01)
    pytest.fail("Worker descendant is still running")


def test_category_keys_reach_real_worker(tmp_path):
    path = tmp_path / "categorized.py"
    path.write_text(
        "from novaprinter import prettyPrinter\nclass categorized:\n    name = 'categorized'\n    supported_categories = {'all': '0', 'movies': '1', 'tv': '2', 'music': '3'}\n    def search(self, query, cat='all'):\n        prettyPrinter({'name': cat, 'link': 'magnet:?xt=urn:btih:' + 'a'*40})\n"
    )
    result = run_searches([SearchJob("categorized", "plugin", str(path))], "generated", "video", timeout=5)
    assert [item.title for item in result] == ["movies", "tv"]
    assert result.outcomes[0].count == 2


def test_windows_cleanup_uses_tree_termination(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import helm.core.search_runner as runner

    process = Mock()
    process.pid = 123
    process.poll.return_value = None
    monkeypatch.setattr(runner, "os", SimpleNamespace(name="nt"))
    terminate = Mock()
    monkeypatch.setattr(runner.subprocess, "run", terminate)
    runner.stop_worker(process)
    assert terminate.call_args[0][0] == ["taskkill", "/PID", "123", "/T", "/F"]
    process.kill.assert_called_once_with()
    process.communicate.assert_called_once_with(timeout=1)


def test_invalid_job_kind_reports_failure(tmp_path):
    result = run_searches([SearchJob("unknown", "unsupported", "none")], "generated", timeout=5)
    assert result == []
    assert result.outcomes[0].status == "parse_error"


def test_worker_limit_environment_and_explicit_override(monkeypatch):
    from helm.core.search_runner import worker_count

    monkeypatch.setenv("HELM_LITE_WORKERS", "2")
    assert worker_count() == 2
    assert worker_count(6) == 6
    monkeypatch.setenv("HELM_LITE_WORKERS", "invalid")
    with pytest.raises(ValueError):
        worker_count()
    with pytest.raises(ValueError):
        worker_count(13)


def test_worker_errors_reach_parent_log_with_secrets_redacted(monkeypatch, tmp_path):
    from helm.core.logger import configure_logging

    logs = tmp_path / "logs"
    monkeypatch.setenv("HELM_STATE_DIR", str(logs))
    monkeypatch.setenv("QB_PASSWORD", "known-worker-private")
    configure_logging()
    job = make_plugin(tmp_path, "failure", "raise ValueError('known-worker-private url?apikey=unknown-worker-private')")
    result = run_searches([job], "generated", timeout=5)
    assert result.outcomes[0].status == "parse_error"
    contents = (logs / "helm.log").read_text()
    assert "ValueError" in contents
    assert "known-worker-private" not in contents
    assert "unknown-worker-private" not in contents
