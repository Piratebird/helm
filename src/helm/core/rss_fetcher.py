"""
core/rss_fetcher.py

responsible for fetching and parsing RSS(really simple syndication) feeds from multiple indexers

"""

import email.utils
import os
import xml.etree.ElementTree as ET

import requests

from helm.core.logger import get_logger
from helm.core.secret_manager import get_secret

logger = get_logger(__name__)


class TorrentItem:
    def __init__(self, title, link, seeders, leechers=0, size=0, pubdate=None, indexer="Unknown"):
        self.title = title
        self.link = link
        self.seeders = seeders
        self.leechers = leechers
        self.size = size
        self.pubdate = pubdate
        self.indexer = indexer


CATEGORY_MAP = {"video": "2000,5000", "games": "4000", "software": "4000", "books": "8000", "music": "3000"}


def _parse_feed(xml_text):
    root = ET.fromstring(xml_text)
    ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
    items = []
    for elem in root.findall("./channel/item"):
        title = elem.findtext("title", default="")
        link = elem.findtext("link", default="")

        pubdate_text = elem.findtext("pubDate")
        pubdate = None
        if pubdate_text:
            try:
                parsed = email.utils.parsedate_to_datetime(pubdate_text)
                pubdate = parsed.strftime("%Y-%m-%d")
            except Exception:
                pass

        size_elem = elem.find("size")
        size = 0
        if size_elem is not None and size_elem.text and size_elem.text.isdigit():
            size = int(size_elem.text)

        seeders = 0
        leechers = 0
        for attr in elem.findall("torznab:attr", namespaces=ns):
            name = attr.get("name")
            value = attr.get("value", "")
            if name == "seeders":
                try:
                    seeders = int(value)
                except ValueError:
                    pass
            elif name == "peers":
                try:
                    peers = int(value)
                    leechers = peers - seeders
                except ValueError:
                    pass
            elif name == "leechers":
                try:
                    leechers = int(value)
                except ValueError:
                    pass
            elif name == "size" and size == 0:
                try:
                    size = int(value)
                except ValueError:
                    pass

        if leechers < 0:
            leechers = 0

        indexer_elem = elem.find("jackettindexer")
        indexer = indexer_elem.text if indexer_elem is not None else "Jackett"

        items.append(TorrentItem(title, link, seeders, leechers, size, pubdate, indexer))
    return items


