"""Repeatable Linux PSS sampling of local search and loopback BitTorrent work.

No public trackers or external torrent content are used. Optional container
attachment is read-only and is reported separately from the local workload.
"""

import argparse
import gc
import hashlib
import json
import os
import platform
import resource
import select
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from helm.core.runtime import child_environment

ENGINE_NAMES = {"dockerd", "containerd", "conmon", "pasta", "slirp4netns", "rootlesskit"}


def parse_pss(text):
    for line in text.splitlines():
        fields = line.split()
        if fields and fields[0] == "Pss:":
            if len(fields) != 3 or fields[2] != "kB":
                raise ValueError("Expected Pss in Linux kB units")
            return int(fields[1]) * 1024
    raise ValueError("Pss is missing")


def process_snapshot(proc_root=Path("/proc")):
    processes = {}
    for directory in proc_root.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            stat = (directory / "stat").read_text()
            tail = stat.rsplit(")", 1)[1].split()
            name = (directory / "comm").read_text().strip()
            processes[int(directory.name)] = (int(tail[1]), name)
        except (OSError, ValueError, IndexError):
            continue
    return processes


def descendants(roots, processes):
    selected = set(roots)
    while True:
        more = {pid for pid, (parent, _) in processes.items() if parent in selected}
        combined = selected | more
        if combined == selected:
            return selected
        selected = combined


def memory_sample(root_pid, container_pids=(), include_engine=False, proc_root=Path("/proc")):
    processes = process_snapshot(proc_root)
    workload = descendants([root_pid], processes)
    stack = descendants(container_pids, processes) - workload
    engine_roots = [pid for pid, (_, name) in processes.items() if name in ENGINE_NAMES] if include_engine else []
    overhead = descendants(engine_roots, processes) - workload - stack
    groups = {"workload": workload, "attached_stack": stack, "engine": overhead}
    sample = {"missing_pids": []}
    for name, pids in groups.items():
        total = 0
        for pid in pids:
            try:
                total += parse_pss((proc_root / str(pid) / "smaps_rollup").read_text())
            except (OSError, ValueError):
                if pid in processes:
                    sample["missing_pids"].append(pid)
        sample[name + "_pss_bytes"] = total
    sample["accounted_pss_bytes"] = sum(sample[name + "_pss_bytes"] for name in groups)
    sample["complete"] = not sample["missing_pids"]
    return sample


def summarize(samples):
    summary = {}
    for phase in sorted({sample["phase"] for sample in samples}):
        selected = [sample for sample in samples if sample["phase"] == phase]
        summary[phase] = {"sample_count": len(selected), "incomplete_samples": sum(not s["complete"] for s in selected)}
        for metric in ("workload_pss_bytes", "attached_stack_pss_bytes", "engine_pss_bytes", "accounted_pss_bytes"):
            values = [sample[metric] for sample in selected if sample["complete"]]
            summary[phase][metric] = {
                "median": statistics.median(values) if values else None,
                "peak": max(values) if values else None,
            }
    return summary


def container_metadata(engine, project):
    def command(*args):
        return subprocess.run([engine, *args], check=True, capture_output=True, text=True, timeout=15).stdout

    ids = command("ps", "-q", "--filter", f"label=com.docker.compose.project={project}").split()
    containers = []
    for identifier in ids:
        raw = json.loads(command("inspect", identifier))[0]
        host = raw.get("HostConfig", {})
        # Deliberately exclude environment variables, mounts and credentials.
        containers.append(
            {
                "id": raw["Id"],
                "pid": raw["State"]["Pid"],
                "image_id": raw.get("Image"),
                "image": raw.get("Config", {}).get("Image"),
                "memory_limit_bytes": host.get("Memory"),
                "nano_cpus": host.get("NanoCpus"),
                "name": raw.get("Name"),
            }
        )
    return {
        "engine": engine,
        "version": command("version", "--format", "{{.Server.Version}}").strip(),
        "project": project,
        "containers": containers,
        "attachment": "read_only; local workload does not exercise these services",
    }


def cpu_usage():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"user": own.ru_utime + children.ru_utime, "system": own.ru_stime + children.ru_stime}


def cpu_delta(before):
    after = cpu_usage()
    user = max(0, after["user"] - before["user"])
    system = max(0, after["system"] - before["system"])
    return {"cpu_user_seconds": user, "cpu_system_seconds": system, "cpu_seconds": user + system}


def phase(name, **details):
    print(json.dumps({"phase": name, **details}), flush=True)


