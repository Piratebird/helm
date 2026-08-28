# VERSION: 1.0
# AUTHORS: helm

# 1337x front pages are Cloudflare-protected on some mirrors but consistently
# reachable on at least one of several mirrors. Search rotates through the
# host list until one answers, then fetches magnets from the top-seeded detail
# pages (kept to a handful of requests so the site is treated lightly).

import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from helpers import retrieve_url
from novaprinter import prettyPrinter

from helm.core.logger import get_logger

logger = get_logger(__name__)

HOSTS = ["1337x.to", "1337x.st", "x1337x.ws", "1337xx.to"]
MAX_DETAILS = 4
CATEGORIES = ["Movies", "TV"]

_working_host = 0


def parse_rows(html):
    """Return a list of {name, path, seeders, leechers, size} from a 1337x list page."""
    start = html.find("table-list")
    if start < 0:
        return []
    rows = []
    for chunk in html[start:].split("<tr")[1:]:
        link = re.search(r'href="(/torrent/[^"]+)"[^>]*>([^<]+)</a>', chunk)
        if not link:
            continue
        size = re.search(r'class="coll-4 size[^"]*">\s*([\d.]+\s*[KMGT]i?B)', chunk)
        seeds = re.search(r'class="coll-2 seeds[^"]*">\s*(\d+)', chunk)
        leeches = re.search(r'class="coll-3 leeches[^"]*">\s*(\d+)', chunk)
        rows.append(
            {
                "name": _unescape(link.group(2).strip()),
                "path": link.group(1),
                "seeds": int(seeds.group(1)) if seeds else 0,
                "leechers": int(leeches.group(1)) if leeches else 0,
                "size": size.group(1) if size else "0 B",
            }
        )
    return rows


def extract_magnet(html):
    """Return the magnet link found on a 1337x detail page, if any."""
    match = re.search(r"magnet:\?xt=urn:btih:[^\"'<>\s]+", html)
    return _unescape(match.group(0)) if match else None


def _unescape(s):
    return s.replace("&amp;", "&").replace("&#x27;", "'").replace("&quot;", '"').replace("&#039;", "'")


def _relevance(name, query):
    """A 1337x mirror may fall back to a generic list on a miss; only keep rows
    that plausibly match the query."""
    norm_name = name.lower().replace(".", " ").replace("_", " ")
    norm_query = query.lower()
    words = [w for w in norm_query.split() if w.isalnum()]
    if not words:
        return False
    if norm_query in norm_name:
        return True
    return all(w in norm_name for w in words)


def _category_path(query, category):
    encoded = urllib.parse.quote(query).replace("%20", "+")
    return f"/category-search/{encoded}/{category}/1/"


class x1337(object):
    url = "https://1337x.to"
    name = "1337x"
    supported_categories = {"all": "all", "movies": "movies", "tv": "tv"}

    def search(self, what, cat="all"):
        query = what.strip()
        if not query:
            return

        base = None
        list_html = None
        for i in range(len(HOSTS)):
            host = HOSTS[(_working_host + i) % len(HOSTS)]
            try:
                html = retrieve_url(f"https://{host}{_category_path(query, CATEGORIES[0])}")
                rows = parse_rows(html)
                if not rows:
                    continue  # empty or a Cloudflare wall page
                base = f"https://{host}"
                list_html = html
                _set_working_host(host)
                break
            except Exception:
                continue
        if not base:
            return

        rows = parse_rows(list_html)

        # The "all" category handed to the plugin can be either; search the
        # other category too so movies and TV overlap is recovered.
        for category in CATEGORIES[1:]:
            try:
                html = retrieve_url(f"{base}{_category_path(query, category)}")
                rows.extend(parse_rows(html))
            except Exception:
                continue

        seen = set()
        unique = []
        for row in rows:
            if not _relevance(row["name"], query):
                continue
            key = (row["name"].lower(), row["size"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)

        unique.sort(key=lambda r: r["seeds"], reverse=True)

        magnets = {}
        with ThreadPoolExecutor(max_workers=MAX_DETAILS) as pool:
            futures = {pool.submit(retrieve_url, f"{base}{row['path']}"): row["path"] for row in unique[:MAX_DETAILS]}
            for future in as_completed(futures):
                path = futures[future]
                try:
                    magnets[path] = extract_magnet(future.result())
                except Exception:
                    magnets[path] = None

        for row in unique[:MAX_DETAILS]:
            magnet = magnets.get(row["path"])
            if not magnet:
                continue
            prettyPrinter(
                {
                    "name": row["name"],
                    "link": magnet,
                    "size": row["size"],
                    "seeds": str(row["seeds"]),
                    "leech": str(row["leechers"]),
                    "engine_url": self.url,
                    "desc_link": f"{base}{row['path']}",
                }
            )


def _set_working_host(host):
    global _working_host
    _working_host = HOSTS.index(host) if host in HOSTS else 0
    logger.debug(f"Event: 1337x working mirror is {host}")
