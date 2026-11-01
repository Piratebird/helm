# Helm Resource Benchmarks

## Reproducible local workload (October 2026)

The tested harness is [benchmark.py](../src/helm/core/benchmark.py). Its [raw samples](benchmarks/local-loopback.json) record three trials at six-worker concurrency, per-sample PSS, phase events, checksums, source SHA-256 fingerprints, the base Git revision and dirty-tree status, Python/kernel/CPU details, and whether engine overhead was included.

Measured environment: **11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz**, 8 logical CPUs, Linux kernel `7.2.11`, Python `3.14.8`, libtorrent `2.0.11.0`. These measurements were taken from an uncommitted working tree; source hashes identify the measured implementation.

These local figures predate later queue and client changes, so current source hashes do not match every file recorded in the report.

The workload consists of a warm idle process, twelve offline search plugins emitting 100 generated results each (1,200 total) through the actual isolated worker pipeline, a real loopback BitTorrent transfer of a generated **4 MiB** payload, session teardown, and process exit. Public trackers, DHT, local discovery, UPnP, and NAT-PMP are disabled for the download. Every transfer is verified by SHA-256.

| Phase | Complete samples | Median workload PSS (MiB) | Peak workload PSS (MiB) |
| :--- | ---: | ---: | ---: |
| idle | 17 | 20.51 | 20.91 |
| search | 26 | 31.12 | 126.55 |
| download | 32 | 30.85 | 39.83 |
| teardown | 18 | 34.54 | 35.04 |
| exited | 3 | 0.00 | 0.00 |

Median twelve-job fixture search latency: **0.498 s**, range **0.487–0.524 s**. Median loopback download phase: **0.738 s**, range **0.699–0.740 s**. Download timing includes fixture generation, torrent/session setup, transfer, flushing, and checksum verification; it is not a pure network-throughput measurement.

Teardown retains the Python process and loaded native libraries. Only the `exited` sample reflects the reaped workload; a zero there says nothing about whole-host RAM usage. Races with exiting processes are marked incomplete and excluded from median/peak calculations rather than counted as zero.

## Concurrency comparison

The [two-worker samples](benchmarks/local-loopback-workers2.json) use the same twelve plugins and payload, measured sequentially after the six-worker run. Each configuration has three trials.

| Concurrent search workers | Median search latency (s) | Peak search workload PSS (MiB) |
| ---: | ---: | ---: |
| 6 | 0.498 | 126.55 |
| 2 | 0.862 | 58.73 |

Two workers reduced the observed peak memory at the cost of a longer fixture search. This host-only comparison does not establish an optimal Android setting. The CLI exposes `--lite-workers 2` (or `HELM_LITE_WORKERS=2`), with six as the default and a valid range of 1–12. The shared search deadline includes queueing, so fewer workers can leave more jobs unstarted when early sources stall; these are reported as `budget_exhausted`.

## Production stack (November 2026)

[stack_benchmark.py](../src/helm/core/stack_benchmark.py) starts Jackett, Prowlarr, qBittorrent and FlareSolverr in a disposable project, using the same image IDs on both engines. A generated indexer page is searched through Prowlarr and Jackett, then qBittorrent downloads a generated 16 MiB payload from a local seeder and its SHA-256 is verified. The raw samples are in [stack-docker.json](benchmarks/stack-docker.json) and [stack-podman.json](benchmarks/stack-podman.json).

Each engine completed three trials. Effective cgroup limits matched the request on every service: 256 MiB and 0.5 CPU, or 512 MiB and 0.5 CPU for FlareSolverr. All containers stopped, and cleanup reported no errors.

| Engine | Five searches, by trial (s) | Download, by trial (s) | Median idle `memory.current` (MiB) | Peak during download (MiB) |
| :--- | ---: | ---: | ---: | ---: |
| Docker | 24.40, 15.83, 11.21 | 2.07, 1.05, 1.24 | 302.6 | 332.8 |
| Podman | 8.52, 8.02, 8.02 | 1.49, 1.48, 1.48 | 316.5 | 345.0 |

