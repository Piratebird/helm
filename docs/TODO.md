# Helm Roadmap & Tasks

## Review priorities (October 2026)

Address reliability and measurement gaps before adding the daemon or AI features.

- [x] Preserve completed source results with one shared deadline; replace nested collection pools with isolated source jobs.
- [x] Snapshot each plugin's result collector and isolate plugin imports/execution in disposable processes.
- [x] Reject empty Lite/CLI queries, preserve other results when Nyaa times out, and explain empty sources versus results removed by filters. Add regression tests.
- [x] Configure DHT bootstrap nodes through session settings for Termux bindings without `add_dht_router`.
- [x] Make libtorrent an optional download extra so search/magnet retrieval can be installed independently of native bindings.
- [x] Omit configuration from bug reports if redaction fails, rather than copying the raw file.
- [x] Correct README commands, explain Termux Lite usage, and qualify unsupported benchmark conclusions.
- [x] **Source diagnostics:** Search-owned outcomes distinguish success, empty, HTTP failure, connection failure, timeout, and parse failure; summaries print after the spinner on stderr.
- [x] **Plugin error visibility:** Bundled plugins record swallowed HTTP/parser failures; helper fetches raise HTTP errors, challenge/changed pages are detected, and retained partial results carry failure outcomes.
- [x] **Cancellation:** Short-lived subprocess jobs share a deadline; imports, network work and queued jobs are bounded. Worker cleanup terminates/reaps POSIX process groups, with a Windows taskkill fallback. Regression tests cover hung imports, process exit, interrupts, queue exhaustion, and descendants.
- [x] **One-shot lifecycle:** Centralized teardown with `try/finally`; regression tests cover startup/search/download/wait failures, early returns, and `--oneshot --lite`.
- [x] **Machine output:** Indexer fallback and source summaries use stderr; tests exercise real `animated_search` fallback paths with `--json`.
- [x] **Logging and reports:** Configure files explicitly after path overrides; redact known/encoded secrets, credential URLs, query keys, nested settings and exception traces; stream redacted archived logs and read bounded file tails.
- [x] **Plugin/category coverage:** Offline fixtures cover every bundled scraper, failure classes and category mapping; selected categories reach isolated workers. Unknown SubsPlease swarm counts use -1 so valid results survive seed filters.
- [x] **Configuration:** Validate CLI content types and unknown arguments; respect the configured downloads directory in the built-in downloader.
- [x] **Benchmarks:** Tested Linux PSS harness and raw samples cover local idle/fixture-search/verified loopback-download/teardown/exit, with source fingerprints, consistent process-tree accounting, and two-/six-worker comparisons. `--lite-workers` exposes the memory/latency tradeoff. See [BENCHMARK.md](BENCHMARK.md).
- [x] **Real stack benchmarks:** Isolated Docker and Podman runs exercise Prowlarr, Jackett, FlareSolverr and qBittorrent with verified cgroup limits, checksummed downloads and lifecycle samples. See [BENCHMARK.md](BENCHMARK.md).
- [x] **Torrent link resolution:** Magnets, HTML detail pages, redirects, direct `.torrent` URLs and local metadata resolve with bounded size, depth, hops and time.
- [ ] **Device measurements:** Verify Android/Termux process cleanup and memory with `--lite-workers`. The profiler and CPU/energy accounting are tested locally, but no complete device run has been recorded.
- [x] **Integration coverage:** Subprocess, real-socket and fake-engine tests cover the headless CLI, HTTP API, tmux sessions, wizard and one-shot ordering, including the wizard's full-automation prompts against local fake Jackett, Prowlarr and qBittorrent servers.

## Released in 0.11.0 (2026-11-03)

- [x] **Settings:** Layered TOML/JSONC settings (environment, file, state, defaults) with `config init`, `edit`, `doctor` and `show --origin`; `config.json` is migrated once and never reset.
- [x] **Look:** Theme presets, logo sources and banner modules with a time budget, applied across the UI, wizard, one-shot mode and Lite downloader.
- [x] **Setup:** `setup.sh` only prepares the host and runs `helm-torrent init` inside the published image, from one compose template.
- [x] **Layout:** `helm.core` split into feature packages and `cli.py` into commands.
- [x] **Reliability:** Tagged one-shot waits, plain progress in job logs, daemon record pruning, narrower error handling.
- [x] **Duplicates:** Results merge by info hash across sources (then by title and size, allowing rounded sizes), keeping the best-seeded copy, a magnet link and every source.
- [x] **Filtering:** Source Torznab categories first; weak cross-category words (audio codecs, `ep`, `REPACK`) only rank lower; `min_seeds` defaults to 1; `show:all` lists hidden results with the reason.
- [x] **Indexers:** 14 public Jackett indexers seeded and shared with Prowlarr, each checked against a live Jackett with FlareSolverr; existing indexer settings are kept.
- [x] **Plugins:** qBittorrent nova3 plugin API (`retrieve_url`, `download_file`, `anySizeToBytes`, `SearchResults`, `pub_date`, encoded queries, `download_torrent`); about 59 of the 72 public community plugins should load.
- [x] **Spelling variants:** Thin searches also try the split spelling release names use, such as `spider man` for `spiderman`.
- [x] **CI:** Every Python version from 3.8 to 3.12 runs to the end; tmux and libtorrent are installed.

