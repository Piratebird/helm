"""Public indexers Helm sets up by default in Jackett and Prowlarr.

Each entry is a Jackett/Prowlarr definition id and the site link Jackett should
use. Links are the first entry of each definition's ``links`` list. Checked
2026-11-03 against a fresh Jackett with FlareSolverr: every one returned
results for a common query. The ones marked "Cloudflare" need FlareSolverr,
which Helm's stack runs. Skipped on purpose: torrentgalaxyclone (no results)
and internetarchive (matches any metadata, mostly not releases).
"""

PUBLIC_INDEXERS = (
    ("1337x", "https://1337x.to/"),  # Cloudflare
    ("yts", "https://yts.gg/"),
    ("nyaasi", "https://nyaa.si/"),
    ("thepiratebay", "https://thepiratebay.org/"),
    ("eztv", "https://eztvx.to/"),  # Cloudflare, TV only
    ("limetorrents", "https://www.limetorrents.fun/"),
    ("therarbg", "https://therarbg.to/"),  # Jackett only; Prowlarr has no definition
    ("torrentdownloads", "https://www.torrentdownloads.pro/"),
    ("torrentdownload", "https://www.torrentdownload.info/"),
    ("torrentproject2", "https://torrentproject.cc/"),
    ("knaben", "https://knaben.org/"),  # meta-search over many trackers
    ("uindex", "https://uindex.org/"),  # Cloudflare
    ("kickasstorrents-to", "https://kickass.torrentbay.st/"),  # Cloudflare
    ("extratorrent-st", "https://extratorrent.st/"),  # Cloudflare
)

PUBLIC_INDEXER_IDS = tuple(name for name, _ in PUBLIC_INDEXERS)