Memory figures sum the four service cgroups at each sample. They include page cache charged to those cgroups and exclude the engine daemons, so they cannot be added to the PSS figures above.

Do not read these runs as a Docker-versus-Podman ranking. The engines ran one after the other on a host with unrelated containers active, Docker's first trial was the slowest for reasons not isolated here, and three trials cannot separate those effects. Search time also reflects 0.5 CPU limits and service overhead, not live indexer latency.

The default image IDs exist only on the host that recorded them. Elsewhere, pass `--image-manifest images.json` or `--image service=reference`:

```bash
PYTHONPATH=src python -m helm.core.stack_benchmark --engine docker --output docs/benchmarks/stack-docker.json --repeats 3
```

## Run the harness

```bash
python -m pip install -e '.[dev,download]'
PYTHONPATH=src python -m helm.core.benchmark --output docs/benchmarks/local-loopback.json --repeats 3 --search-workers 6
PYTHONPATH=src python -m helm.core.benchmark --output docs/benchmarks/local-loopback-workers2.json --repeats 3 --search-workers 2

# Optional: sample an ALREADY RUNNING stack and shared engine processes.
# This does not start, stop, configure, or download through the attached stack.
PYTHONPATH=src python -m helm.core.benchmark --output /tmp/helm-attached.json --engine docker --project helm --include-engine

python -m pytest tests/test_benchmark.py tests/test_search_runner.py tests/test_scraper_fixtures.py -q
```

