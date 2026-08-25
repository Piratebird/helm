import datetime
import logging

import requests

from helm.core.rss_fetcher import TorrentItem

logger = logging.getLogger(__name__)

def search_lite(query):
    """
    Lite mode fetcher using public APIs (e.g., apibay) without needing Jackett.
    """
    items = []

    # Apibay (The Pirate Bay API)
    apibay_url = "https://apibay.org/q.php"
    try:
        r = requests.get(apibay_url, params={"q": query}, timeout=15)
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
            encoded_name = requests.utils.quote(title)
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

            items.append(TorrentItem(title, magnet, seeders, leechers, size, pubdate))

    except Exception as e:
        logger.debug(f"Event: Apibay connection failed: {e}")


    # Torrents-csv API (Aggregator)
    torrents_csv_url = "https://torrents-csv.com/service/search"
    try:
        r = requests.get(torrents_csv_url, params={"q": query, "size": 100}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("torrents", []):
                title = item.get("name", "")
                info_hash = item.get("infohash", "")
                if not info_hash:
                    continue

                encoded_name = requests.utils.quote(title)
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

                items.append(TorrentItem(title, magnet, seeders, leechers, size, pubdate))
    except Exception as e:
        logger.debug(f"Event: Torrents-csv connection failed: {e}")

    # Nyaa RSS API (Anime/Asian content)
    nyaa_url = f"https://nyaa.iss.one/?page=rss&q={requests.utils.quote(query)}&c=0_0&f=0"
    try:
        r = requests.get(nyaa_url, timeout=15)
        if r.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.content)
            for item in root.findall('./channel/item'):
                title = item.findtext('title') or ""
                magnet = None
                seeders = 0
                leechers = 0
                size = 0

                # Nyaa stores the magnet in the torrent namespace or in link
                for child in item:
                    if child.tag.endswith('seeders'):
                        seeders = int(child.text or 0)
                    elif child.tag.endswith('leechers'):
                        leechers = int(child.text or 0)
                    elif child.tag.endswith('size'):
                        size_str = child.text or "0"
                        if "GiB" in size_str: size = int(float(size_str.replace(" GiB","")) * 1024**3)
                        elif "MiB" in size_str: size = int(float(size_str.replace(" MiB","")) * 1024**2)
                    elif child.tag.endswith('infoHash'):
                        info_hash = child.text
                        magnet = f"magnet:?xt=urn:btih:{info_hash}&dn={requests.utils.quote(title)}&tr=http%3A%2F%2Fnyaa.tracker.wf%3A7777%2Fannounce"

                pubdate = item.findtext('pubDate') or None
                if pubdate:
                    try:
                        # Parse "Sun, 08 Dec 2024 16:53:15 +0000"
                        from email.utils import parsedate_to_datetime
                        pubdate = parsedate_to_datetime(pubdate).strftime("%Y-%m-%d")
                    except Exception:
                        pass

                if magnet:
                    items.append(TorrentItem(title, magnet, seeders, leechers, size, pubdate))
    except Exception as e:
        logger.debug(f"Event: Nyaa connection failed: {e}")

    # --- qBittorrent Plugins Integration --- #
    import os
    try:
        from helm.core.lite_plugin_loader import run_plugins

        # Define where to look for plugins
        plugin_dirs = [
            os.path.expanduser("~/.helm_data/plugins"), # User plugins
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins") # Bundled plugins
        ]

        plugin_results = run_plugins(query, plugin_dirs)
        if plugin_results:
            items.extend(plugin_results)
    except Exception as e:
        logger.debug(f"Event: Plugin loader failed: {e}")

    return items

