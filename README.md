# Helm - Torrent Automation Prototype

<div>
<p align="center">
  <img src="images/the_helm.jpeg" width="700">
</p>
<p align="center">
  <a href="https://pypi.org/project/helm-torrent/"><img src="https://img.shields.io/pypi/v/helm-torrent.svg?style=flat-square&color=blue" alt="PyPI"></a>
  <a href="https://hub.docker.com/r/piratebird/helm"><img src="https://img.shields.io/docker/pulls/piratebird/helm.svg?style=flat-square" alt="Docker Pulls"></a>
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
  - [Configuration](#configuration)
  - [Usage (WIP)](#usage-wip)
  - [Disclaimer](#disclaimer)
  - [Contributing](#contributing)
  - [How it works (high-level)](#how-it-works-high-level)
  - [Known limitations](#known-limitations)
  - [Credits](#credits)
  - [License](#license)

**Current State:** Prototype

Helm is a CLI-based torrent automation tool designed to fetch, filter, and send magnet links to qBittorrent. 

It now handles its own dependencies seamlessly using Docker or Podman, spinning up Jackett, Flaresolverr, and qBittorrent automatically! Later, I plan to publish my own indexer files for easier setup so hell yeah?!.

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
If you are on a Mac, the easiest way to get the required container engine is via Homebrew:
```bash
# To install Docker Desktop:
brew install --cask docker

# OR to install Podman Desktop:
brew install podman podman-desktop podman-compose
```

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

## Configuration

Helm uses a combo of env variables and JSON configuration files cleanly sandboxed away from your host OS.

What these do:

- `docker_data/.env.docker`: Environment variables for the containers
- `docker_data/jackett/`: Jackett configuration and indexers
- `docker_data/qbittorrent/`: qBittorrent configuration and state
- `docker_data/downloads/`: Your downloaded files

Will see how the configuration changes based on the state of the project/its version.

## Usage (WIP)

Helm is currently run from the CLI using the generated launcher script.

Typical workflow:

1. Run `./setup.sh` to configure indexers and credentials.
2. Run `./helm.sh` to fetch RSS feeds and search.
3. Matching torrents are filtered and sent to qBittorrent automatically.

You can also force one-shot modes and auto-downloads:
```bash
# Force one-shot mode for a single search
./helm.sh --oneshot

# Auto-download the top result for a query
./helm.sh --oneshot --auto -q "Ubuntu 24.04" --type software

# Run built-in Lite Mode (without Jackett/Docker)
helm --lite

# Manage Jackett indexers
helm --indexers
```

More detailed usage instructions will be added as the project stablize so hang in there :<

## Disclaimer

Helm does **NOT** host, distribute, or provide any copyrighted content.

This tool simply automates (kinda) the process of fetching RSS feeds and sending magnet links to a torrent client.

However you use this tool is your responsibility gangster.

## Contributing

Contributions are welcome especially but not limited to bug fixes, refactors or doumenation improvements.

If you plan to add a major feature or change behavior, it's prolly a good idea to open an issue.

with that out the way this project is real close to me since it's my official first project so let's make this bozo go (perchance).

## How it works (high-level)

Helm pulls torrent RSS feeds from configured indexers, applies filtering and deduplication rules, and automatically sends matching magnet links to qBittorrent.

## Known limitations

- Project is still in early prototype stage.
- Configuration format could change.
- Error handeling maybe minimal in some areas here and there.
- Not extensively tested on all platforms.

## Credits

shoutout to the goats (this project is built with these open-source tools btw) qBittorrent, Jackett, and Flaresolverr.

## License

This project is licensed under the GPL License.
See the [LICENSE](LICENSE) file for details.