The PSS sampler requires Linux `/proc/*/smaps_rollup` access. Compatible Termux devices can run it if Android permits reading those files. Install libtorrent bindings supported by the platform before running the download phase. The harness follows the [official libtorrent Python bindings](https://www.libtorrent.org/python_binding.html).

Options control repetitions, sample interval, payload size, download timeout, fixture plugin/result counts, and search concurrency. No source searches or external downloads occur in the local benchmark. Output files contain measurements and allowlisted metadata; container environment variables and secrets are excluded.

## Accounting and practical limits

- Every component uses **PSS in bytes**. Workload, attached service trees, and shared engine/helper processes are disjoint PID sets; overlapping descendants are counted once.
- The benchmark observer is excluded. The local workload includes its search subprocesses; seeder and downloader sessions both live inside the measured workload. This is a reproducible two-peer exercise, not a measurement of one production downloader alone.
- Engine and attached-stack figures are separate components. The saved local run has neither enabled; their zeros mean excluded, rather than evidence of zero engine overhead.
- Optional whole-host engine costs include `dockerd`, `containerd`, `conmon`, `pasta`, `slirp4netns`, and `rootlesskit` process trees. These costs may be shared with unrelated workloads. Unreadable PSS is explicitly marked incomplete.
- PSS excludes filesystem cache and kernel/network allocations. It cannot be added to RSS or cgroup memory usage as if they were the same metric.
- Container attachment records image IDs, configured CPU/memory ceilings, engine version, and project selection. Declared limits are not proof of enforcement; verify effective cgroups separately on the target host.
- The saved download is loopback-only, and the fixture search is offline. These numbers do not predict live indexer latency, internet swarm speed, Termux performance, or a full container stack's memory footprint.

No Helm project containers were running during this review. A real Docker/Podman stack idle/search/download/teardown comparison remains a separate measurement task; this harness supports read-only attachment and does not fabricate those figures.

## Improvements suggested by the evidence

1. Measure complete production stack lifecycles in an isolated Compose project, with effective cgroup limits and the same legal payload. Keep live-source availability separate from engine comparisons.
2. Track cold subprocess startup and peak memory as worker concurrency changes. Process isolation fixes unbounded exit but adds startup and memory costs; offline fixtures do not establish the best concurrency for live sources that may block for their entire timeout.
3. Add per-phase CPU and energy measurements for Termux, where six simultaneous Python workers may be expensive.
4. Exercise interrupted download recovery and native-library teardown on a real Android device. Linux fixtures cannot establish device compatibility.

---

## Historical reports (unverified)

These are historical figures reported for August and September 2026. Those original reports were not accompanied by raw samples, exact image versions, or a record of their workload and measurement commands. The figures below have not been independently reproduced in this review and should be treated as observations to validate, rather than performance guarantees.

The August comparison covers **Jackett + qBittorrent**. The September comparison includes more services and uses a different accounting boundary; the two tables are not directly comparable.

### Test Environment
* **OS:** Fedora Linux 44 (Workstation Edition) x86_64
* **Kernel:** Linux 7.1.8-200.fc44.x86_64
* **CPU:** 11th Gen Intel Core i5-1135G7 (8 threads @ 4.20 GHz)
* **RAM:** 16 GB (15.41 GiB usable)

> [!NOTE]
> The original report described limits of `0.50` CPU cores and `256M` RAM. Current generated configurations vary by service: Flaresolverr declares `512M`, and Gluetun declares `128M` / `0.25` CPU cores. Limits are ceilings, not measured usage, and enforcement must be checked on the actual engine and host.

### Memory Consumption Comparison Chart

| Deployment Architecture | qBittorrent RAM | Jackett RAM | Engine Daemon Overhead | Total Stack Memory Usage | Hard Limits Configured |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Docker Compose** | `~19.8 MB` | `~91.5 MB` | `~100.0 MB` | **`~211.3 MB`** | Yes (cgroups) |
| **Podman Compose** | `~38.0 MB` | `~140.0 MB` | `0.0 MB` *(daemonless)* | **`~178.0 MB`** | Yes (cgroups) |
| **Native Host** | `~36.5 MB` | `~231.9 MB` | `0.0 MB` | **`~268.4 MB`** | No |

---

### Interpretation and limitations

The reported Docker and Podman totals are lower than the reported native total for this particular snapshot. They do not demonstrate why Jackett used less memory: runtime versions, garbage collector settings, workload, warm-up, and measurement method were not recorded. There are no search-latency measurements to support a claim that memory limits have no performance cost.

Podman does not require a persistent central daemon, but container monitors, networking helpers, and other host processes still consume resources. The table's `0.0 MB` daemon column must not be interpreted as zero host overhead. Docker daemon overhead can also be shared with other workloads.

---

### Update (September 2026): Full Ephemeral Stack with Prowlarr & Flaresolverr

The original report added the following figures after integrating **Prowlarr** and **Flaresolverr**. The container totals below sum only the listed services and CLI; unlike the August Docker total, they exclude engine overhead. Lite Mode is a search-only baseline with a different feature set, and does not include an active libtorrent download.

| Deployment Architecture | qBittorrent | Jackett   | Prowlarr  | Flaresolverr | CLI Runner (Python) | Sum of Listed Components |
| :---                    | :---        | :---      | :---      | :---         | :---                | :---                |
| **Docker**              | `~17.2 MB`  | `~53.1 MB`| `~67.9 MB`| `~40.2 MB`   | `~31.0 MB`          | **`~209.4 MB`**     |
| **Podman**              | `~15.0 MB`  | `~47.0 MB`| `~61.0 MB`| `~36.0 MB`   | `~31.0 MB`          | **`~190.0 MB`**     |
| **Native (Lite Mode)**  | N/A         | N/A       | N/A       | N/A          | `~31.0 MB`          | **`~31.0 MB`**      |

### One-shot lifecycle

One-shot mode stops the service containers after the workflow completes. This releases their process memory, but does not imply that total host overhead returns to zero: the engine, cached pages, CLI, and other helpers may remain. Error and interruption paths also need to be tested when evaluating cleanup.
