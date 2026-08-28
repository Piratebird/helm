# VERSION: 1.0
# AUTHORS: helm

# SubsPlease exposes a small JSON API that returns recent/new anime with magnet
# links embedded. It carries no swarm data, so seeders/leechers are reported as
# 0 (the CLI keeps unknown-seed items per the min-seeds filter fix in 0.9.0).

import re

import requests
from novaprinter import prettyPrinter

from helm.core.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://subsplease.org/api/"
RES_PREFERENCE = ["1080", "720", "480"]


def parse_magnet(magnet):
    """Return (info_hash, size_bytes, dn) from a magnet link, or None."""
    match = re.search(r"urn:btih:([a-zA-Z0-9]{32,40})", magnet, re.IGNORECASE)
    if not match:
        return None
    info_hash = match.group(1).lower()
    size_match = re.search(r"[?&]xl=(\d+)", magnet)
    size = int(size_match.group(1)) if size_match else 0
    return info_hash, size


class subsplease(object):
    url = "https://subsplease.org"
    name = "SubsPlease"
    supported_categories = {"all": "all", "tv": "all", "anime": "all"}

    def search(self, what, cat="all"):
        query = what.strip()
        if not query:
            return
        try:
            r = requests.get(
                API_URL,
                params={"f": "search", "s": query, "tz": "UTC"},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) helm/0.9.1"},
                timeout=15,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.debug(f"Event: SubsPlease API connection failed: {e}")
            return

        if not isinstance(payload, dict):
            return

        for entry in payload.values():
            if not isinstance(entry, dict):
                continue
            downloads = entry.get("downloads") or []
            magnet = None
            for res in RES_PREFERENCE:
                pick = next(
                    (d for d in downloads if isinstance(d, dict) and d.get("res") == res and d.get("magnet")),
                    None,
                )
                if pick:
                    magnet = pick["magnet"]
                    resolution = res
                    break
            if magnet is None:
                pick = next(
                    (d for d in downloads if isinstance(d, dict) and d.get("magnet")),
                    None,
                )
                if pick:
                    magnet = pick["magnet"]
                    resolution = pick.get("res") or "?"
            if not magnet:
                continue

            parsed = parse_magnet(magnet)
            if not parsed:
                continue
            info_hash, size = parsed

            show = entry.get("show") or "Unknown"
            episode = entry.get("episode")
            name = f"{show} - {episode} [{resolution}p]" if episode else f"{show} [{resolution}p]"

            prettyPrinter(
                {
                    "name": name,
                    "link": magnet,
                    "size": f"{size} B",
                    "seeds": "0",
                    "leech": "0",
                    "engine_url": self.url,
                    "desc_link": entry.get("page") or self.url,
                    "pub_date": 0,
                }
            )
