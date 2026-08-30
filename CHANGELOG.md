# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