def local_download(directory, size_bytes, timeout):
    import libtorrent as lt

    seed_dir = directory / "seed"
    dest_dir = directory / "download"
    seed_dir.mkdir()
    dest_dir.mkdir()
    source = seed_dir / "payload.bin"
    # Deterministic generated content, never downloaded from an external source.
    block = bytes(range(256)) * 4096
    with source.open("wb") as stream:
        remaining = size_bytes
        while remaining:
            data = block[: min(remaining, len(block))]
            stream.write(data)
            remaining -= len(data)
    files = lt.file_storage()
    lt.add_files(files, str(source))
    torrent = lt.create_torrent(files, 256 * 1024)
    lt.set_piece_hashes(torrent, str(seed_dir))
    info = lt.torrent_info(torrent.generate())
    settings = {
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        "enable_outgoing_utp": False,
        "enable_incoming_utp": False,
        "allow_multiple_connections_per_ip": True,
    }
    seed = lt.session(settings)
    client = lt.session({**settings, "download_rate_limit": 2 * 1024 * 1024})
    try:
        seed_params = lt.add_torrent_params()
        seed_params.ti = info
        seed_params.save_path = str(seed_dir)
        seed_params.flags |= lt.torrent_flags.seed_mode
        seed_params.flags &= ~lt.torrent_flags.paused
        seed_params.flags &= ~lt.torrent_flags.auto_managed
        seed.add_torrent(seed_params)
        download_params = lt.add_torrent_params()
        download_params.ti = info
        download_params.save_path = str(dest_dir)
        download_params.flags &= ~lt.torrent_flags.paused
        download_params.flags &= ~lt.torrent_flags.auto_managed
        handle = client.add_torrent(download_params)
        deadline = time.monotonic() + timeout
        while not handle.status().is_seeding:
            if time.monotonic() >= deadline:
                raise TimeoutError("Loopback transfer did not complete")
            handle.connect_peer(("127.0.0.1", seed.listen_port()))
            time.sleep(0.05)
        # Flush data to disk before validating the generated payload.
        handle.flush_cache()
        time.sleep(0.1)
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        actual = hashlib.sha256((dest_dir / "payload.bin").read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Downloaded payload checksum mismatch")
        return {"payload_bytes": size_bytes, "sha256": actual, "libtorrent_version": binding_version(lt)}
    finally:
        seed.pause()
        client.pause()


def binding_version(binding):
    # Termux's binding exposes __version__ but no version attribute.
    return getattr(binding, "__version__", None) or getattr(binding, "version", None) or "unknown"


def workload(payload_bytes, timeout, fixture_plugins=12, fixture_results=100, search_workers=6):
    from helm.core.lite_plugin_loader import plugin_jobs
    from helm.core.search_runner import run_searches

    with tempfile.TemporaryDirectory(prefix="helm-benchmark-workload-") as temporary:
        directory = Path(temporary)
        plugins = directory / "plugins"
        plugins.mkdir()
        for number in range(fixture_plugins):
            (plugins / f"fixture{number}.py").write_text(
                "from novaprinter import prettyPrinter\nclass fixture:\n"
                f"    name = 'Local fixture {number}'\n"
                "    def search(self, query, cat='all'):\n"
                f"        for index in range({fixture_results}):\n"
                "            prettyPrinter({'name': query + str(index), 'link': 'magnet:?xt=urn:btih:' + '%040x' % index, 'seeds': 10})\n"
            )
        cpu = cpu_usage()
        phase("idle")
        time.sleep(0.4)
        phase("idle", **cpu_delta(cpu))
        cpu = cpu_usage()
        phase("search")
        started = time.monotonic()
        results = run_searches(plugin_jobs([str(plugins)]), "generated fixture", "all", max_workers=search_workers)
        if len(results) != fixture_plugins * fixture_results:
            raise RuntimeError("Fixture search failed")
        phase("search", search_seconds=time.monotonic() - started, result_count=len(results), **cpu_delta(cpu))
        time.sleep(0.2)
        cpu = cpu_usage()
        phase("download")
        started = time.monotonic()
        details = local_download(directory, payload_bytes, timeout)
        phase("download", download_seconds=time.monotonic() - started, **details, **cpu_delta(cpu))
        cpu = cpu_usage()
        phase("teardown")
        del results
        gc.collect()
        time.sleep(0.4)
        phase("teardown", **cpu_delta(cpu))


def run_trial(
    interval,
    payload_bytes,
    timeout,
    attachment=None,
    include_engine=False,
    fixture_plugins=12,
    fixture_results=100,
    search_workers=6,
):
    environment = child_environment()
    with tempfile.TemporaryDirectory(prefix="helm-benchmark-state-") as temporary:
        environment.update(HELM_CONFIG_DIR=temporary + "/config", HELM_STATE_DIR=temporary + "/state")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "helm.core.benchmark",
                "--worker",
                str(payload_bytes),
                str(timeout),
                str(fixture_plugins),
                str(fixture_results),
                str(search_workers),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=True,
        )
        from helm.core.search_runner import stop_worker

        samples, events = [], []
        current = "startup"
        pending_bytes = b""
        started = time.monotonic()
        pids = [item["pid"] for item in (attachment or {}).get("containers", [])]
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout + 40:
                    raise TimeoutError("Benchmark workload exceeded its budget")
                ready, _, _ = select.select([process.stdout], [], [], interval)
                if ready:
                    pending_bytes += os.read(process.stdout.fileno(), 65536)
                    while b"\n" in pending_bytes:
                        line, pending_bytes = pending_bytes.split(b"\n", 1)
                        if line:
                            event = json.loads(line)
                            current = event["phase"]
                            events.append(event)
                sample = memory_sample(process.pid, pids, include_engine)
                samples.append({"elapsed_seconds": time.monotonic() - started, "phase": current, **sample})
            stdout, stderr = process.communicate(timeout=1)
            for line in stdout.splitlines():
                events.append(json.loads(line))
            if process.returncode:
                raise RuntimeError("Benchmark workload failed: " + stderr[-4096:])
        finally:
            stop_worker(process)
        # Post-exit sample measures reaped workload processes, not host engine shutdown.
        samples.append(
            {
                "elapsed_seconds": time.monotonic() - started,
                "phase": "exited",
                **memory_sample(process.pid, pids, include_engine),
            }
        )
        return {"samples": samples, "events": events, "summary": summarize(samples)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--payload-mib", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--fixture-plugins", type=int, default=12)
    parser.add_argument("--fixture-results", type=int, default=100)
    parser.add_argument("--search-workers", type=int, choices=range(1, 13), default=6)
    parser.add_argument("--engine", choices=["docker", "podman"])
    parser.add_argument("--project", default="helm")
    parser.add_argument("--include-engine", action="store_true")
    args = parser.parse_args(argv)
    if (
        args.repeats < 1
        or args.interval <= 0
        or args.payload_mib < 1
        or args.timeout <= 0
        or args.fixture_plugins < 1
        or args.fixture_results < 1
    ):
        parser.error("repeats, interval, payload size and timeout must be positive")
    if not Path("/proc/self/smaps_rollup").exists():
        parser.error("This PSS benchmark requires Linux /proc (including compatible Termux hosts)")
    attachment = container_metadata(args.engine, args.project) if args.engine else None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    source_root = Path(__file__).resolve().parents[1]
    fingerprints = {
        str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source_root.rglob("*.py"))
    }
    cpu_model = platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.partition(":")[2].strip()
                break
    except OSError:
        pass
    report = {
        "schema_version": 1,
        "mode": "local_fixture_search_and_loopback_bittorrent",
        "metric": "PSS bytes; same metric for every component",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "cpu_count": os.cpu_count(),
            "cpu_model": cpu_model,
            "source_sha256": fingerprints,
            "cgroup_version": 2 if Path("/sys/fs/cgroup/cgroup.controllers").exists() else 1,
            "revision": revision,
            "dirty": dirty,
        },
        "interval_seconds": args.interval,
        "payload_bytes": args.payload_mib * 1024**2,
        "fixture_plugins": args.fixture_plugins,
        "fixture_results_per_plugin": args.fixture_results,
        "search_workers": args.search_workers,
        "attachment": attachment,
        "include_engine": args.include_engine,
        "limitations": [
            "Fixture search is offline; not a live indexer latency measurement",
            "Download uses generated content and loopback peers; not internet swarm performance",
            "PSS excludes filesystem cache and kernel/container networking allocations",
            "Engine processes are whole-host shared costs when included",
            "Attached stack is sampled read-only; its lifecycle is not exercised",
        ],
    }
    report["trials"] = [
        run_trial(
            args.interval,
            args.payload_mib * 1024**2,
            args.timeout,
            attachment,
            args.include_engine,
            args.fixture_plugins,
            args.fixture_results,
            args.search_workers,
        )
        for _ in range(args.repeats)
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "trials": len(report["trials"]),
                "summaries": [trial["summary"] for trial in report["trials"]],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        workload(int(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]))
    else:
        raise SystemExit(main())
