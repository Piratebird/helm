# Changelog
All notable changes to this project will be documented in this file.
## [0.11.0] - Unreleased

Headless services, layered TOML/JSONC settings with `config` commands, themes and the banner, a rebuilt setup that runs `helm-torrent init` inside the image, and a package restructure. Set the date when this is tagged.

### Changed
- `search.min_seeds` defaults to 1 instead of 3, so results with one or two seeders are shown. A `min_seeds = 3` in an old `config.json` is treated as the old default when migrating.
- Rename the Lite downloader's `download_magnet` to `download_torrent`, since it also takes `.torrent` URLs and files. `download_magnet` stays as an alias.
- Split setup: `setup.sh` now only checks the host (engine, native installs, systemd) and runs `helm-torrent init` inside the Helm image as the current user. `init` writes the compose file, `.env.docker`, API keys and the launcher, and takes `--engine`, `--indexers`, `--run-mode`, `--workdir`, `--socket`, `--project`, `--mount-source` and `--skip-prompts`; it asks for whatever is missing and runs even when the config file is broken. It no longer activates Jackett and Prowlarr indexers through their APIs: Jackett gets the seeded indexer files, and Prowlarr seeds its defaults on first search. If the image cannot be pulled, setup uses a local copy.
- Rewrite `setup.sh` as small functions with a short `main`, tested against fake Docker, Podman and systemctl binaries. `setup.sh` and `helm-torrent init` now render one compose template, `src/helm/core/compose.template.yml`, and `docker-compose.yml.example` is removed. Setup no longer edits `config.json`; the indexer choice goes into `.env.docker` as `INDEXER_MANAGER`. The container name prefix is `helm` (or `COMPOSE_PROJECT_NAME`) everywhere, instead of the directory name in `.env.docker`. An existing `qBittorrent.conf` is kept instead of overwritten.
- Use the theme everywhere the UI, wizard, one-shot mode and Lite downloader print colour, and show the banner when the UI starts. Colours are now off when output is piped or `NO_COLOR` is set. Red messages that used the terminal's basic red now use the theme's error colour.
- Use one set of service defaults: Jackett `19117`, Prowlarr `19696`, qBittorrent `18080`. Set `[services]` for a native Jackett on `9117`.
- Report invalid config files clearly instead of resetting them. Runtime choices are stored in `settings.json` in the state directory, below the user's file.
- Start faster by deferring heavy imports, and split the CLI entry point into separate handlers.
- Use SQLite WAL, indexed lookups, one-second cancellation polling and `sendfile` in the queue and file server.
- Install the `helm-torrent` command instead of `helm` to avoid Kubernetes Helm's executable. Python imports, configuration paths, and container names remain compatible. Source setup generates `helm-torrent.sh`.
- Make libtorrent an optional `download` extra; Lite searches and magnet retrieval work without native bindings.
- Respect the configured download directory and reject unknown flags, blank queries, and invalid content types.

