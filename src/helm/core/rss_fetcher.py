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
    r = requests.get(url, params=params, timeout=20)
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


def search_jackett(query, content_type="video"):
    jackett_url = os.getenv("JACKETT_URL", "http://localhost:9117")
    api_key = get_secret("JACKETT_API_KEY")
    if not api_key:
        logger.info("Event: Jackett API key not configured. Seamlessly falling back to native Lite Mode plugins.")
        return []

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
                logger.info(f"Event: Jackett indexer '{indexer_id}' returned {len(indexer_items)} results")
                items.extend(indexer_items)

        # If every per-indexer query failed (e.g. all returned errors), retry
        # with Jackett's aggregate endpoint before giving up.
        if not items:
            try:
                items = _fetch_aggregate(jackett_url, api_key, query, cat)
                logger.info(f"Event: Jackett aggregate fallback returned {len(items)} results")
            except requests.exceptions.RequestException as e:
                logger.debug("Event: Network error while connecting to Jackett", exc_info=True)
                raise RuntimeError(f"Jackett connection failed: {e}")  # noqa: B904
            except Exception as e:
                logger.debug("Event: Unexpected error during aggregate fallback", exc_info=True)
                raise RuntimeError(f"Unexpected error querying Jackett: {e}")  # noqa: B904
    else:
        try:
            items = _fetch_aggregate(jackett_url, api_key, query, cat)
            logger.info(f"Event: Jackett aggregate returned {len(items)} results")
        except requests.exceptions.RequestException as e:
            logger.debug("Event: Network error while connecting to Jackett", exc_info=True)
            raise RuntimeError(f"Jackett connection failed: {e}")  # noqa: B904
        except ET.ParseError:
            logger.debug("Event: XML parsing error", exc_info=True)
            raise RuntimeError("Invalid response from Jackett (XML Parse Error)")  # noqa: B904
        except ValueError as e:
            logger.debug("Event: Value error while parsing results", exc_info=True)
            raise RuntimeError(f"Value error while parsing Jackett results: {e}")  # noqa: B904
        except Exception as e:
            logger.debug("Event: Unexpected error parsing results", exc_info=True)
            raise RuntimeError(f"Unexpected error parsing Jackett results: {e}")  # noqa: B904

    # Jackett's per-indexer endpoints can hand back the same release twice.
    # Collapse exact title+size duplicates before returning so the CLI dedupe
    # and display stay clean.
    if len(indexers) > 1:
        seen = set()
        unique = []
        for item in items:
            key = (item.title.lower().strip(), item.size)
            if key not in seen:
                seen.add(key)
                unique.append(item)
        items = unique

    if len(items) == 0:
        logger.debug(f"Event: Jackett returned 0 items for query: '{query}'")
    return items
