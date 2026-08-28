# VERSION: 1.0
# AUTHORS: Helm

# yts.lt YIFY API (movies). yts.mx is intermittently down; yts.lt resolves the
# same API and returns 200 without Cloudflare as of 2026-08.

import requests
from novaprinter import prettyPrinter

from helm.core.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://yts.lt/api/v2/list_movies.json"
TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.coppersurfer.tk:6969/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.demonii.com:1337/announce",
)


class yts(object):
    url = "https://yts.lt"
    name = "YTS"
    supported_categories = {"all": "all", "movies": "all"}

    def search(self, what, cat="all"):
        try:
            r = requests.get(
                API_URL,
                params={"query_term": what, "limit": 50},
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Helm/0.9"},
                timeout=20,
            )
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            logger.debug(f"Event: YTS API connection failed: {e}")
            return

        if payload.get("status") != "ok":
            logger.debug(f"Event: YTS API returned status {payload.get('status')!r}")
            return

        movies = (payload.get("data") or {}).get("movies") or []
        trackers = "&".join(f"tr={requests.utils.quote(t)}" for t in TRACKERS)

        for movie in movies:
            title = movie.get("title") or movie.get("title_english") or movie.get("title_long") or ""
            year = movie.get("year")
            if not title:
                continue
            base = f"{title} ({year})" if year else title

            for torrent in movie.get("torrents") or []:
                info_hash = torrent.get("hash")
                if not info_hash:
                    continue
                quality = torrent.get("quality") or ""
                ttype = torrent.get("type") or ""
                display = " ".join(part for part in (base, quality, ttype) if part)

                size_bytes = torrent.get("size_bytes")
                if not size_bytes:
                    try:
                        size_str = torrent.get("size") or "0 B"
                        multiplier = {"GB": 1024**3, "MB": 1024**2, "KB": 1024}
                        for unit, factor in multiplier.items():
                            if unit in size_str:
                                size_bytes = int(float(size_str.replace(unit, "").strip()) * factor)
                                break
                        else:
                            size_bytes = int(size_str.replace("B", "").strip())
                    except (ValueError, TypeError):
                        size_bytes = 0

                seeders = torrent.get("seeds", 0) or 0
                leechers = torrent.get("peers", 0) or 0

                magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={requests.utils.quote(display)}&{trackers}"

                prettyPrinter(
                    {
                        "name": display,
                        "link": magnet,
                        "size": str(size_bytes) + " B",
                        "seeds": str(seeders),
                        "leech": str(leechers),
                        "engine_url": self.url,
                        "desc_link": movie.get("url") or self.url,
                        "pub_date": int(torrent.get("date_uploaded_unix") or 0),
                    }
                )