### Added
- Spelling variants: when a search finds fewer than 30 results, Helm tries each long word split in two ("spiderman" as "spider man"), keeps a split only if probe results from Apibay and Torrents-csv contain the original word once punctuation is removed and outnumber the typed query at least twice, and then searches it too and merges the results. "spiderman brand new day" went from 6 results to 581. Turn it off with `search.spelling_variants = false` or `HELM_SPELLING_VARIANTS=false`.
- qBittorrent plugin compatibility: `helpers` and `novaprinter` now match qBittorrent's nova3 API (`retrieve_url(url, custom_headers, request_data, ssl_context, unescape_html_entities)`, `download_file(url, referer)` returning `"<path> <url>"`, `headers`/`_headers`, `enable_socks_proxy`, `anySizeToBytes`, `SearchResults`). Your own plugins get the URL-encoded query, and when you pick a result whose link is not a magnet, the plugin's `download_torrent` runs in a child process with a 60-second timeout, falling back to Helm's own link resolver. By a static check of the 72 public plugins on qBittorrent's unofficial list, 59 should now load and run, up from about 30; the rest are Python 2 or need extra packages.
- `init` now sets up 14 public Jackett indexers instead of 4, adding EZTV, LimeTorrents, TheRARBG, TorrentDownloads, TorrentDownload, TorrentProject, Knaben, uindex, Kickass and ExtraTorrent. Each was checked against a fresh Jackett with FlareSolverr. Prowlarr's first-search defaults use the same list.
- `show:all` in the result filter lists the results that filters hid, marked `[HIDDEN]` with the reason, and the result header counts them.
- Tests for the setup wizard's full-automation prompts against a local HTTP server that acts like Jackett, Prowlarr and qBittorrent, and in-process tests for `status`, indexer management and the interactive session. CI now installs tmux and tries libtorrent, so those tests run there instead of skipping, and lists any skips.
- `helm-torrent daemon prune` deletes the records and logs of daemons that have stopped.
- Theme presets (`helm`, `tokyo-night`, `nord`, `catppuccin-mocha`) with single-colour overrides, and `helm-torrent banner [--json]`. Banner modules and logo sources (built-in, file, command) run under one time limit, so a slow or failing part cannot break or delay the banner.
- `helm-torrent config init`, `edit`, `doctor` and `show --origin`. They run even when the config file is broken. `doctor` checks syntax, values, unknown keys, ignored files, `secrets.env` permissions, writable directories and service reachability, and never prints secrets.
- Layered settings from `config.toml` or `config.jsonc`, with validation and a shared JSON Schema. Existing `config.json` values migrate once, and the original file stays in place. If both new files exist, `config.toml` is used.
- Durable job queue with process-isolated workers, crash recovery, cancellation and a completed-file catalog.
- `helm-torrent download`, `watch`, `serve`, `files`, `attach` and `daemon`, plus `--daemon` and `--session`.
- Authenticated HTTP job API and range streaming limited to cataloged files inside the download directory.
- Resolution of HTML detail pages, redirects, direct `.torrent` URLs and local metadata, with size, depth, hop and time limits.
- Isolated Docker and Podman stack benchmarks with effective cgroup verification and `--image`/`--image-manifest` overrides.
- A device profiler that reports energy as unavailable unless discharging is verified.
- A tested Linux PSS benchmark harness, source fingerprints, raw samples and verified generated-payload loopback BitTorrent measurements, including twelve-source concurrency comparisons and configurable `--lite-workers`.
- Isolated Lite worker processes with bounded deadlines/imports, category propagation, process cleanup and fixtures for every bundled scraper.
- Per-source Lite outcomes and retained-result counts on stderr, preserving JSON stdout.
- Standalone `uninstall.sh` for raw GitHub execution, with preview-by-default, selective removal and unit tests.

