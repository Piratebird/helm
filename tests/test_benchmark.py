import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from helm.core import benchmark


def fake_proc(root, pid, parent, name, pss):
    path = root / str(pid)
    path.mkdir(parents=True)
    (path / "stat").write_text(f"{pid} ({name}) S {parent} 0 0 0")
    (path / "comm").write_text(name + "\n")
    if pss is not None:
        (path / "smaps_rollup").write_text(f"Rss: 1000 kB\nPss: {pss} kB\n")


def test_pss_units_and_invalid_samples():
    assert benchmark.parse_pss("Pss: 12 kB\n") == 12 * 1024
    with pytest.raises(ValueError):
        benchmark.parse_pss("Rss: 12 kB\n")
    with pytest.raises(ValueError):
        benchmark.parse_pss("Pss: 12 MB\n")


def test_memory_accounting_uses_disjoint_process_sets(tmp_path):
    fake_proc(tmp_path, 1, 0, "workload", 10)
    fake_proc(tmp_path, 2, 1, "worker", 20)
    fake_proc(tmp_path, 3, 0, "dockerd", 30)
    fake_proc(tmp_path, 4, 3, "helper", 40)
    fake_proc(tmp_path, 5, 3, "container", 50)
    fake_proc(tmp_path, 6, 5, "service", 60)
    sample = benchmark.memory_sample(1, [5], True, tmp_path)
    assert sample["workload_pss_bytes"] == 30 * 1024
    assert sample["attached_stack_pss_bytes"] == 110 * 1024
    assert sample["engine_pss_bytes"] == 70 * 1024
    assert sample["accounted_pss_bytes"] == 210 * 1024
    assert sample["complete"] is True


def test_missing_memory_is_flagged_instead_of_assumed_complete(tmp_path):
    fake_proc(tmp_path, 1, 0, "workload", None)
    sample = benchmark.memory_sample(1, proc_root=tmp_path)
    assert sample["missing_pids"] == [1]
    assert sample["complete"] is False


def test_summary_excludes_incomplete_samples():
    def sample(value, complete):
        return {
            "phase": "idle",
            "complete": complete,
            **{
                key: value
                for key in ("workload_pss_bytes", "attached_stack_pss_bytes", "engine_pss_bytes", "accounted_pss_bytes")
            },
        }

    summary = benchmark.summarize([sample(10, True), sample(30, True), sample(999, False)])
    assert summary["idle"]["accounted_pss_bytes"] == {"median": 20, "peak": 30}
    assert summary["idle"]["incomplete_samples"] == 1
    assert benchmark.summarize([sample(999, False)])["idle"]["accounted_pss_bytes"]["median"] is None


def test_container_metadata_drops_secrets_and_records_limits(monkeypatch):
    raw = [
        {
            "Id": "abc",
            "Name": "helm-test",
            "State": {"Pid": 123},
            "Image": "sha256:image",
            "Config": {"Image": "example/image", "Env": ["PASSWORD=private"]},
            "HostConfig": {"Memory": 1000, "NanoCpus": 500000000},
        }
    ]

    def run(command, **kwargs):
        if "inspect" in command:
            output = json.dumps(raw)
        elif "version" in command:
            output = "test-version"
        else:
            assert "label=com.docker.compose.project=helm" in command
            output = "abc\n"
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(benchmark.subprocess, "run", run)
    metadata = benchmark.container_metadata("docker", "helm")
    assert "private" not in json.dumps(metadata)
    assert metadata["containers"][0]["memory_limit_bytes"] == 1000
    assert metadata["containers"][0]["image_id"] == "sha256:image"


