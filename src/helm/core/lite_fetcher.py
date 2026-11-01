import asyncio
import datetime
import os
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import List
from urllib.parse import quote

from helm.core.config_manager import get_config_dir
from helm.core.http import get_shared_session
from helm.core.lite_plugin_loader import PLUGIN_SEARCH_TIMEOUT
from helm.core.logger import get_logger
from helm.core.rss_fetcher import SearchResults, SourceOutcome, TorrentItem, source_status

logger = get_logger(__name__)


def _fetch_apibay(query: str) -> List[TorrentItem]:
    """The Pirate Bay public API (apibay)."""
    items = []
    apibay_url = "https://apibay.org/q.php"
    try:
        r = get_shared_session().get(apibay_url, params={"q": query}, timeout=15)
        r.raise_for_status()
        data = r.json()

        for item in data:
            if item.get("id") == "0" and item.get("name") == "No results returned":
                continue

            title = item.get("name", "")
            info_hash = item.get("info_hash", "")
            if not info_hash:
                continue

            # Construct magnet link with popular public trackers
            encoded_name = quote(title)
            magnet = (
                f"magnet:?xt=urn:btih:{info_hash}"
                f"&dn={encoded_name}"
                f"&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
                f"&tr=udp%3A%2F%2Ftracker.coppersurfer.tk%3A6969%2Fannounce"
                f"&tr=udp%3A%2F%2Ftracker.leechers-paradise.org%3A6969%2Fannounce"
                f"&tr=udp%3A%2F%2Ftracker.internetwarriors.net%3A1337%2Fannounce"
                f"&tr=udp%3A%2F%2Fexodus.desync.com%3A6969%2Fannounce"
                f"&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce"
                f"&tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce"
            )

            try:
                seeders = int(item.get("seeders", 0))
            except ValueError:
                seeders = 0

            try:
                leechers = int(item.get("leechers", 0))
            except ValueError:
                leechers = 0

            try:
                size = int(item.get("size", 0))
            except ValueError:
                size = 0

            try:
                added = int(item.get("added", 0))
                pubdate = datetime.datetime.fromtimestamp(added, datetime.timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, OSError, OverflowError):
                pubdate = None

            items.append(TorrentItem(title, magnet, seeders, leechers, size, pubdate, "Apibay"))

    except Exception as e:
        logger.debug(f"Event: Apibay connection failed: {e}")
        return SearchResults(items, [SourceOutcome("Apibay", source_status(e), len(items))])

    logger.info(f"Event: Apibay returned {len(items)} results")
    return SearchResults(items, [SourceOutcome("Apibay", "success" if items else "empty", len(items))])


def _fetch_torrents_csv(query: str) -> List[TorrentItem]:
    """Torrents-csv API (Aggregator)."""
    items = []
    torrents_csv_url = "https://torrents-csv.com/service/search"
    try:
        r = get_shared_session().get(torrents_csv_url, params={"q": query, "size": "100"}, timeout=15)
        r.raise_for_status()
        if r.status_code == 200:
            data = r.json()
            for item in data.get("torrents", []):
                title = item.get("name", "")
                info_hash = item.get("infohash", "")
                if not info_hash:
                    continue

                encoded_name = quote(title)
                magnet = (
                    f"magnet:?xt=urn:btih:{info_hash}"
                    f"&dn={encoded_name}"
                    f"&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce"
                )

                seeders = int(item.get("seeders", 0))
                leechers = int(item.get("leechers", 0))
                size = int(item.get("size_bytes", 0))

                added = int(item.get("created_unix", 0))
                if added > 0:
                    pubdate = datetime.datetime.fromtimestamp(added, datetime.timezone.utc).strftime("%Y-%m-%d")
                else:
                    pubdate = None

                items.append(TorrentItem(title, magnet, seeders, leechers, size, pubdate, "Torrents-csv"))
    except Exception as e:
        logger.debug(f"Event: Torrents-csv connection failed: {e}")
        return SearchResults(items, [SourceOutcome("Torrents-csv", source_status(e), len(items))])

    logger.info(f"Event: Torrents-csv returned {len(items)} results")
    return SearchResults(items, [SourceOutcome("Torrents-csv", "success" if items else "empty", len(items))])


def _fetch_nyaa(query: str) -> List[TorrentItem]:
    """Nyaa RSS API (Anime/Asian content)."""
    items = []
    nyaa_url = f"https://nyaa.si/?page=rss&q={quote(query)}&c=0_0&f=0"
    try:
        r = get_shared_session().get(nyaa_url, timeout=15)
        r.raise_for_status()
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall("./channel/item"):
                title = item.findtext("title") or ""
                magnet = None
                seeders = 0
                leechers = 0
                size = 0

                # Nyaa stores the magnet in the torrent namespace or in link
                for child in item:
                    if child.tag.endswith("seeders"):
                        seeders = int(child.text or 0)
                    elif child.tag.endswith("leechers"):
                        leechers = int(child.text or 0)
                    elif child.tag.endswith("size"):
                        size_str = child.text or "0"
                        if "GiB" in size_str:
                            size = int(float(size_str.replace(" GiB", "")) * 1024**3)
                        elif "MiB" in size_str:
                            size = int(float(size_str.replace(" MiB", "")) * 1024**2)
                    elif child.tag.endswith("infoHash"):
                        info_hash = child.text
                        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={quote(title)}&tr=http%3A%2F%2Fnyaa.tracker.wf%3A7777%2Fannounce"

                pubdate = item.findtext("pubDate") or None
                if pubdate:
                    try:
                        # Parse "Sun, 08 Dec 2024 16:53:15 +0000"
                        pubdate = parsedate_to_datetime(pubdate).strftime("%Y-%m-%d")
                    except Exception:
                        pass

                if magnet:
                    items.append(TorrentItem(title, magnet, seeders, leechers, size, pubdate, "Nyaa"))
    except Exception as e:
        logger.debug(f"Event: Nyaa connection failed: {e}")
        return SearchResults(items, [SourceOutcome("Nyaa", source_status(e), len(items))])

    logger.info(f"Event: Nyaa returned {len(items)} results")
    return SearchResults(items, [SourceOutcome("Nyaa", "success" if items else "empty", len(items))])


def _plugin_dirs():
    return [
        os.path.join(get_config_dir(), "plugins"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins"),
    ]


def _collect_all(query, content_type="video"):
    from helm.core.lite_plugin_loader import plugin_jobs
    from helm.core.search_runner import SearchJob, run_searches

    jobs = [
        SearchJob("Apibay", "inline", "apibay"),
        SearchJob("Torrents-csv", "inline", "csv"),
        SearchJob("Nyaa", "inline", "nyaa"),
    ]
    jobs.extend(plugin_jobs(_plugin_dirs()))
    return run_searches(jobs, query, content_type, timeout=PLUGIN_SEARCH_TIMEOUT)


def search_lite(query, content_type="video"):
    """Lite mode fetcher using public APIs (e.g., apibay) without needing Jackett."""
    query = query.strip()
    if not query:
        return []
    return _collect_all(query, content_type)


async def search_all_plugins(query, content_type="video"):
    """Async aggregate search over every lite source, for the indexer-manager fallback.

    All I/O is delegated to the worker pool inside :func:`search_lite`.
    """
    return await asyncio.get_running_loop().run_in_executor(None, search_lite, query, content_type)
