import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath('src'))
from helm.core.indexer_manager import JackettManager
from helm.core.rss_fetcher import search_jackett

config_path = './docker_data/jackett/Jackett/ServerConfig.json'

print("Waiting for Jackett to generate ServerConfig.json...")
for _ in range(30):
    if os.path.exists(config_path):
        break
    time.sleep(2)

if not os.path.exists(config_path):
    print("Jackett failed to boot in time!")
    sys.exit(1)

with open(config_path, 'r') as f:
    config = json.load(f)
    api_key = config.get("APIKey")

if not api_key:
    print("Could not find APIKey in config!")
    sys.exit(1)

# Ensure the .env exists for the JackettManager to read
with open('.env', 'w') as f:
    f.write(f"JACKETT_API_KEY={api_key}\n")
    f.write("JACKETT_URL=http://localhost:19117\n")

# Need to reload env
os.environ['JACKETT_API_KEY'] = api_key
os.environ['JACKETT_URL'] = "http://localhost:19117"

try:
    manager = JackettManager()
    print("Adding 1337x tracker to Jackett...")
    manager.add_indexer("1337x")

    print("Starting search benchmark (Ubuntu)...")
    start_time = time.time()
    results = search_jackett("ubuntu")
    end_time = time.time()

    print(f"Torrents found: {len(results)}")
    print(f"Time taken: {end_time - start_time:.2f} seconds")
except Exception as e:
    print(f"Error: {e}")
