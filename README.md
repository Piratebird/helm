# The Helm - Torrent Automation Prototype

<div>
<p align="center">
  <img src="https://raw.githubusercontent.com/Piratebird/helm/main/images/the_helm.jpeg" width="700">
</p>
<p align="center">
  <a href="https://pypi.org/project/helm-torrent/"><img src="https://img.shields.io/pypi/v/helm-torrent.svg?style=flat-square&color=blue" alt="PyPI"></a>
  <a href="https://pypi.org/project/helm-torrent/"><img src="https://img.shields.io/pypi/pyversions/helm-torrent.svg?style=flat-square" alt="Python Versions"></a>
  <a href="https://hub.docker.com/r/piratebird/helm"><img src="https://img.shields.io/docker/pulls/piratebird/helm.svg?style=flat-square" alt="Docker Pulls"></a>
  <a href="https://github.com/Piratebird/helm/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Piratebird/helm/ci.yml?branch=main&style=flat-square" alt="CI Status"></a>
  <a href="https://github.com/Piratebird/helm/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Piratebird/helm.svg?style=flat-square" alt="License"></a>
  <a href="https://github.com/Piratebird/helm/stargazers"><img src="https://img.shields.io/github/stars/Piratebird/helm.svg?style=flat-square&color=yellow" alt="Stars"></a>
</p>
</div>
<hr>
<br>

## Table of Contents

