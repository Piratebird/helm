# Dependencies (Fedora / RHEL)

sudo dnf install rb_libtorrent-devel rb_libtorrent-python3

# Dependencies (Debian and its derivatives)

sudo apt install python3-libtorrent

helm/
├── core/
│ ├── config_manager.py # Step 0 → load/save config & .env
│ ├── indexer_setup.py # Step 1 → add indexers / build RSS URLs
│ ├── rss_fetcher.py # Step 2 → fetch & parse RSS feeds
│ ├── torrent_filter.py # Step 3 → deduplicate, filter, sort
│ ├── qbittorrent_client.py # Step 4 → send magnet links to qBittorrent
│ └── utils.py # Small helper functions
├── **main**.py # CLI entry point: search, download, auto-watch
├── config.json # Stores indexers, qualities, min_seeds
├── .env.example # Shows variables for Jackett URL/API key
└── README.md # Quick usage instructions
