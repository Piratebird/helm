"""Queue behaviour against a real database and real worker subprocesses."""

import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import responses

import helm.services.jobs as jobs
import helm.torrents.qbittorrent_client as client
from helm.config.config_manager import get_dl_dir
from helm.services.jobs import (
    JobStore,
    cancel_job,
    execute_job,
    host_path,
    process_one,
    public_job,
    queue_loop,
    reconcile_qbittorrent,
)

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
OTHER = "magnet:?xt=urn:btih:" + "b" * 40
TORRENT = b"d4:infod6:lengthi5e4:name4:file12:piece lengthi16384e6:pieces20:" + b"p" * 20 + b"ee"
BASE = "http://localhost:8080"
DEAD_PID = 2**22 + 1


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "state" / "jobs.sqlite3")


@pytest.fixture
def qbittorrent(monkeypatch):
    monkeypatch.setenv("QB_WEBUI", BASE)
    monkeypatch.setenv("QB_PASSWORD", "testpass")
    monkeypatch.setattr(client, "_session", None)
    monkeypatch.setattr(client, "_session_key", None)


def set_row(store, identifier, **values):
    columns = ",".join(key + "=?" for key in values)
    with store.connect() as connection:
        connection.execute(f"UPDATE jobs SET {columns} WHERE id=?", (*values.values(), identifier))


def test_store_is_private_and_uses_wal(store):
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    with store.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM jobs WHERE status='pending' ORDER BY created LIMIT 1"
        ).fetchone()
    assert "jobs_status_created" in plan["detail"]


def test_enqueue_deduplicates_active_jobs(store):
    first = store.enqueue(MAGNET)
    assert store.enqueue(MAGNET)["id"] == first["id"]
    assert store.enqueue(MAGNET, lite=True)["id"] != first["id"]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_failure_can_be_resubmitted(store, status):
    job = store.enqueue(MAGNET)
    store.update(job["id"], status, error="broken")
    again = store.enqueue(MAGNET)
    assert again["id"] == job["id"]
    assert (again["status"], again["error"]) == ("pending", None)


def test_succeeded_job_is_not_repeated(store):
    job = store.enqueue(MAGNET)
    store.update(job["id"], "succeeded")
    assert store.enqueue(MAGNET)["status"] == "succeeded"


def test_cancelled_job_with_live_worker_is_not_requeued(store):
    job = store.enqueue(MAGNET)
    assert store.claim()["id"] == job["id"]
    store.cancel(job["id"])
    assert store.enqueue(MAGNET)["status"] == "cancelled"
    store.release(job["id"])
    assert store.enqueue(MAGNET)["status"] == "pending"


def test_cancelled_job_with_dead_owner_can_be_resubmitted(store):
    job = store.enqueue(MAGNET)
    store.claim()
    store.cancel(job["id"])
    set_row(store, job["id"], owner_pid=DEAD_PID, owner_start="1")
    assert store.enqueue(MAGNET)["status"] == "pending"


def test_enqueue_many_is_all_or_nothing(store):
    with pytest.raises(ValueError):
        store.enqueue_many([MAGNET, "magnet:?xt=urn:btih:12345"])
    assert store.list() == []
    assert len(store.enqueue_many([MAGNET, OTHER])) == 2


@pytest.mark.parametrize("source", ["ftp://x/y", "http://", "/no/such/file.torrent", "plain text"])
def test_invalid_sources_are_rejected(store, source):
    with pytest.raises(ValueError):
        store.enqueue(source)


def test_local_torrent_is_spooled_privately(store, tmp_path):
    original = tmp_path / "a.torrent"
    original.write_bytes(TORRENT)
    job = store.enqueue(str(original))
    original.unlink()
    spooled = Path(job["source"])
    assert spooled.read_bytes() == TORRENT
    assert stat.S_IMODE(spooled.stat().st_mode) == 0o600
    assert not list(spooled.parent.glob("*.tmp"))


