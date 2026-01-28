"""
core/rss_fetcher.py

responsible for fetching and parsing RSS(really simple syndication) feeds from multiple indexers

"""

from dotenv import load_dotenv
import os
import feedparser
import requests
# from core.config_manager import load_config

load_dotenv()
JACKETT_URL = os.getenv("JACKETT_URL", "http://localhost:9117")
API_KEY = os.getenv("JACKETT_API_KEY")

if API_KEY is None:
    raise RuntimeError("JACKETT_API_KEY environment variable not set")


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


def search_jackett(query):
    url = f"{JACKETT_URL}/api/v2.0/indexers/all/results/torznab/api"
    params = {
        "apikey": API_KEY,
        "q": query,
    }

    try:
        r = requests.get(url, params=params, timeout=30)
        return feedparser.parse(r.text).entries
    except Exception as e:
        print(f"Search error: {e}")

    # return all_items
