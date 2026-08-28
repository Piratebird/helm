# VERSION: 1.0
# AUTHORS: helm

# BitTorrented is a general index (its own library plus a large DHT crawl) with
# a JSON search API that returns real swarm counts and info hashes. Only its
# "video" type is consumed here; helm's other categories are left to their
# dedicated sources.

import re

import requests
from novaprinter import prettyPrinter

from helm.core.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://bittorrented.com/api/search/torrents"
MIN_QUERY = 3
INFO_HASH = re.compile(r"^[a-f0-9]{40}$")


def map_results(results):
    """Map API rows to prettyPrinter dicts, dropping rows without a valid hash."""
    out = []
    for r in results:
        info_hash = (r.get("torrent_infohash") or "").lower()
        if not INFO_HASH.match(info_hash):
            continue
        name = r.get("torrent_name") or info_hash
        out.append(
            {
                "name": name,
                "link": f"magnet:?xt=urn:btih:{info_hash}&dn={requests.utils.quote(name)}",
                "size": f"{r.get('torrent_total_size') or 0} B",
                "seeds": str(r.get("torrent_seeders") or 0),
                "leech": str(r.get("torrent_leechers") or 0),
                "engine_url": "https://bittorrented.com",
                "desc_link": f"https://bittorrented.com/torrent/{r.get('torrent_id') or ''}",
            }
        )
    return out


class bittorrented(object):
    url = "https://bittorrented.com"
    name = "BitTorrented"
    supported_categories = {"all": "all", "movies": "all", "tv": "all"}

    def search(self, what, cat="all"):
        query = what.strip()
        # The index requires a real query (the API rejects fewer than 3 chars).
        if len(query) < MIN_QUERY:
            return
        try:
            r = requests.get(
                API_URL,
                params={
                    "q": query,
                    "type": "video",
                    "limit": 50,
                    "sortBy": "seeders",
                    "sortOrder": "desc",
                },
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) helm/0.9.1",
                    "Accept": "application/json",
                },
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.debug(f"Event: BitTorrented API connection failed: {e}")
            return

        results = (payload or {}).get("results") or []
        for item in map_results(results):
            prettyPrinter(item)
