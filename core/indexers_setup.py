"""
core/indexer_setup.py

used for setting up the indexers used by jackett api
,such as piratebay, 1337x ...etc

"""

### imports ###
import requests

# fromt the config_manager module we import the jackett api jackett url and the config functions
from core.config_manager import JACKETT_URL, API_KEY, load_config, save_config

# the value is constant shouldn't change
DEFAULT_INDEXERS = ["1337x", "torrentgalaxy", "YTS", "nyaa", "thepiratebay"]


def add_indexer(indexer):
    url = f"{JACKETT_URL}/api/v2.0/indexers"
    headers = {"X-Api-key": API_KEY}
    payload = {"indexer": indexer}
    r = requests.post(url, json=payload, headers=headers)
    return r.status_code == 200


def setup_indexers():
    config = load_config()
    rss_urls = []

    for ix in DEFAULT_INDEXERS:
        if add_indexer(ix):
            rss_urls.append(
                f"{JACKETT_URL}/api/v2.0/indexers/{ix}/results/rss?apikey={API_KEY}"
            )

        config["indexers"] = rss_urls
        save_config(config)
        return rss_urls