### Fixed
- A banner command that hit its own timeout just before the banner deadline showed "unavailable" instead of "timed out"; it failed the Python 3.8 CI run. CI now runs every Python version to the end instead of cancelling the rest after one fails.
- `REPACK` no longer hides video results, since scene video releases use it too; it only ranks them lower. Game repacks are still caught by FitGirl, DODI, ElAmigos and similar keywords.
- Dedupe also joins a result that has no hash and a rounded size (such as LimeTorrents' "4.2 GB") to a hashed copy with the same title and a size within 2%.
- Plugin results whose size was given in GiB/MiB/KiB/TiB, in lowercase, with thousands separators, or as a number were dropped; they are kept now, and a plugin's `pub_date` shows as the result date.
- A plugin's second `retrieve_url` argument was sent as POST data instead of headers, and `download_file`'s referer was used as a file name.
- Rerunning `init` no longer resets Jackett indexers you have already configured; only missing ones are added. YTS is set up at its current `yts.gg` link instead of the retired `yts.mx`.
- Prowlarr's default indexers no longer include `torrentgalaxy` and `nyaapantsu`, which have no Prowlarr definition, so only 3 of its 5 defaults were ever set up.
- Content filters use the Torznab category a source gives (Jackett, Prowlarr and Torznab feeds): a result filed under the searched type skips the keyword checks, and one filed only under another type is hidden. Results without a category still use keywords.
- Keywords from another content type that are normal in video release names (audio codecs such as `AAC`, `MP3` and `FLAC`, plus `ep` and `single`) no longer hide video results; they only rank them lower. Strong signals such as `PC`, `DODI`, `FitGirl` or `soundtrack` still hide them.
- Book searches ask Torznab for category `7000` (books) as well as `8000` (other), and game searches add `1000` (console).
- Duplicate results from different sources are merged instead of dropped. Results now match on info hash, read from magnets, from `.torrent` URLs named after the hash, and from the `InfoHash` field that Jackett, Prowlarr and Torznab send with their download links, before falling back to the title (ignoring case and punctuation) plus size. The merged result keeps the most-seeded copy, takes a missing date or size from another copy, uses a magnet over an indexer download link, lists every source as `Jackett (TPB) +1`, and lists them in `--json` as `sources`.
- Search results from the Jackett and Prowlarr JSON APIs now keep their publish date (only the Torznab XML fallback did before). Jackett's `0001-01-01` placeholder is treated as no date.
- A missing Prowlarr API key and a JSON search that falls back to Torznab are logged to `helm.log` only, not printed during searches.
- One-shot waits now watch only the torrents this run sent (tagged `helm` plus a per-run `helm-ui-…` tag), so torrents added to qBittorrent by hand no longer hold up the teardown, and Ctrl+C removes only this run's unfinished torrents. The wait goes through the shared qBittorrent session and URL instead of its own `QB_WEBUI` default, and a missing password now stops it instead of retrying forever.
- Progress lines from the Lite downloader and the one-shot wait are plain text, at most one per whole percent or 30 seconds, when output is not a terminal, so job logs no longer fill with escape codes.
- qBittorrent login failures raise `QBittorrentAuthError` (a `ConnectionError`), and one-shot startup, readiness checks and teardown catch only the subprocess and HTTP errors they expect.
- Setup: detect an existing Jackett or Prowlarr container before writing the compose and env files, so the detected one is used instead of a second copy.
- Setup: mount the config directory into the CLI container and set `HELM_CONFIG_DIR`; the old `/app/config.json` mount was never read.
- Setup: use the published `ghcr.io/piratebird/helm` image on Podman instead of a `mini-helm` image nothing built.
- Setup: with a VPN, map gluetun `18080:18080` (qBittorrent listens on 18080) and point `QB_WEBUI` at `gluetun`.
- Setup: fix the systemd unit's empty `ExecStart`, the launcher adding `--oneshot` when any argument contained `-o`, the summary ignoring `--*-dir`, and `sed -i` failing on macOS. `.env.docker` is now written with mode `0600`.
- Log in to qBittorrent again after a session expires, and follow URL or credential changes in long-running services.
- Remove a cancelled job's torrent from qBittorrent, and map container paths to host paths when cataloging its files.
- Allow failed or cancelled sources to be resubmitted, and keep the queue worker alive after unexpected errors.
- Stop orphaned download workers after a forced service stop.
- Reject a whole watch file when any line is invalid, and survive transient watch-directory failures.
- Close connections after rejected requests so an unread body cannot be parsed as a new request; time out stalled clients.
- Keep the working directory off child-process import paths.
- Use DHT bootstrap session settings on bindings without `add_dht_router`, including Termux.
- Preserve completed plugin results when other plugins stall or reuse worker threads.
- Clean up owned one-shot containers across failures and interrupts; Lite searches never start the stack.
- Initialize logging after path overrides, redact credentials in formatted messages and archived logs, and read bounded log tails.
- Report bundled plugin HTTP/parser errors instead of presenting every failure as an empty search.
- Preserve unknown SubsPlease swarm counts through seed filtering.
- Omit configuration from bug reports when redaction fails.
- Correct README commands and qualify historical benchmark claims; add regression coverage.

## [0.10.0] - 2026-09-08


### Added
- **Prowlarr Integration**: Full support for Prowlarr indexing alongside Jackett. Helm now natively discovers over 600+ indexers via Prowlarr's `/api/v1/indexer/schema` API.
- **Privacy Display**: Prowlarr indexers in the TUI now clearly display their status as `public`, `private`, or `semi-private` .
- **Tracker Origin Tagging**: JSON output now prefixes trackers with their source (`Jackett (1337x)` vs `Prowlarr (1337x)`) to determine indexer accountability.
- **Interactive Installer Options**: `setup.sh` now prompts users to choose between Jackett, Prowlarr, or both for their core indexer manager.
- **Native Prowlarr Installation**: `setup.sh` now supports downloading and installing Prowlarr directly to `/opt/Prowlarr` and configuring a `systemd` daemon for bare-metal deployments (includes BETA safety warnings).
- **Container Status Command**: Added a new `helm status` (and global `--status` flag) command that natively queries Docker or Podman to list the uptime of all running Helm containers and prints their active Web UI URLs for easy access.
- **Robust Exception Handling**: Implemented a global `KeyboardInterrupt` trap in `cli.py` to prevent ugly stack traces. The ephemeral stack now safely spins down via `teardown_oneshot()` if the user cancels operations prematurely.
- **Network Resiliency**: Added explicit timeouts to all Jackett and Prowlarr API calls in `indexer_manager.py` and `rss_fetcher.py`. ReadTimeouts will no longer hang the app.

### Changed
- **Port Conflict Avoidance**: Migrated Prowlarr's ephemeral docker port from `9696` to `19696` to strictly match Jackett (`19117`) and qBittorrent (`18080`). This guarantees no "Address already in use" errors when running Helm on servers that already have these native apps running.
- **JSON Stdout Sanitization**: The `-j/--json` flag is now guaranteed to return pure, `jq`-parseable JSON. Ephemeral boot logs have been properly redirected to `stderr`.
- **Linting & Code Cleanup**: Cleaned up the repository of temporary patching scripts.
- **Unified Concurrent Lite Pipeline**: The three inline Lite sources (apibay, torrents-csv, Nyaa RSS) now run inside the same bounded worker pool as the search plugins instead of serially before them. Worst-case search latency dropped from a ~75s serial chain of timeouts to a single ~25s bound.
- **Shared HTTP Session**: Lite fetchers and the JSON-API plugins (BitTorrented, EZTV, SubsPlease, YTS) now reuse one connection-pooled `requests.Session` (keep-alive + pool of 32 sockets) via `helm.core.http`, eliminating a fresh TLS handshake per request on multi-page HTML plugins.
- **Single apibay Fetch**: The bundled `piratebay.py` plugin (a duplicate of the inline apibay source) is no longer loaded by the plugin loader, so The Pirate Bay is queried exactly once per search.

### Fixed
- **Lite-Mode fallback crash on empty manager results**: `search_indexers()` referenced a never-implemented `search_all_plugins` import, so any search where Jackett/Prowlarr returned zero results raised `ImportError` (surfacing as a confusing "Indexer Manager not available" message). The async aggregator is now implemented and the fallback silently returns Lite results.
- **Empty piped stdout on exit**: CLI termination paths used `os._exit`, which bypasses Python's stdout flush and produced empty output when `--json` was piped. All exits now use `raise SystemExit(...) from None`, flushing buffered stdout reliably.
- **Prowlarr default indexer seeding actually implemented**: `ProwlarrManager.seed_default_indexers()` now provisions working defaults (1337x, YTS, TorrentGalaxy, NyaaPantsu, EZTV) on first use against Prowlarr's live schema rather than remaining a no-op.


The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.5] - 2026-08-31
### Added
- **Interactive Multi-Select Checkboxes**: Filter results by multiple categories using a spacebar-toggled checkbox menu before searching.
- **Advanced Inline Filtering & Sorting**: Sort and filter search results directly in the TUI (e.g., `cat:games sort:-size`).
- **Stateful Navigation**: Use `ESC` to go back to previous prompts and menus without exiting the application.
- **Tracker Origins**: Display the indexer/tracker name for each result in the TUI list.
- **Auto-inject Default Trackers**: `helm init` now automatically activates the most reliable public trackers (1337x, YTS, TorrentGalaxy, Nyaa, EZTV) if Jackett is configured.

