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


import time

def add_indexer(indexer):
    url = f"{JACKETT_URL}/api/v2.0/indexers"
    headers = {"X-Api-key": API_KEY}
    payload = {"indexer": indexer}
    
    for attempt in range(5):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=5)
            return r.status_code == 200
        except requests.exceptions.ConnectionError:
            print(f"Jackett is not ready yet, retrying... ({attempt+1}/5)")
            time.sleep(3)
    
    print(f"Failed to connect to Jackett to add indexer {indexer}.")
    return False


def setup_indexers():
    config = load_config()
    existing_indexers = config.get("indexers", [])

    print("Setting up jackett indexers...")
    new_indexers_added = False
    
    for ix in DEFAULT_INDEXERS:
        if ix not in existing_indexers:
            success = add_indexer(ix)
            if success:
                existing_indexers.append(ix)
                new_indexers_added = True

    if new_indexers_added:
        # store indexer names, not rss urls
        config["indexers"] = existing_indexers
        save_config(config)
