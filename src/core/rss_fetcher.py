"""
core/rss_fetcher.py

responsible for fetching and parsing RSS(really simple syndication) feeds from multiple indexers

"""

import os
import xml.etree.ElementTree as ET
import requests
import email.utils
import datetime
# from core.config_manager import load_config


# def fetch_feed(url):
#     try:
#         return feedparser.parse(requests.get(url, timeout=10).text).entries
#     # IMP!! fix later and make more detailed catch statement
#     except Exception as e:
#         print(f"An error occured: {e}")
#         return []


# def fetch_all_feeds():
#     config = load_config()
#     all_items = []

#     for url in config["indexers"]:
#         all_items.extend(fetch_feed(url))
#     return all_items


class TorrentItem:
    def __init__(self, title, link, seeders, leechers=0, size=0, pubdate=None):
        self.title = title
        self.link = link
        self.seeders = seeders
        self.leechers = leechers
        self.size = size
        self.pubdate = pubdate

def search_jackett(query, content_type="video"):
    jackett_url = os.getenv("JACKETT_URL", "http://localhost:9117")
    api_key = os.getenv("JACKETT_API_KEY")
    if not api_key:
        raise RuntimeError("JACKETT_API_KEY environment variable not set")
        
    url = f"{jackett_url}/api/v2.0/indexers/all/results/torznab/api"
    
    category_map = {
        "video": "2000,5000",
        "games": "4000",
        "software": "4000",
        "books": "8000",
        "music": "3000"
    }
    
    params = {
        "apikey": api_key,
        "q": query,
    }
    cat = category_map.get(content_type)
    if cat:
        params["cat"] = cat

    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        
        root = ET.fromstring(r.text)
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
                    try: seeders = int(value)
                    except ValueError: pass
                elif name == "peers":
                    try: 
                        peers = int(value)
                        leechers = peers - seeders
                    except ValueError: pass
                elif name == "leechers":
                    try: leechers = int(value)
                    except ValueError: pass
                elif name == "size" and size == 0:
                    try: size = int(value)
                    except ValueError: pass

            if leechers < 0:
                leechers = 0

            items.append(TorrentItem(title, link, seeders, leechers, size, pubdate))
        if len(items) == 0:
            print(f"\n[DEBUG] Jackett returned 0 items. Raw XML response (first 2000 chars):\n{r.text[:2000]}\n")
        return items
    except requests.exceptions.RequestException as e:
        print(f"Network error while connecting to Jackett: {e}")
        return []
    except Exception as e:
        print(f"Unexpected error parsing results: {e}")
        return []

    # return all_items