### Changed
- Replaced the manual CLI search prompt with a state machine to support interactive category selection and stateful navigation.
- Category scoring and matching logic updated to support multi-category profiles simultaneously.
- Switched `sys.exit` calls to `os._exit` for immediate termination and to avoid traceback stacks on exit.

## [0.9.4] - 2026-08-30
### Fixed
- Fixed literal `\n` characters rendering incorrectly as `\\n` during the `helm init` interactive prompts.
- Added graceful `KeyboardInterrupt` handling to `helm init` so pressing `Ctrl+C` exits cleanly without a traceback stack.

## [0.9.3] - 2026-08-30
### Added
- **Zero-Touch Bootstrapping (`helm init`)**: Added an `init` subcommand to the Python CLI that natively bootstraps the Docker/Podman container environment. Users installing via `pip install helm-torrent` can now skip the bash installer completely.
- **Silent Installation Flag**: Added a `--skip-prompts` flag to `setup.sh` for fully automated, non-interactive installations using default settings (Docker, Ephemeral Mode, no VPN).
- **One-Liner Installation**: Added a single `curl` command to `README.md` for instant terminal setup without manual cloning.

### Changed
- `oneshot.py` now resolves `docker-compose.yml` natively from the XDG state directory (`~/.local/state/helm`) rather than relying on the current working directory, preventing crashes when running `helm` globally.

