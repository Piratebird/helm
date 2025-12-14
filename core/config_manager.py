"""
core/config_manager.py

responsible for creating the config files for jackett and saving the configuration,
once the user use "helm" once later on it'll be just detetced and loaded

"""

## imports##
import os
import json
from dotenv import load_dotenv

## setting up the  URL and the API key from .env ##

load_dotenv()

# the jackett url unless the user changed thiers before hand
JACKETT_URL = os.getenv("JACKETT_URL", "http://localhost:9117")
API_KEY = os.getenv("JACKETT_API_KEY")

if API_KEY is None:
    raise RuntimeError("JACKETT_API_KEY environment variable not set")


def load_config():
    if not os.path.exists("config.json"):
        return {
            # the default values for the json file if it's not detected/exists
            "indexers": [],
            "qualities": [
                "480p",
                "720p",
                "1080p",
                "2160p",
                "WEB-DL",
                "WEBRip",
                "BluRay",
            ],
            "min_seeds": 3,
        }

    with open("config.json") as f:
        try:
            content = f.read().strip()
            if not content:
                return {
                    "indexers": [],
                    "qualities": [
                        "480p",
                        "720p",
                        "1080p",
                        "2160p",
                        "WEB-DL",
                        "WEBRip",
                        "BluRay",
                    ],
                    "min_seeds": 3,
                }
            return json.loads(content)
        except json.JSONDecodeError:
            print("Config file is corrupted, resetting.")
            return {
                "indexers": [],
                "qualities": [
                    "480p",
                    "720p",
                    "1080p",
                    "2160p",
                    "WEB-DL",
                    "WEBRip",
                    "BluRay",
                ],
                "min_seeds": 3,
            }


# saving the configuration that are passed to it and formatting it
def save_config(config):
    with open("config.json", "w") as f:
        json.dump(config, f, indent=4)