## Open

- [ ] **Prowlarr and FlareSolverr:** Check whether the stack adds FlareSolverr as a Prowlarr indexer proxy; without it the five Cloudflare indexers fail in Prowlarr.
- [ ] **`helm-torrent --version`:** The flag does not exist yet.
- [ ] **Structured JSON errors:** Return `{"status": "error", "code": ...}` under `--json` instead of plain messages.
- [ ] **Python orchestration:** Move the remaining host setup from `setup.sh` to the Docker SDK.
- [ ] **Unofficial plugins at runtime:** The plugin compatibility estimate is from reading the code; run a sample of community plugins in a sandbox.

## Completed Tasks
- [x] Create a Bash installer (`setup.sh`)
  - [x] Automate installation of Docker containers for Jackett, Flaresolverr, and qBittorrent
  - [x] Implement Permanent / Ephemeral run modes
- [x] Implement interactive user configuration
  - [x] Config wizard gathers all required info through input prompts
  - [x] Store state gracefully in `config.json`
- [x] Fix exception handling in `core/rss_fetcher.py`
- [x] Implement proper qBittorrent login flow
  - [x] Ensure the user is actually authenticated via requests session
- [x] Integrate VPN Support (Gluetun for secure torrenting)
- [x] Automate Docker image publishing (GitHub Container Registry via GitHub Actions)

## Planned / Upcoming Tasks

### 0. Architecture & Systems
- [x] **Global Logging System:** Implemented a centralized Python `logging` architecture that writes structured logs to `~/.local/state/helm/logs/helm.log` (or Windows equivalent) for easy debugging of background tasks and failed plugin fetches.
- [x] **Advanced Configuration Management:** Expanded `ConfigManager` to support schema validations, OS-native paths (XDG standards), and seamless migrations when updating to newer Helm versions.

### 1. User Interface & Experience
- [ ] **Scrumptious TUI Interface:** Replace raw `termios` handling with a responsive UI.
  - [ ] Rewrite the interface using the `Textual` Python framework for a rich, responsive split-screen UI.
  - [ ] **Background Download Manager:** Upgrade the blocking `lite_downloader.py` (libtorrent) to run as an async background worker.
  - [ ] **Downloads Sidebar:** Users can search Jackett while simultaneously watching live progress bars, ETA, and speeds of active libtorrent downloads on the side.
  - [ ] **Seeding Controls:** Ability to pause/resume/delete active downloads directly from the UI.
  - [x] Tracker Origins (e.g. `[1337x]`) in the data tables.
  - [x] Interactive Multi-Select checkboxes for category selection.
  - [x] Stateful Navigation (`ESC` to Go Back to previous prompts).
  - [x] Advanced inline sorting/filtering (`cat:games sort:-size`).
- [x] **"Zero Setup" Lite Mode:** 
  - [x] Implement a fallback scraper inside Python for 2-3 hardcoded public indexers.
  - [x] Allow users to search and get Magnet links instantly *without* Docker or Jackett installed.

### 2. Tracker Management
- [x] **Prowlarr Integration (Optional Mode):** 
  - [x] Give users a choice during setup between Jackett and Prowlarr.
  - [x] Implement API handlers for Prowlarr so users who prefer modern tracker syncing can use it seamlessly.
- [x] **Improve Jackett Interaction:** 
  - [x] Allow adding/removing indexers directly from the CLI.
  - [x] Seed default indexers during `helm-torrent init` (14 public ones since 0.11.0).

### 3. Distribution & Installation
- [x] **Uninstall Script (`uninstall.sh`):**
  - [x] Standalone raw-GitHub Bash script with preview-by-default removal of selected containers, networks, named volumes, app configuration/state, and the Python package; preserve host downloads and Kubernetes Helm configuration.
- [x] **Command disambiguation:** Install `helm-torrent` without a `helm` alias; update setup guidance and generated launcher name.
- [x] **Publish to PyPI (`pip install helm-torrent`):**
  - [x] Restructure directory to standard Python package layout.
  - [x] Implement `helm-torrent init` to automatically bootstrap the Docker environment on first run.
- [x] **Fully Automated Setup Script:**
  - [x] Add a "skip-prompts" flag to `setup.sh` that auto-generates passwords and pulls Jackett API keys completely silently.
  - [x] Provide a curl one-liner for instant terminal installation.

### 4. Headless & Server Mode
- [x] **Daemon Background Processes:** Add a `--daemon` flag to run operations silently in the background.
- [x] **Watch Directory (`helm-torrent watch <dir>`):** Automatically parse and download any `.torrent` or magnet files dropped into a specified folder.
- [x] **HTTP Server (`helm-torrent serve` / `helm-torrent files`):** Spin up a lightweight web server to accept magnets remotely and stream finished downloads.
- [x] **Session Attach (`helm-torrent attach`):** Allow reattaching to a running TUI session across SSH disconnects.

### 5. AI & Automation
- [ ] **AI Torrent Agent (Natural Language):** Integrate an LLM (Gemini/OpenAI) so users can type natural language commands (e,g. *"Download the LOTR Extended Editions in 4K"*). The AI will autonomously map the intent to search queries, evaluate the seeders/sizes of the results, pick the optimal magnet, and send it to qBittorrent.