def test_claim_is_oldest_first_and_exclusive(store):
    first = store.enqueue(MAGNET)
    second = store.enqueue(OTHER)
    set_row(store, second["id"], created=first["created"] + 1)
    assert store.claim()["id"] == first["id"]
    assert store.claim()["id"] == second["id"]
    assert store.claim() is None


def test_concurrent_claims_never_share_a_job(store):
    for index in range(20):
        store.enqueue("magnet:?xt=urn:btih:" + f"{index:040x}")
    claimed = []

    def worker():
        while True:
            job = store.claim()
            if job is None:
                return
            claimed.append(job["id"])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(claimed) == 20 == len(set(claimed))


def test_claim_recovers_jobs_of_dead_owners_only(store):
    job = store.enqueue(MAGNET)
    store.claim()
    assert store.claim() is None
    set_row(store, job["id"], owner_pid=DEAD_PID, owner_start="1")
    assert store.claim()["id"] == job["id"]


def test_cancel(store):
    job = store.enqueue(MAGNET)
    assert store.cancel(job["id"]) is True
    assert store.cancel(job["id"]) is False
    with pytest.raises(KeyError):
        store.cancel("missing")


def test_late_worker_result_does_not_override_cancellation(store):
    job = store.enqueue(MAGNET)
    store.cancel(job["id"])
    store.update(job["id"], "succeeded")
    assert store.get(job["id"])["status"] == "cancelled"


def test_register_files_stays_inside_download_root(store, tmp_path):
    root = Path(get_dl_dir())
    root.mkdir(parents=True, exist_ok=True)
    inside = root / "inside.bin"
    inside.write_bytes(b"123")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x")
    job = store.enqueue(MAGNET)
    store.register_files(job["id"], [inside, outside, root / "missing"])
    assert [item["path"] for item in store.files()] == ["inside.bin"]
    assert store.file_entry("inside.bin")["size"] == 3
    assert store.file_entry("outside.bin") is None


def test_public_job_redacts_and_hides_internals(store, monkeypatch):
    monkeypatch.setenv("JACKETT_API_KEY", "supersecretvalue1234")
    job = store.enqueue("http://example.test/a?apikey=supersecretvalue1234")
    view = public_job(job)
    assert "owner_pid" not in view and "fingerprint" not in view
    assert "supersecretvalue1234" not in view["source"]


def test_execute_job_outcomes(store, monkeypatch):
    import helm.torrents.lite_downloader as downloader

    job = store.enqueue(MAGNET, lite=True)
    assert execute_job(store, job["id"]) == 1  # Not claimed.
    store.claim()
    monkeypatch.setattr(downloader, "download_torrent", lambda source, on_complete: False)
    assert execute_job(store, job["id"]) == 1
    assert store.get(job["id"])["status"] == "failed"

    store.release(job["id"])
    store.enqueue(MAGNET, lite=True)
    store.claim()
    monkeypatch.setattr(downloader, "download_torrent", lambda source, on_complete: on_complete([]) or True)
    assert execute_job(store, job["id"]) == 0
    assert store.get(job["id"])["status"] == "succeeded"


@responses.activate
def test_execute_job_submits_with_shared_and_job_tags(store, qbittorrent):
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.POST, BASE + "/api/v2/torrents/add", status=200)
    job = store.enqueue(MAGNET)
    store.claim()
    assert execute_job(store, job["id"]) == 0
    assert store.get(job["id"])["status"] == "submitted"
    assert ("tags=helm%2Chelm-job-" + job["id"]) in responses.calls[-1].request.body


def write_worker(tmp_path, body):
    """Install a fake helm.services.jobs worker module found through PYTHONPATH."""
    package = tmp_path / "fake" / "helm" / "services"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "jobs.py").write_text(body)
    return str(tmp_path / "fake")


def patch_environment(monkeypatch, path):
    monkeypatch.setattr(jobs, "child_environment", lambda: {**os.environ, "PYTHONPATH": path})


