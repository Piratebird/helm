# Helm - Torrent Automation Prototype

**Current State:** Prototype

Helm is a CLI-based torrent automation tool designed to fetch, filter, and send magnet links to qBittorrent. Currently, the project is in a prototype stage. To fully operate, it requires a few external services:

- **Flaresolverr** (run via Docker image)
- **Jackett** (as a service)
- **qBittorrent-nox** (headless torrent client)

Later, I plan to publish my own indexer files for easier setup.

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

<!-- rb_libtorrent-devel rb_libtorrent-python3 -->
Debian / Ubuntu
<!-- sudo apt install python3-libtorrent -->
```

## Project Structure

```
helm/
├── core/
│ ├── config_manager.py # Step 0 → load/save config & .env
│ ├── indexer_setup.py # Step 1 → add indexers / build RSS URLs
│ ├── rss_fetcher.py # Step 2 → fetch & parse RSS feeds
│ ├── torrent_filter.py # Step 3 → deduplicate, filter, sort
│ ├── qbittorrent_client.py # Step 4 → send magnet links to qBittorrent
│ └── utils.py # Small helper functions
├── main.py # CLI entry point: search, download
├── config.json # Stores indexers, qualities, min_seeds
├── .env.example # Shows variables for Jackett URL/API key
└── README.md # Quick usage instructions
```
