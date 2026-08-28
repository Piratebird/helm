# VERSION: 2.0
# AUTHORS: helm

# The HTML search endpoint (eztvx.to/search/... ) is Cloudflare-blocked (HTTP
# 403 "Just a moment"), so this plugin uses eztvx.to's public JSON API instead.
# The API exposes the latest releases (no server-side search): matching titles
# are filtered client-side from the most recent 100 episodes.

import requests
from novaprinter import prettyPrinter

from helm.core.logger import get_logger

logger = get_logger(__name__)


class eztv(object):
    name = "EZTV"
    url = "https://eztvx.to"
    supported_categories = {"all": "all", "tv": "all"}

    def search(self, what, cat="all"):
        query = what.strip().lower()
        try:
            r = requests.get(
                f"{self.url}/api/get-torrents",
                params={"limit": 100, "page": 1},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) helm/0.9.1"},
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.debug(f"Event: EZTV API connection failed: {e}")
            return

        for torrent in payload.get("torrents") or []:
            title = torrent.get("title") or torrent.get("filename") or ""
            if not title:
                continue
            if query and query not in title.lower():
                continue

            info_hash = (torrent.get("hash") or "").lower()
            if not info_hash:
                continue

            magnet = torrent.get("magnet_url") or ""
            if not magnet:
                magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={requests.utils.quote(title)}"

            try:
                size = int(torrent.get("size_bytes") or 0)
            except (TypeError, ValueError):
                size = 0

            prettyPrinter(
                {
                    "name": title,
                    "link": magnet,
                    "size": f"{size} B",
                    "seeds": str(torrent.get("seeds", 0) or 0),
                    "leech": str(torrent.get("peers", 0) or 0),
                    "engine_url": self.url,
                    "desc_link": f"https://eztvx.to/ep/{info_hash}",
                    "pub_date": int(torrent.get("date_released_unix") or 0),
                }
            )