def test_worker_that_writes_nothing_is_marked_failed(store, tmp_path, monkeypatch):
    patch_environment(monkeypatch, write_worker(tmp_path, "raise SystemExit(0)\n"))
    job = store.enqueue(MAGNET)
    assert process_one(store) is True
    final = store.get(job["id"])
    assert final["status"] == "failed" and "job log" in final["error"]
    assert final["owner_pid"] is None


def test_process_one_with_empty_queue(store):
    assert process_one(store) is False


def test_stop_requeues_running_job_and_kills_worker(store, tmp_path, monkeypatch):
    marker = tmp_path / "pid"
    body = f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    patch_environment(monkeypatch, write_worker(tmp_path, body))
    job = store.enqueue(MAGNET)
    stop = threading.Event()
    thread = threading.Thread(target=process_one, args=(store, stop))
    thread.start()
    deadline = time.monotonic() + 10
    while not (marker.exists() and marker.read_text()) and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int(marker.read_text())
    stop.set()
    thread.join(10)
    assert not thread.is_alive()
    final = store.get(job["id"])
    assert (final["status"], final["owner_pid"]) == ("pending", None)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancellation_stops_worker(store, tmp_path, monkeypatch):
    marker = tmp_path / "pid"
    body = f"import os,time\nopen({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    patch_environment(monkeypatch, write_worker(tmp_path, body))
    job = store.enqueue(MAGNET)
    thread = threading.Thread(target=process_one, args=(store, None, 0.05))
    thread.start()
    deadline = time.monotonic() + 10
    while not (marker.exists() and marker.read_text()) and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int(marker.read_text())
    store.cancel(job["id"])
    thread.join(10)
    assert not thread.is_alive()
    assert store.get(job["id"])["status"] == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_spawn_failure_fails_the_job(store, monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("cannot fork")

    monkeypatch.setattr(jobs.subprocess, "Popen", refuse)
    job = store.enqueue(MAGNET)
    assert process_one(store) is True
    assert store.get(job["id"])["status"] == "failed"
    assert store.get(job["id"])["owner_pid"] is None


def test_queue_loop_survives_unexpected_errors(store, monkeypatch):
    calls = []
    stop = threading.Event()

    def explode(*args):
        calls.append(1)
        if len(calls) == 3:
            stop.set()
            return False
        raise RuntimeError("bad job")

    monkeypatch.setattr(jobs, "process_one", explode)
    monkeypatch.setattr(jobs, "min", lambda *a: 0.001, raising=False)
    queue_loop(store, stop)
    assert len(calls) == 3


def test_queue_loop_survives_reconcile_errors(store, monkeypatch):
    stop = threading.Event()
    seen = []

    def broken(_):
        seen.append(1)
        stop.set()
        raise RuntimeError("qBittorrent offline")

    monkeypatch.setattr(jobs, "process_one", lambda *a: False)
    monkeypatch.setattr(jobs, "reconcile_qbittorrent", broken)
    queue_loop(store, stop)
    assert seen == [1]


def test_host_path_maps_container_mount(monkeypatch):
    monkeypatch.delenv("QB_DOWNLOADS_ROOT", raising=False)
    assert host_path("/downloads/sub", "a/b.iso") == Path(get_dl_dir()) / "sub/a/b.iso"
    assert host_path("/downloads/", "b.iso") == Path(get_dl_dir()) / "b.iso"
    assert host_path("/srv/native", "b.iso") == Path("/srv/native/b.iso")
    monkeypatch.setenv("QB_DOWNLOADS_ROOT", "/data")
    assert host_path("/data", "b.iso") == Path(get_dl_dir()) / "b.iso"


@responses.activate
def test_reconcile_uses_one_list_call_and_records_host_paths(store, qbittorrent):
    root = Path(get_dl_dir())
    root.mkdir(parents=True, exist_ok=True)
    (root / "done.iso").write_bytes(b"12345")
    done = store.enqueue(MAGNET)
    active = store.enqueue(OTHER)
    store.update(done["id"], "submitted")
    store.update(active["id"], "submitted")
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(
        responses.GET,
        BASE + "/api/v2/torrents/info",
        json=[
            {"hash": "h1", "progress": 1, "save_path": "/downloads", "tags": "helm, helm-job-" + done["id"]},
            {"hash": "h2", "progress": 0.5, "save_path": "/downloads", "tags": "helm, helm-job-" + active["id"]},
        ],
    )
    responses.add(
        responses.GET,
        BASE + "/api/v2/torrents/files",
        json=[{"name": "done.iso", "progress": 1}, {"name": "partial.bin", "progress": 0.2}],
    )
    reconcile_qbittorrent(store)
    assert store.get(done["id"])["status"] == "succeeded"
    assert store.get(active["id"])["status"] == "submitted"
    assert [item["path"] for item in store.files()] == ["done.iso"]
    assert sum(call.request.url.split("?")[0].endswith("/info") for call in responses.calls) == 1
    assert "tag=helm" in next(call.request.url for call in responses.calls if "/info" in call.request.url)


@responses.activate
def test_reconcile_skips_network_without_submitted_jobs(store, qbittorrent):
    store.enqueue(MAGNET)
    reconcile_qbittorrent(store)
    assert len(responses.calls) == 0


@responses.activate
def test_reconcile_fails_jobs_removed_from_qbittorrent_after_grace(store, qbittorrent):
    recent = store.enqueue(MAGNET)
    removed = store.enqueue(OTHER)
    store.update(recent["id"], "submitted")
    store.update(removed["id"], "submitted")
    set_row(store, removed["id"], updated=time.time() - jobs.MISSING_GRACE - 1)
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.GET, BASE + "/api/v2/torrents/info", json=[])
    reconcile_qbittorrent(store)
    assert store.get(recent["id"])["status"] == "submitted"
    assert store.get(removed["id"])["status"] == "failed"