## [0.9.2] - 2026-08-28
### Added
- **BitTorrented indexer** (`src/helm/plugins/bittorrented.py`): JSON search API over a DHT-crawled image library, with real swarm counts. +50 results per search.
- **1337x indexer** (`src/helm/plugins/x1337.py`): HTML search with mirror rotation (`1337x.to`, `1337x.st`, `x1337x.ws`, `1337xx.to`), magnets pulled from the top-seeded detail pages only, and a relevance gate that discards the generic fallback list a mirror serves on a miss. Only reaches Cloudflare-cleared mirrors, so results vary by network.
- **SubsPlease indexer** (`src/helm/plugins/subsplease.py`): JSON API over recent anime with magnets embedded (base32 info hashes supported). Animetitles only; reports unknown seeds since the source carries no swarm data.
- Tests for the EZTV, 1337x, SubsPlease and BitTorrented plugin parsing, relevance gating, and connection-failure fallbacks.

### Changed
- **EZTV indexer now uses the public JSON API** (`eztvx.to/api/get-torrents`) instead of the Cloudflare-blocked HTML search; the API is browse-only (no search param), so titles are filtered client-side from the latest 100 releases.
- Measured raw-result lift over 0.9.1 (`the boys` +153, `dune` +74, `one piece` +84), with 1337x, BitTorrented and SubsPlease all contributing zero or no-op on failure.

### Fixed
- `helm search --json` on a fresh install (no Jackett config) no longer emits the interactive "No configuration found" prompt into the JSON stream; it now falls back to Lite Mode silently and keeps stdout machine-readable.

## [0.9.1] - 2026-08-28
### Added
- **YTS indexer** (`src/helm/plugins/yts.py`): pulls movie releases from the `yts.lt` YIFY API (returns 200 without Cloudflare; `yts.mx` is intermittently down) and emits magnets for every quality/torrent per title. +45 results on a `dune` search.
- Portability: `docker-compose.yml` is now a generated, gitignored artifact; the checked-in `docker-compose.yml.example` template is directly usable by `docker compose` via `${HELM_CONFIG}`/`${HELM_STATE}`/`${HELM_DL}` env vars.
- Container engine override: the one-shot ephemeral stack honors `HELM_DOCKER_CMD` (defaults to `docker`), so Podman or a custom engine can drive `compose/stop/inspect`.
- Tests for the lite fetcher (apibay/torrents-csv/nyaa aggregation), the `--json` CLI search path, RSS feed parsing, derived negative keywords, YTS plugin parsing, and thread-local plugin result isolation; test environment is isolated via `tests/conftest.py`.

