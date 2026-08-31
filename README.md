# Helm - Torrent Automation Prototype

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

- [Helm - Torrent Automation Prototype](#helm---torrent-automation-prototype)
  - [Table of Contents](#table-of-contents)
  - [Features](#features)
  - [Installation](#installation)
  - [Why this exists](#why-this-exists)
  - [Roadmap](#roadmap)
  - [Changelog](#changelog)
  - [Configuration](#configuration)
  - [Usage](#usage)
  - [Disclaimer](#disclaimer)
  - [Contributing](#contributing)
  - [How it works (high-level)](#how-it-works-high-level)
  - [Credits](#credits)
  - [License](#license)

Helm is a blazing-fast CLI-based torrent automation tool designed to fetch, filter, and send magnet links to qBittorrent. 

It completely automates its own setup, orchestrating Jackett, Flaresolverr, and qBittorrent using Docker or Podman under the hood.

---

## Features

- **Automated Container Setup:** No more manually configuring Jackett, qBittorrent, or Flaresolverr. `setup.sh` orchestrates everything via Docker/Podman compose.
- **Ephemeral (One-Shot) Mode:** Containers spin up when you search/download, and tear down immediately after to save RAM and CPU.
- **Permanent Mode:** Keep the stack running 24/7 if you prefer.
- **VPN Support:** Automatically route qBittorrent traffic through Gluetun (Wireguard/OpenVPN).
- **Live Search & Filter:** A fast, interactive CLI interface to search indexers and select torrents.
- **Lite Mode (Zero Setup):** Search and get Magnet links instantly using built-in Python scrapers without needing Docker or Jackett installed!
- **CLI Indexer Management:** Add and remove Jackett indexers directly from the command line.

---

## Installation

Helm requires **Docker** (with Docker Compose) or **Podman** (with podman-compose) installed on your system.

### Python Package (New!)
You can now install Helm directly via pip to run it natively or use the new Lite Mode:
```bash
pip install helm-torrent
```
Once installed, you can launch the CLI from anywhere using:
```bash
helm
```

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

<br>

## Why this exists

Honestly for the most part it's for myself and my own usage i wanted to get magnets of torrents and shows and it was annoying sometimes to look all over the internet for a torrent so i wanted to do that but with the terminal for the most part and heck yeah it gets the job done so far it's not perfect but it's my own so hell yeah :)

## Roadmap

Helm is actively roaming the 7seas and trying to get more treasures:

- Prowlarr integration (Replacing Jackett).
- Scrumptious TUI interface.
- Better indexer management.

For more detailed tasks breakdown check [TODO.md](docs/TODO.md)

## Changelog

All notable changes are tracked in [CHANGELOG.md](CHANGELOG.md).

**Latest release: [0.9.5](CHANGELOG.md#095---2026-08-31)** — Major UI improvements including multi-select category checkboxes, inline filtering and sorting, stateful navigation, tracker origins in results, and auto-injection of default Jackett indexers on initialization. See the full file for everything that came before.

## Configuration

Helm uses a combo of env variables and config files cleanly sandboxed away from your host OS.

With the adoption of XDG standards, your configuration and state are stored natively based on your OS:

- **Linux:** `~/.config/helm/` (Config) and `~/.local/state/helm/` (State)
- **Mac:** `~/Library/Application Support/helm/` (Config and State)
- **Windows:** `%APPDATA%\helm\` (Config) and `%LOCALAPPDATA%\helm\` (State)

What these do:

- `config.json`: Settings only (indexers, URLs, flags) — never secrets.
- `secrets.env`: API keys and passwords (e.g. `JACKETT_API_KEY`, `JACKETT_PASSWORD`, `QB_PASSWORD`). Written with mode `0600` and resolved with env vars taking priority.
- `state/.env.docker`: Environment variables for the containers
- `state/jackett/`: Jackett configuration and indexers
- `state/qbittorrent/`: qBittorrent configuration and state
- `Downloads/helm/`: Your downloaded files

The first run migrates any legacy secrets found in older `config.json` files into `secrets.env` and rewrites the config file scrubbed.

## Usage

Helm is primarily run using the included scripts to manage the Docker containers and CLI.

Typical workflow:

1. Run `./setup.sh` to configure indexers, VPN, and credentials.
2. Run `./helm.sh` to launch the interactive CLI and search for torrents.
3. Matching torrents are filtered and sent to qBittorrent automatically.

You can also pass arguments directly to the wrapper script:
```bash
# Force one-shot mode (spins up and tears down Docker)
./helm.sh --oneshot

# Auto-download the top result for a query
./helm.sh --oneshot --auto -q "Ubuntu 24.04" --type software

# Run built-in Lite Mode (without Jackett/Docker)
./helm.sh --lite

# Manage Jackett indexers
./helm.sh --indexers
```

## Performance & Resource Optimization

Helm is aggressively optimized for a minimal memory footprint. While containerized applications are usually heavy, the `setup.sh` installer strictly throttles the Docker/Podman engines using Linux cgroups (e.g., hard-capping Jackett to 256MB).

In our [Resource Benchmarks](docs/BENCHMARK.md), we proved that running Helm's containerized stack actually **saves RAM** compared to installing the software natively!

## Disclaimer

Helm does **NOT** host, distribute, or provide any copyrighted content.

This tool simply automates (kinda) the process of fetching RSS feeds and sending magnet links to a torrent client.

However you use this tool is your responsibility gangster.

## Contributing

Contributions are welcome especially but not limited to bug fixes, refactors or documentation improvements. Please see our [CONTRIBUTING.md](CONTRIBUTING.md) guide for details on setting up the environment, our branching strategy, and code quality expectations.

If you plan to add a major feature or change behavior, it's prolly a good idea to open an issue first.

with that out the way this project is real close to me since it's my official first project so let's make this bozo go (perchance).

## How it works (high-level)

Helm pulls torrent RSS feeds from configured indexers, applies filtering and deduplication rules, and automatically sends matching magnet links to qBittorrent.

## Credits

Shoutout to the goats! This project is built utilizing these fantastic open-source tools: qBittorrent, Jackett, and Flaresolverr.

**Lite Mode Plugins:**
A massive thank you to the [qBittorrent search engine plugins community](https://github.com/qbittorrent/search-plugins) and developers. Helm's lightweight search mode natively supports their `.py` plugins, making it possible to search dozens of torrent indexers instantly without any Docker overhead.

## License

This project is licensed under the GPL License.
See the [LICENSE](LICENSE) file for details.