@responses.activate
def test_cancelling_submitted_job_removes_torrent(store, qbittorrent):
    job = store.enqueue(MAGNET)
    store.update(job["id"], "submitted")
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.GET, BASE + "/api/v2/torrents/info", json=[{"hash": "h1"}])
    responses.add(responses.POST, BASE + "/api/v2/torrents/delete", status=200)
    assert cancel_job(store, job["id"]) is True
    assert "hashes=h1" in responses.calls[-1].request.body


def test_cancelling_lite_job_needs_no_qbittorrent(store):
    job = store.enqueue(MAGNET, lite=True)
    assert cancel_job(store, job["id"]) is True
    with pytest.raises(KeyError):
        cancel_job(store, "missing")


@responses.activate
def test_cancel_succeeds_when_qbittorrent_is_offline(store, qbittorrent):
    job = store.enqueue(MAGNET)
    store.update(job["id"], "submitted")
    assert cancel_job(store, job["id"]) is True
    assert store.get(job["id"])["status"] == "cancelled"


def test_orphaned_worker_exits_when_its_parent_dies(tmp_path):
    src = str(Path(jobs.__file__).resolve().parents[2])
    marker = tmp_path / "pid"
    child = (
        "import os,threading,time\n"
        "from helm.services.jobs import exit_with_parent\n"
        "threading.Thread(target=exit_with_parent,args=(os.getppid(),0.05),daemon=True).start()\n"
        f"open({str(marker)!r},'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    parent = f"import subprocess,sys\nsubprocess.Popen([sys.executable,'-c',{child!r}])\nimport time\ntime.sleep(30)\n"
    process = subprocess.Popen([sys.executable, "-c", parent], env={**os.environ, "PYTHONPATH": src})
    deadline = time.monotonic() + 15
    while not (marker.exists() and marker.read_text()) and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int(marker.read_text())
    process.kill()
    process.wait()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    os.kill(pid, 9)
    pytest.fail("Orphaned worker kept running")
