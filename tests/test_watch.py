import errno
import threading
import time

import pytest

import helm.services.watch as watch
from helm.services.jobs import JobStore
from helm.services.watch import run_watch, safe_scan, scan_directory

MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
OTHER = "magnet:?xt=urn:btih:" + "b" * 40
TORRENT = b"d4:infod6:lengthi5e4:name4:file12:piece lengthi16384e6:pieces20:" + b"p" * 20 + b"ee"


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "state" / "jobs.sqlite3")


@pytest.fixture
def inbox(tmp_path):
    path = tmp_path / "inbox"
    path.mkdir()
    return path


def settle_scan(inbox, store, lite=False):
    return scan_directory(inbox, store, scan_directory(inbox, store, {}, lite, settle=1, now=0), lite, settle=1, now=2)


def test_file_is_not_ingested_until_stable(inbox, store):
    path = inbox / "a.magnet"
    path.write_text(MAGNET)
    observed = scan_directory(inbox, store, {}, settle=1, now=0)
    assert store.list() == [] and path.exists()
    path.write_text(MAGNET + "\n")  # Still being written.
    observed = scan_directory(inbox, store, observed, settle=1, now=2)
    assert store.list() == [] and path.exists()
    scan_directory(inbox, store, observed, settle=1, now=4)
    assert len(store.list()) == 1 and not path.exists()


def test_valid_text_file_moves_to_processed(inbox, store):
    (inbox / "a.txt").write_text(f"# comment\n\n{MAGNET}\n  {OTHER}  \n")
    assert settle_scan(inbox, store) == {}
    assert len(store.list()) == 2
    assert len(list((inbox / ".processed").iterdir())) == 1


def test_torrent_file_survives_being_moved(inbox, store):
    (inbox / "a.torrent").write_bytes(TORRENT)
    settle_scan(inbox, store, lite=True)
    job = store.list()[0]
    assert job["lite"] == 1
    assert ".processed" not in job["source"]
    assert open(job["source"], "rb").read() == TORRENT


def test_one_bad_line_queues_nothing(inbox, store):
    (inbox / "a.txt").write_text(f"{MAGNET}\nmagnet:?xt=urn:btih:12345\n")
    settle_scan(inbox, store)
    assert store.list() == []
    failed = list((inbox / ".failed").iterdir())
    assert len(failed) == 2
    assert any(path.name.endswith(".error.txt") for path in failed)


@pytest.mark.parametrize(
    "name,content",
    [("a.txt", b"# only a comment\n"), ("a.txt", b"ftp://x/y\n"), ("a.txt", b"\xff\xfe\x00"), ("a.torrent", b"junk")],
)
def test_invalid_inputs_are_quarantined(inbox, store, name, content):
    (inbox / name).write_bytes(content)
    settle_scan(inbox, store)
    assert store.list() == []
    assert not (inbox / name).exists()
    assert list((inbox / ".failed").iterdir())


def test_oversized_input_is_quarantined(inbox, store, monkeypatch):
    monkeypatch.setattr(watch, "MAX_METADATA", 4)
    (inbox / "a.txt").write_text(MAGNET)
    settle_scan(inbox, store)
    assert store.list() == []


def test_unrelated_files_and_symlinks_are_ignored(inbox, store, tmp_path):
    (inbox / "notes.md").write_text(MAGNET)
    target = tmp_path / "target.magnet"
    target.write_text(MAGNET)
    (inbox / "link.magnet").symlink_to(target)
    (inbox / "dir.txt").mkdir()
    assert settle_scan(inbox, store) == {}
    assert store.list() == []
    assert (inbox / "notes.md").exists() and target.exists()


def test_symlinked_processed_directory_is_refused(inbox, store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (inbox / ".processed").symlink_to(outside)
    (inbox / "a.magnet").write_text(MAGNET)
    settle_scan(inbox, store)
    assert list(outside.iterdir()) == []


def test_file_vanishing_during_scan_is_skipped(inbox, store, monkeypatch):
    real = watch.Path.stat

    def vanishing(self, *args, **kwargs):
        if self.name == "gone.magnet":
            raise FileNotFoundError(errno.ENOENT, "vanished", str(self))
        return real(self, *args, **kwargs)

    (inbox / "gone.magnet").write_text(MAGNET)
    monkeypatch.setattr(watch.Path, "stat", vanishing)
    assert scan_directory(inbox, store, {}, settle=0, now=0) == {}


def test_safe_scan_keeps_state_when_directory_is_unavailable(tmp_path, store):
    observed = {"x": 1}
    assert safe_scan(tmp_path / "missing", store, observed, False, 1) is observed


def test_run_watch_once_enqueue_only(tmp_path, inbox, monkeypatch):
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(watch, "JobStore", lambda: store)
    (inbox / "a.magnet").write_text(MAGNET)
    assert run_watch(inbox, lite=True, interval=0.05, once=True, enqueue_only=True) == 0
    assert [job["status"] for job in store.list()] == ["pending"]


def test_run_watch_once_processes_queue(tmp_path, inbox, monkeypatch):
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(watch, "JobStore", lambda: store)
    processed = iter([True, False])
    monkeypatch.setattr(watch, "process_one", lambda *a: next(processed))
    assert run_watch(inbox, interval=0.01, once=True) == 0
    assert next(processed, "done") == "done"


def test_run_watch_loop_stops_and_joins_worker(tmp_path, inbox, monkeypatch):
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    monkeypatch.setattr(watch, "JobStore", lambda: store)
    monkeypatch.setattr(watch, "queue_loop", lambda store, stop: stop.wait())
    stop = threading.Event()
    (inbox / "a.magnet").write_text(MAGNET)
    thread = threading.Thread(target=run_watch, args=(inbox,), kwargs={"interval": 0.02, "stop": stop})
    thread.start()
    deadline = time.monotonic() + 5
    while not store.list() and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()
    assert len(store.list()) == 1


def test_interval_must_be_positive(inbox):
    with pytest.raises(ValueError):
        run_watch(inbox, interval=0)