- [The Helm - Torrent Automation Prototype](#the-helm---torrent-automation-prototype)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Installation](#installation)
    - [Python Package](#python-package)
    - [Termux / Android](#termux--android)
    - [Container Setup (Full Mode)](#container-setup-full-mode)
  - [Why this exists](#why-this-exists)
  - [Roadmap](#roadmap)
  - [Changelog](#changelog)
  - [Configuration](#configuration)
  - [Usage](#usage)
  - [Uninstall](#uninstall)
  - [Performance \& Resource Optimization](#performance--resource-optimization)
  - [Disclaimer](#disclaimer)
  - [Contributing](#contributing)
  - [How it works (high-level)](#how-it-works-high-level)
  - [Credits](#credits)
  - [License](#license)

Helm is a CLI-based torrent automation tool designed to fetch, filter, and send magnet links to qBittorrent.

It completely automates its own setup, orchestrating Jackett, Prowlarr, Flaresolverr, and qBittorrent using Docker or Podman under the hood.

---

## Features

- **Automated Container Setup:** No more manually configuring Jackett, Prowlarr, qBittorrent, or Flaresolverr. `setup.sh` orchestrates everything via Docker/Podman compose.
- **Ephemeral (One-Shot) Mode:** Containers spin up when you search/download, and tear down immediately after to save RAM and CPU.
- **Permanent Mode:** Keep the stack running 24/7 if you prefer.
- **VPN Support:** Automatically route qBittorrent traffic through Gluetun (Wireguard/OpenVPN).
- **Live Search & Filter:** A fast, interactive CLI interface to search indexers and select torrents. The filter bar takes words to match plus `cat:<type>`, `sort:size`, `sort:seeds` (prefix `-` for ascending) and `show:all`.
- **Lite Mode (Zero Setup):** Search public APIs and bundled Python scrapers without Docker or Jackett. Lite sources run in isolated processes with a shared 30-second budget, including queueing, startup and network work. Six run simultaneously by default; use `--lite-workers 2` or `HELM_LITE_WORKERS=2` to reduce process concurrency (valid range: 1–12). Stalled workers are terminated and reaped, with POSIX process-group cleanup; completed source results are preserved. Each worker thread reuses its own HTTP session.
- **CLI Indexer Management:** Manage both Jackett and Prowlarr indexers directly from the command line.

---

## Installation

Lite Mode needs Python 3.8+ and internet access. Full Mode additionally needs **Docker** (with Docker Compose) or **Podman** (with podman-compose).

### Python Package
Install Helm for native searches and magnet retrieval:
```bash
pip install helm-torrent
```
Once installed, launch the CLI from anywhere:
```bash
helm-torrent
```

The Helm steers torrent downloads and is unrelated to Kubernetes Helm. The executable is `helm-torrent`; Helm does not install a `helm` alias. Existing Python imports, data directories, and container names remain compatible. When upgrading from an older release, reinstall the Python package to replace its entry points. If the old installation previously overwrote Kubernetes Helm, restore Kubernetes Helm using its own installer afterward.

When installing this checkout, use `python -m pip install -e .` to run the uncommitted changes. Public PyPI packages and raw GitHub files only include these changes after a release or push.

The built-in downloader is optional and requires compatible libtorrent bindings:
```bash
pip install 'helm-torrent[download]'
```
Without libtorrent, selecting a Lite result prints its magnet link for use in another torrent client. Searching and JSON output work without it.

### Termux / Android

Run Lite Mode directly from Termux; it does not require the container stack:
```bash
pkg install python
python -m pip install --upgrade helm-torrent
helm-torrent search "Ubuntu" --type software --lite --json
```
For lower-memory searches, add `--lite-workers 2`; fewer workers can increase latency or exhaust the shared queue budget.

Native libtorrent bindings may require platform-specific installation. You can use the magnet links with an Android torrent client instead.

If a source times out (for example, `nyaa.si`), Helm keeps results from other completed sources. A timeout indicates a connection problem with that source; it does not establish that no matching torrents exist. DNS, network filtering, and source outages can affect availability.

- Enter a nonempty, specific query. A request containing `q=` has an empty query; current Helm rejects blank interactive and CLI searches.
- Use `helm-torrent logs` for recent source errors, or `HELM_VERBOSE=true helm-torrent search "artist album" --type music --lite --json` for verbose diagnostics on stderr.
- Sources match words literally, and release names often split names ("Spider-Man"). When a search finds little, Helm also tries the split spelling and prints `Also searched for ...`; set `search.spelling_variants = false` to turn this off.
- If raw results exist but the final list is empty, check content filters and `search.min_seeds` in `config.toml` (default: 1). Use `helm-torrent paths` to locate it. Type `show:all` in the result filter to list hidden results with the reason each was hidden (`seeds`, `category` or `keyword:<word>`).
- Content types are mapped to plugin category keys when supported (video searches movies/TV/anime); general sources fall back to `all`. Inline feeds and unsupported categories still rely on local scoring/filtering.
- A source summary on stderr distinguishes empty results from timeouts, HTTP failures, connection failures, and parse failures. Bundled plugins report swallowed failures; third-party plugins may need the `novaprinter.report_error` hook. Jobs whose queue budget expires are marked `budget_exhausted`; JSON stdout stays separate.

### Container Setup (Full Mode)

**Instant Terminal Installation:**
For a fully automated setup that skips cloning the repository manually, run:
```bash
bash <(curl -sL https://raw.githubusercontent.com/Piratebird/helm/main/setup.sh)

# Or for a completely silent install using defaults (Docker, Ephemeral mode):
bash <(curl -sL https://raw.githubusercontent.com/Piratebird/helm/main/setup.sh) --skip-prompts
```

**Manual Source Installation:**
If you prefer to inspect the source and install manually:
```bash
# Clone the repo
git clone https://github.com/Piratebird/helm.git
cd helm

# Run the automated setup script
./setup.sh
```

The script will guide you through:
1. Choosing your container engine (Docker vs Podman)
2. Choosing your run mode (Ephemeral vs Permanent)
3. Setting up a VPN (optional)

`setup.sh` only handles the host: the container engine, native installs and the optional systemd unit. It then pulls `ghcr.io/piratebird/helm` and runs `helm-torrent init` inside it, with the engine socket and your directories mounted at the same paths, so the files it writes are owned by you. `init` writes `docker-compose.yml` in the current directory from `src/helm/stack/compose.template.yml`, writes `state/.env.docker` with mode `0600` (including `INDEXER_MANAGER` for the containers), reads the Jackett and Prowlarr API keys, and creates the `helm-torrent.sh` launcher in one-shot mode. The CLI container gets your config directory, so `config.toml` and `config.jsonc` work there too. Set `COMPOSE_PROJECT_NAME` to change the container name prefix (default `helm`), `HELM_IMAGE` to use another image, or `HELM_SETUP_RUNNER=helm-torrent` to run `init` on the host instead of in the image.

`init` sets up 14 public Jackett indexers: 1337x, YTS, Nyaa, The Pirate Bay, EZTV, LimeTorrents, TheRARBG, TorrentDownloads, TorrentDownload, TorrentProject, Knaben, uindex, Kickass and ExtraTorrent (the list is in `src/helm/search/public_indexers.py`). Five of them sit behind Cloudflare and work through the stack's FlareSolverr. Prowlarr enables the same ones on its first search when it has none. An indexer you already set up in Jackett is left as it is, so rerunning `init` adds the missing ones only.

<br>

## Why this exists

Honestly for the most part it's for myself and my own usage i wanted to get magnets of torrents and shows and it was annoying sometimes to look all over the internet for a torrent so i wanted to do that but with the terminal for the most part and heck yeah it gets the job done so far it's not perfect but it's my own so hell yeah :)

## Roadmap

Helm is actively roaming the 7seas and trying to get more treasures:

- ~~Prowlarr integration (Replacing Jackett).~~ Done in 0.10.0 — Jackett, Prowlarr, or both.
- Scrumptious TUI interface.
- Better indexer management.

For more detailed tasks breakdown check [TODO.md](docs/TODO.md)

## Changelog

All notable changes are tracked in [CHANGELOG](CHANGELOG.md).

**Latest release: [0.11.0](CHANGELOG.md#0110---2026-11-03)**: headless services (`download`, `watch`, `serve`, `attach`, `--daemon`), layered TOML/JSONC settings with `config init|edit|doctor|show`, themes and the banner, setup that runs `helm-torrent init` inside the image, and wider search: duplicates merged by info hash, source-category filtering with `show:all`, 14 seeded Jackett indexers, qBittorrent plugin API compatibility and spelling variants such as `spider man` for `spiderman`. See the full file for everything that came before.

## Configuration

Helm uses a combo of env variables and config files cleanly sandboxed away from your host OS.

With the adoption of XDG standards, your configuration and state are stored natively based on your OS:

- **Linux:** `~/.config/helm/` (Config) and `~/.local/state/helm/` (State)
- **Mac:** `~/Library/Application Support/helm/` (Config and State)
- **Windows:** `%APPDATA%\helm\` (Config) and `%LOCALAPPDATA%\helm\` (State)

What these do:

- `config.toml` or `config.jsonc`: Your settings, never secrets. Helm does not rewrite this file. If both exist, `config.toml` is used and the two are not merged.
- `state/settings.json`: Choices Helm saves at runtime, such as wizard answers. Your config file overrides these.
- `secrets.env`: API keys and passwords (e.g. `JACKETT_API_KEY`, `JACKETT_PASSWORD`, `QB_PASSWORD`). Written with mode `0600` and resolved with env vars taking priority.
- `state/.env.docker`: Environment variables for the containers
- `state/jackett/`: Jackett configuration and indexers
- `state/qbittorrent/`: qBittorrent configuration and state
- `Downloads/helm/`: Your downloaded files

```toml
# ~/.config/helm/config.toml
[search]
min_seeds = 1
lite_workers = 6
default_type = "video"
indexer_manager = "jackett"   # jackett, prowlarr or both
lite_only = false

[services]
jackett = "http://localhost:19117"
prowlarr = "http://localhost:19696"
qbittorrent = "http://localhost:18080"
qbittorrent_user = "admin"
qbittorrent_downloads_root = "/downloads"
```

`config.jsonc` uses the same keys and allows comments and trailing commas. Command-line flags and environment variables such as `QB_WEBUI` override the file. Values you leave out use the defaults above.

The first run creates `config.toml` from any non-default values in an older `config.json`, moves legacy secrets into `secrets.env`, and leaves `config.json` in place with a `.pre-toml.bak` copy. After that, `config.json` is ignored.

Manage the file with the `config` commands. They also work when the file is broken, so you can fix it:

```bash
helm-torrent config init                 # Write a commented config.toml once (--format jsonc for JSONC)
helm-torrent config edit                 # Open it in $VISUAL or $EDITOR, then validate it
helm-torrent config doctor               # Check values, files, permissions, directories and services
helm-torrent config show --origin        # Print every value and whether it came from env, file, state or default
```

`config init` never overwrites an existing file. `config doctor` exits with status 1 on errors. Unreachable services only produce warnings, and the service checks are skipped when `search.lite_only` is set. Neither command prints secrets, and passwords in URLs are shown as `***`.

### Theme and banner

```toml
[theme]
preset = "helm"            # helm, tokyo-night, nord or catppuccin-mocha
# logo = "#4A69BD"         # Override one preset colour: logo, accent, text, line, error, muted

[logo]
source = "builtin:helm"    # builtin:helm, file, cmd:<command> or none
position = "left"          # left or top
# art_file = "~/.config/helm/logo.txt"

[banner]
enabled = true
modules = ["version", "mode", "indexers", "jobs", "downloads"]
timeout_ms = 400
```

`helm-torrent banner` prints the banner, and `--json` prints its data. Modules are `version`, `mode`, `indexers`, `jobs`, `downloads`, `services` and `separator`, plus your own commands, such as `{ type = "command", label = "Uptime", command = ["uptime", "-p"] }`. Commands run without a shell, and only the first line of their output is shown. All modules share the `timeout_ms` limit: a slow one shows `timed out`, a failing one shows `unavailable`, and a logo that fails falls back to the built-in one. For `source = "file"`, art can use `{logo}`, `{accent}`, `{text}`, `{line}`, `{muted}` and `{reset}` colour placeholders. The theme colours the whole interface: the interactive UI, the setup wizard, one-shot mode and the Lite downloader. The banner shows when the UI starts, and with `enabled = false` only the plain wordmark does. `HELM_THEME` and `HELM_BANNER` override the file. Colours are off when output is not a terminal or `NO_COLOR` is set, and `FORCE_COLOR` turns them back on. `--json` output never contains colours or the banner.


## Usage

Run `helm-torrent` for the interactive CLI. Use subcommands for scripted searches and administration:
```bash
# Search without containers and emit JSON (no download)
helm-torrent search "Ubuntu" --type software --lite --json

# Configure the full container environment (asks for anything not given)
helm-torrent init --engine docker --indexers both --run-mode oneshot

# Start the configured stack, select a download, then stop it
helm-torrent search "Ubuntu" --type software --oneshot

# Automatically select and download the top filtered result
helm-torrent search "Ubuntu" --type software --auto

# Manage indexers or inspect the installation
helm-torrent indexers --prowlarr
helm-torrent status
helm-torrent paths
helm-torrent logs
```

Built-in downloads respect `--dl-dir` / `HELM_DL_DIR`. Invalid content types and unknown flags are rejected. Lite Mode never starts containers, including when `--oneshot` is passed.

JSON search exits with status 0 when results exist, or 1 for an empty list. Normal search prompts you to choose a result; `--auto` sends the top result to the downloader. The source installer also generates a machine-specific `helm-torrent.sh` launcher for the container workflow; it is not included in the source checkout.

### Headless and background use

```bash
# Download a magnet, detail page, .torrent URL or local .torrent file
helm-torrent download 'magnet:?xt=urn:btih:...'

# Queue stable .torrent, .magnet and .txt files dropped into a directory
helm-torrent --daemon watch ~/torrents/inbox

# Accept jobs and stream finished files over an authenticated local API
helm-torrent --daemon serve --port 8765

# List or stop background commands
helm-torrent daemon status
helm-torrent daemon stop <id>
helm-torrent daemon prune     # forget daemons that have stopped

# Start a tmux UI that survives SSH disconnects, then reconnect to it
helm-torrent --session main
helm-torrent attach main
```

`serve` and `files` require `Authorization: Bearer <HELM_SERVER_TOKEN>` on every route. A token is generated in `secrets.env` when none is set. `files` is the read-only variant. Both bind to `127.0.0.1` by default and speak plain HTTP, so use a VPN or TLS proxy before exposing them to a network.

If qBittorrent sees downloads at a path other than `/downloads` inside its container, set `QB_DOWNLOADS_ROOT` so finished files can be mapped back to the host. tmux is required for `--session` and `attach`.

## Uninstall

Download the standalone Bash script from GitHub, or run `./uninstall.sh` from a checkout. This URL becomes available after the script is pushed:
```bash
# Show options; this does not remove anything
bash <(curl -fsSL https://raw.githubusercontent.com/Piratebird/helm/main/uninstall.sh) --help

# Preview removing application config and service data
bash <(curl -fsSL https://raw.githubusercontent.com/Piratebird/helm/main/uninstall.sh) --config --state --dry-run

# Apply only the selected removal options
./uninstall.sh --config --state --yes

# Preview removal of this project's containers and networks
./uninstall.sh --containers --engine docker --project helm --dry-run

# Preview uninstalling the Python package from a particular environment
HELM_PYTHON=/path/to/venv/bin/python ./uninstall.sh --package --dry-run
```

Every operation previews by default; `--yes` applies it, and `--dry-run` always prevents changes. `--containers` targets exact Compose project labels. Named volumes are preserved unless you also select `--volumes`; that option deletes all contents of matching named volumes, including any downloads stored inside them.

Host downloads, user plugins, source files, images, and Kubernetes Helm configuration are preserved. Use `--config-dir`, `--state-dir`, and `--downloads-dir` for custom locations. The script does not remove native Jackett/qBittorrent installations or system services; disable an installer-created `helm-app.service` separately before removing its stack. It never invokes the `helm` executable.

## Performance & Resource Optimization

The generated Compose configuration declares per-service CPU and memory limits. Enforcement depends on the container engine and host cgroup configuration; verify the limits on your deployment. One-shot mode stops service containers after use, while the container engine, filesystem cache, and other host processes may remain active.

The [resource benchmark report](docs/BENCHMARK.md) includes a tested harness, raw PSS samples, and three verified local search/download trials at each of two concurrency settings. It separates these measurements from unverified historical container figures. Run `python -m helm.benchmarks.benchmark --help` for reproduction options.

## Disclaimer

Helm does **NOT** host, distribute, or provide any copyrighted content.

This tool simply automates (kinda) the process of fetching RSS feeds and sending magnet links to a torrent client.

However you use this tool is your responsibility gangster.

## Contributing

Contributions are welcome especially but not limited to bug fixes, refactors or documentation improvements. Please see our [CONTRIBUTING](CONTRIBUTING.md) guide for details on setting up the environment, our branching strategy, and code quality expectations.

If you plan to add a major feature or change behavior, it's prolly a good idea to open an issue first.

with that out the way this project is real close to me since it's my official first project so let's make this bozo go (perchance).

## How it works (high-level)

Helm pulls torrent RSS feeds from configured indexers, applies filtering and deduplication rules, and automatically sends matching magnet links to qBittorrent.

## Credits

Shoutout to the goats! This project is built utilizing these fantastic open-source tools:

(i lwk feel like a sales person pitching these but wtv lol)

- **[qBittorrent](https://www.qbittorrent.org)** — the torrent client that receives all our magnets.
- **[Jackett](https://github.com/Jackett/Jackett)** — the battle-tested indexer manager for public trackers.
- **[Prowlarr](https://github.com/Prowlarr/Prowlarr)** — the modern, community-indexer-powered search indexer manager. Helm natively discovers its 600+ indexer schema alongside Jackett.
- **[Flaresolverr](https://github.com/FlareSolverr/FlareSolverr)** — busts Cloudflare walls so the indexers actually talk to us.
- **[Gluetun](https://github.com/qdm12/gluetun)** — keeps qBittorrent safely behind your VPN with zero config headaches.
- **[LinuxServer.io](https://www.linuxserver.io)** — their massively maintained container images are what the Docker/Podman stack runs on (always coming in clutch istg).

**Lite Mode Plugins:**
A massive thank you to the [qBittorrent search engine plugins community](https://github.com/qbittorrent/search-plugins) and developers. Helm's lightweight search mode runs their `.py` plugins, making it possible to search dozens of torrent indexers instantly without any Docker overhead. Drop a plugin into the `plugins` folder of your config directory (`helm-torrent paths` shows it). Helm provides qBittorrent's plugin API: `retrieve_url` and `download_file` with qBittorrent's arguments, `headers`, `htmlentitydecode`, `enable_socks_proxy`, `prettyPrinter` (with `pub_date` and every size form `anySizeToBytes` accepts), `anySizeToBytes` and `SearchResults`. Plugins get the URL-encoded query, as in qBittorrent, and a result whose link is a details page goes through the plugin's own `download_torrent`. Python 2 plugins and plugins that need packages Helm does not install will not load. Bundled and adapted from that community (licensed for adaptation): **BitSearch, EZTV, LimeTorrents, SubsPlease, TorLock, TorrentProject, YTS, 1337x, The Pirate Bay/apibay** — alongside Helm's own **BitTorrented** plugin and the inline **apibay, torrents-csv & Nyaa** feeds.

**Built with these Python libraries:**
**[requests](https://github.com/psf/requests)** (originally Kenneth Reitz), **[beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/)** (Leonard Richardson), **[python-dotenv](https://github.com/theskumar/python-dotenv)** (theskumar), and **[libtorrent](https://github.com/arvidn/libtorrent)** (Arvid Norberg) doing the heavy lifting.

## License

This project is licensed under the GPL License.
See the [LICENSE](LICENSE) file for details.