def test_local_download_validates_generated_payload_and_pauses(monkeypatch, tmp_path):
    seed, client = Mock(), Mock()
    handle = Mock()
    handle.status.return_value = SimpleNamespace(is_seeding=True)

    def add(params):
        source = tmp_path / "seed" / "payload.bin"
        (Path(params.save_path) / "payload.bin").write_bytes(source.read_bytes())
        return handle

    client.add_torrent.side_effect = add
    sessions = iter([seed, client])
    create = Mock()
    create.generate.return_value = {}
    module = SimpleNamespace(
        file_storage=lambda: object(),
        add_files=lambda *args: None,
        create_torrent=lambda *args: create,
        set_piece_hashes=lambda *args: None,
        torrent_info=lambda value: value,
        session=lambda settings: next(sessions),
        add_torrent_params=lambda: SimpleNamespace(flags=0),
        torrent_flags=SimpleNamespace(seed_mode=1, paused=2, auto_managed=4),
        version="fixture",
    )
    monkeypatch.setitem(sys.modules, "libtorrent", module)
    monkeypatch.setattr(benchmark.time, "sleep", lambda seconds: None)
    result = benchmark.local_download(tmp_path, 4096, 1)
    assert result["payload_bytes"] == 4096
    assert len(result["sha256"]) == 64
    seed.pause.assert_called_once()
    client.pause.assert_called_once()


def test_cli_writes_raw_samples_and_reproducible_metadata(monkeypatch, tmp_path):
    trial = {"samples": [], "events": [], "summary": {}}
    monkeypatch.setattr(benchmark, "run_trial", lambda *args: trial)
    output = tmp_path / "samples.json"
    assert benchmark.main(["--output", str(output), "--repeats", "2"]) == 0
    data = json.loads(output.read_text())
    assert data["schema_version"] == 1
    assert len(data["trials"]) == 2
    assert data["attachment"] is None
    assert "PSS" in data["metric"]
    assert data["environment"]["python"]


@pytest.mark.parametrize(
    "arguments", [["--repeats", "0"], ["--interval", "0"], ["--payload-mib", "0"], ["--timeout", "0"]]
)
def test_invalid_benchmark_arguments(tmp_path, arguments):
    with pytest.raises(SystemExit) as error:
        benchmark.main(["--output", str(tmp_path / "unused.json"), *arguments])
    assert error.value.code == 2
    assert not (tmp_path / "unused.json").exists()


@pytest.mark.parametrize("filename", ["local-loopback.json", "local-loopback-workers2.json"])
def test_published_raw_samples_have_valid_phases_and_checksums(filename):
    path = Path(__file__).resolve().parents[1] / "docs" / "benchmarks" / filename
    data = json.loads(path.read_text())
    assert data["mode"] == "local_fixture_search_and_loopback_bittorrent"
    for trial in data["trials"]:
        assert {"idle", "search", "download", "teardown", "exited"} <= {sample["phase"] for sample in trial["samples"]}
        assert trial["summary"] == benchmark.summarize(trial["samples"])
        downloads = [event for event in trial["events"] if "sha256" in event]
        assert len(downloads) == 1
        assert downloads[0]["payload_bytes"] == data["payload_bytes"]
        assert len(downloads[0]["sha256"]) == 64
        searches = [event for event in trial["events"] if "result_count" in event]
        assert searches[0]["result_count"] == data["fixture_plugins"] * data["fixture_results_per_plugin"]


def test_benchmark_deadline_stops_worker(monkeypatch):
    from helm.core import search_runner

    process = Mock()
    process.poll.return_value = None
    process.pid = 12345
    monkeypatch.setattr(benchmark.subprocess, "Popen", lambda *a, **kw: process)
    clock = iter([0, 100])
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: next(clock))
    stop = Mock()
    monkeypatch.setattr(search_runner, "stop_worker", stop)
    with pytest.raises(TimeoutError):
        benchmark.run_trial(0.05, 1024, 1)
    stop.assert_called_once_with(process)


def test_sampler_tracks_real_worker_protocol_and_exit(monkeypatch):
    real_popen = benchmark.subprocess.Popen
    code = "import json,time\nfor phase in ('idle','search','download','teardown'):\n print(json.dumps({'phase': phase}),flush=True)\n time.sleep(0.08)\n"

    def fixture_process(command, **kwargs):
        return real_popen([sys.executable, "-c", code], **kwargs)

    monkeypatch.setattr(benchmark.subprocess, "Popen", fixture_process)
    trial = benchmark.run_trial(0.01, 1024, 1)
    assert {"idle", "search", "download", "teardown", "exited"} <= {s["phase"] for s in trial["samples"]}
    assert trial["samples"][-1]["workload_pss_bytes"] == 0
    assert trial["summary"] == benchmark.summarize(trial["samples"])
