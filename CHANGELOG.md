# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
