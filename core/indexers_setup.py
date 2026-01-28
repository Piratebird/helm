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

    # if they're already setup
    if config.get("indexers"):
        return
    print("Setting up jackett indexers...")
    for ix in DEFAULT_INDEXERS:
        add_indexer(ix)

    # store indexer names, not rss urls
    config["indexers"] = DEFAULT_INDEXERS
    save_config(config)