def _get_configured_indexers(jackett_url, api_key):
    url = f"{jackett_url}/api/v2.0/indexers/all/results/torznab/api"
    r = requests.get(url, params={"apikey": api_key, "t": "indexers"}, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    return [idx.get("id") for idx in root.findall("indexer") if idx.get("configured") == "true"]


def _search_indexer(jackett_url, api_key, indexer_id, query, cat):
    url = f"{jackett_url}/api/v2.0/indexers/{indexer_id}/results/torznab/api"
    params = {"apikey": api_key, "q": query}
    if cat:
        params["cat"] = cat
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return _parse_feed(r.text)


def _fetch_aggregate(jackett_url, api_key, query, cat):
    url = f"{jackett_url}/api/v2.0/indexers/all/results/torznab/api"
    params = {"apikey": api_key, "q": query}
    if cat:
        params["cat"] = cat
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    return _parse_feed(r.text)


def search_prowlarr(query, content_type="video"):
    from helm.core.config_manager import load_config
    from helm.core.secret_manager import get_secret

    config = load_config()
    prowlarr_url = config.get("PROWLARR_URL", "http://localhost:19696")
    api_key = get_secret("PROWLARR_API_KEY")

    if not api_key or "your_prowlarr_api_key_here" in api_key.lower():
        logger.warning("Event: Prowlarr API key not configured or is placeholder.")
        return []

    # Seed default indexers on first use when none are configured.
    try:
        idx_r = requests.get(f"{prowlarr_url}/api/v1/indexer", headers={"X-Api-Key": api_key}, timeout=5)
        if idx_r.status_code == 200 and len(idx_r.json()) == 0:
            logger.info("Event: Prowlarr indexers empty. Seeding defaults...")
            from helm.core.indexer_manager import ProwlarrManager

            os.environ["PROWLARR_URL"] = prowlarr_url
            seed_count = ProwlarrManager().seed_default_indexers()
            logger.info("Event: Seeded %s default Prowlarr indexers.", seed_count)
    except Exception:
        pass

    endpoint = f"{prowlarr_url}/api/v1/search"
    params = {"query": query, "apikey": api_key}

    try:
        r = requests.get(endpoint, params=params, timeout=60)
        r.raise_for_status()

        results = []
        data = r.json()

        for item in data:
            title = item.get("title", "unknown")
            size = item.get("size", 0)
            seeders = item.get("seeders") or 0
            leechers = item.get("leechers") or 0
            indexer = item.get("indexer", "unknown")
            download_url = item.get("magnetUrl") or item.get("downloadUrl")

            if not download_url:
                continue

            results.append(
                TorrentItem(
                    title,
                    download_url,
                    seeders,
                    leechers,
                    size,
                    pubdate=None,
                    indexer=f"Prowlarr ({indexer})",
                )
            )

        logger.info(f"Event: Prowlarr aggregated JSON search returned {len(results)} results")
        return sorted(results, key=lambda x: x.seeders, reverse=True)

    except Exception as e:
        logger.error(f"Event: Prowlarr JSON search failed: {e}. Falling back to Torznab XML parsing.")

        cats = content_type.split(",")
        cat_ids = []
        for c in cats:
            if c in CATEGORY_MAP:
                cat_ids.append(CATEGORY_MAP[c])
        cat = ",".join(cat_ids) if cat_ids else CATEGORY_MAP.get("video")

        # Prowlarr Torznab: /{appId}/api?apikey=...&t=search&q=...
        torznab_url = f"{prowlarr_url}/1/api"
        params_xml = {"t": "search", "q": query, "apikey": api_key}
        if cat:
            params_xml["cat"] = cat

        try:
            r = requests.get(torznab_url, params=params_xml, timeout=120)
            r.raise_for_status()
            items = _parse_feed(r.text)
            logger.info(f"Event: Prowlarr Torznab fallback returned {len(items)} results")
            return items
        except Exception as ex:
            logger.error(f"Event: Prowlarr Torznab fallback also failed: {ex}", exc_info=True)
            return []


def search_indexers(query, content_type="video"):
    """
    Search using the configured Indexer Manager.

    Runs the configured manager (Prowlarr, Jackett, or both), falls back to the
    other manager when configured, and finally to native Lite Mode plugins so a
    search never comes up empty.
    """
    from helm.core.config_manager import load_config

    config = load_config()
    manager = config.get("INDEXER_MANAGER", "jackett")

    def dedupe(items):
        seen = set()
        unique = []
        for item in items:
            key = (item.title.lower().strip(), item.size)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    items = []
    if manager in ("prowlarr", "both"):
        items.extend(search_prowlarr(query, content_type))
    if manager in ("jackett", "both"):
        items.extend(search_jackett(query, content_type))
    elif manager == "prowlarr" and not items:
        logger.info("Event: Prowlarr returned no results, falling back to Jackett.")
        items.extend(search_jackett(query, content_type))

    if items:
        return dedupe(items)

    logger.info("Event: Media Server search failed or returned 0 results, falling back to Lite mode...")
    import asyncio

    from helm.core.lite_fetcher import search_all_plugins

    return dedupe(asyncio.run(search_all_plugins(query, content_type)))


def search_jackett(query, content_type="video"):
    jackett_url = os.getenv("JACKETT_URL", "http://localhost:9117")
    api_key = get_secret("JACKETT_API_KEY")
    if not api_key:
        logger.info("Event: Jackett API key not configured. Seamlessly falling back to native Lite Mode plugins.")
        return []

    # 1) Try JSON API first
    endpoint = f"{jackett_url}/api/v2.0/indexers/all/results"
    params = {"Query": query, "apikey": api_key}
    try:
        r = requests.get(endpoint, params=params, timeout=60)
        r.raise_for_status()

        results = []
        data = r.json()
        items = data.get("Results", [])

        for item in items:
            title = item.get("Title", "unknown")
            size = item.get("Size", 0)
            seeders = item.get("Seeders") or 0
            leechers = item.get("Peers") or 0
            indexer = item.get("Tracker", "unknown")
            download_url = item.get("MagnetUri") or item.get("Link")

            if not download_url:
                continue

            results.append(
                TorrentItem(title, download_url, seeders, leechers, size, pubdate=None, indexer=f"Jackett ({indexer})")
            )

        if results:
            logger.info(f"Event: Jackett aggregated JSON search returned {len(results)} results")
            return sorted(results, key=lambda x: x.seeders, reverse=True)

    except Exception as e:
        logger.error(f"Event: Jackett JSON search failed: {e}. Falling back to ThreadPool Torznab XML parsing.")

    # 2) Hard fallback to XML Torznab Parsing
    cats = content_type.split(",")
    cat_ids = []
    for c in cats:
        if c in CATEGORY_MAP:
            cat_ids.append(CATEGORY_MAP[c])
    cat = ",".join(cat_ids) if cat_ids else CATEGORY_MAP.get("video")

    try:
        indexers = _get_configured_indexers(jackett_url, api_key)
    except Exception as e:
        logger.debug(
            f"Event: Could not list configured indexers ({e}); falling back to aggregate search", exc_info=True
        )
        indexers = []

    items = []
    if indexers:
        import concurrent.futures

        def run(indexer_id):
            try:
                return indexer_id, _search_indexer(jackett_url, api_key, indexer_id, query, cat)
            except Exception as e:
                logger.debug(f"Event: Indexer '{indexer_id}' failed: {e}", exc_info=True)
                return indexer_id, []

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(run, idx) for idx in indexers]
            for future in concurrent.futures.as_completed(futures, timeout=120):
                indexer_id, indexer_items = future.result()
                if indexer_items:
                    logger.info(f"Event: Jackett indexer '{indexer_id}' returned {len(indexer_items)} results")
                    items.extend(indexer_items)

        # If every per-indexer query failed (e.g. all returned errors), retry
        # with Jackett's aggregate endpoint before giving up.
        if not items:
            try:
                items = _fetch_aggregate(jackett_url, api_key, query, cat)
            except Exception as e:
                logger.debug(f"Event: Jackett aggregate torznab also failed: {e}", exc_info=True)

    else:
        # No specific indexers found, try aggregate
        try:
            items = _fetch_aggregate(jackett_url, api_key, query, cat)
        except Exception as e:
            logger.debug(f"Event: Jackett aggregate torznab failed: {e}", exc_info=True)

    # Jackett's per-indexer endpoints can hand back the same release twice.
    # Collapse exact title+size duplicates so the dedupe and display stay clean.
    if len(items) > 1:
        seen = set()
        unique = []
        for item in items:
            key = (item.title.lower().strip(), item.size)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique
    return items
