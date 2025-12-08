"""
core/rss_fetcher.py

responsible for fetching and parsing RSS(really simple syndication) feeds from multiple indexers

"""

import feedparser
import requests
from core.config_manager import load_config


def fetch_feed(url):
    try:
        return feedparser.parse(requests.get(url, timeout=10).text).entries
    # IMP!! fix later and make more detailed catch statement
    except:
        return []


def fetch_all_feeds():
    config = load_config()
    all_items = []

    for url in config["indexers"]:
        all_items.extend(fetch_feed(url))
    return all_items