### Changed
- Secret store writes are now atomic (`mkstemp` + `fsync` + `os.replace`) and always tightened to mode `0600`, even when a pre-existing store had looser permissions; a crash can no longer truncate `secrets.env`.
- User plugin discovery moved from the legacy `~/.helm_data/plugins` dir to the XDG config dir (`<config>/plugins`), consistent with the 0.8.0 XDG migration.
- `NEGATIVE_KEYWORDS` is now derived from the `CONTENT_PROFILES` keyword lists (each category's negatives = every other category's positives) instead of five hand-maintained copies, so editing one profile propagates everywhere.
- TUI renderers share one code path for truncating titles to display width, paginating scrolled windows, and entering/exiting raw input (both `interactive_indexer_selector` and `interactive_selector`).
- CLI send-magnet logic (qBittorrent with LITE-MODE fallback) deduplicated into a single `_send_magnet` helper; legacy `~/.helm_data/.env` loading removed in favor of the XDG `secrets.env`.
- Refactored the one-shot stack with small helpers (`_docker_run`, `_container_name`) and request timeouts; stale-socket compose check fixed.

### Fixed
- **Plugin result race**: plugin results are now collected into a per-thread list (`novaprinter`) instead of one shared module global, so a plugin that outlives the 30s run timeout can never keep appending its results into the *next* search. The logged `Native plugins returned N` total is now an honest sum of the per-plugin counts.
- `beautifulsoup4` (used by the bundled BitSearch plugin) was missing from the declared dependencies; a fresh install without it silently failed to load that plugin.

### Removed
- Bundled side-effect test fixtures `tests/test_loader.py` and `tests/test_plugin.py`; plugin-loader coverage now lives in the dedicated `tests/test_lite_plugin_loader.py`.
- Legacy `~/.helm_data` paths for the `.env` load and bundled plugin directory.

## [0.9.0] - 2026-08-27
### Added
- **Secret Management**: API keys and passwords now live in a dedicated `secrets.env` store (`~/.config/helm/secrets.env`, mode `0600`). Config values are migrated out of `config.json` automatically; `config.json` now holds settings only.
- **Parallel Jackett Search**: Configured indexers are queried in parallel through their per-indexer Torznab endpoints (with the combined "all indexers" query as a fallback) and deduplicated by title and size.
- Per-indexer result count logging so low-result searches are easy to diagnose from the log file.
- Tests for the secret manager, config migration/redaction, and the min-seed filter.

### Fixed
- Nyaa lite search returning zero results (host 301-redirected to the HTML homepage, breaking RSS parsing); now uses `https://nyaa.si`.
- EZTV indexer returning zero results due to HTTP 403 (Cloudflare); now detected and logged clearly instead of silently skipping.
- Items with unknown seeder counts (`seeders = -1`) were dropped by the `min_seeds` filter; they are now kept.
- `--json` output corrupted by console log lines and the TUI search spinner; logs now go to `stderr` and the spinner is disabled in JSON mode.
- Latent `UnboundLocalError` crash in `--json` mode (`json` was only imported inside the bug-report branch).
- API keys leaked in `--json` link output and bug-report archives; now sanitized.

### Changed
- Secrets are never written to `config.json`; the config wizard and `ensure_config()` resolve them from the environment or `secrets.env`.
- Console log handler writes to `stderr` instead of `stdout` so piped output stays machine-readable.

## [0.8.0] - 2026-08-25
### Added
- **Global Logging Architecture**: Centralized Python `logging` to capture debug and error outputs into a dedicated log file (`logger.py`).
- **XDG Base Directory Support**: Config and state directories now dynamically map to OS-native paths (e.g., `~/.config/helm`, `~/.local/state/helm` on Linux, `AppData` on Windows).
- Ruff and Mypy integration for linting and type checking.
- GitHub Actions CI pipeline matrix for Python 3.8 to 3.12.
- `scripts/archive/` directory for throwaway development scripts.

### Fixed
- JSON corruption issue when `--json` flag is used alongside search command.
- Removed duplicate and unreachable `logs` handler block in `cli.py`.
- Refactored `argparse` fake flags into robust command checks.

### Changed
- Refactored config paths to use OS-native standards instead of a hardcoded `.helm_data` directory.
- Pinned `libtorrent` dependency to `>=2.0.0,<2.1.0`.
