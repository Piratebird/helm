# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
