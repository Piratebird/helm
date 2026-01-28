# Helm - Torrent Automation Prototype

<p align="center">
  <img src="images/the_helm.jpeg" width="700">
</p>

**Current State:** Prototype

Helm is a CLI-based torrent automation tool designed to fetch, filter, and send magnet links to qBittorrent. Currently, the project is in a prototype stage. To fully operate, it requires a few 

## external services:

- **Flaresolverr** (run via Docker image)
- **Jackett** (as a service)
- **qBittorrent-nox** (headless torrent client)

Later, I plan to publish my own indexer files for easier setup so hell yeah?!.

---

## Features (Planned / Prototype)

1. Load and save configuration (.env and JSON)
2. Add indexers and build RSS URLs
3. Fetch and parse RSS feeds
4. Deduplicate, filter, and sort torrents
5. Send magnet links automatically to qBittorrent

---

## Dependencies

### Fedora / RHEL

```bash
sudo dnf install qbittorrent-nox
```
### Debian / Ubuntu
```bash
sudo apt install qbittorrent-nox
```

## Installation


```bash
# Clone the repo
git clone https://github.com/Piratebird/helm.git
cd helm

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

```


# Why this exists ?

Honestly for the most part it's for myself and my own usage i wanted to get magents of torrents and shows and it was annoying sometimes to look all over the internet for a torrent so i wanted to do that but with the terminal for the most part and heck yeah it gets the job done so far it's not perfect but it's my own so hell yeah :)