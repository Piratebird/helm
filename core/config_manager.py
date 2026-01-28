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

CONTENT_PROFILES = {
    "video": [
        "480p",
        "720p",
        "1080p",
        "2160p",
        "web",
        "webrip",
        "web-dl",
        "bluray",
        "bdrip",
        "brrip",
        "x264",
        "x265",
        "h264",
        "h265",
        "mkv",
    ],
    "games": [
        "pc",
        "repack",
        "gog",
        "fitgirl",
        "dodi",
        "elamigos",
        "codex",
        "flt",
        "skidrow",
        "steamrip",
    ],
    "software": [
        "x64",
        "x86",
        "win",
        "linux",
        "mac",
    ],
    "books": [
        # ebboks
        "pdf",
        "epub",
        "mobi",
        "azw",
        "azw3",
        "djvu",
        # audiobooks
        "audiobook",
        "m4b",
        "aac",
        # common shenagians
        "ebook",
        "e-book",
        "retail",
        "scan",
        "ocr",
    ],
    "music": [
        # formats
        "mp3",
        "flac",
        "aac",
        "wav",
        "alac",
        "ogg",
        # release types and what not
        "album",
        "single",
        "ep",
        "lp",
        "ost",
        "soundtrack",
        # quality
        "320kbps",
        "lossless",
        "24bit",
    ],
}

NEGATIVE_KEYWORDS = {
    "games": [
        "1080p",
        "2160p",
        "720p",
        "web",
        "webrip",
        "bluray",
        "bdrip",
        "x264",
        "x265",
        "h264",
        "h265",
        "season",
        "episode",
        "s01",
        "s02",
    ],
    "video": ["repack", "fitgirl", "dodi", "codex", "skidrow", "gog"],
    "software": ["1080p", "2160p", "720p", "bluray", "bdrip", "x264", "x265"],
    "books": [
        # video/tv
        "1080p",
        "2160p",
        "720p",
        "web",
        "webrip",
        "bluray",
        "bdrip",
        "x264",
        "x265",
        "h264",
        "h265",
        "season",
        "episode",
        "s01",
        "s02",
        # games/software
        "repack",
        "gog",
        "fitgirl",
        "dodi",
        "elamigos",
        "codex",
        "flt",
        "skidrow",
        "steamrip",
        "x64",
        "x86",
        "win",
        "linux",
        "mac",
        # music
        "flac",
        "aac",
        "wav",
        "album",
        "single",
        "ep",
        "lp",
        "ost",
        "soundtrack",
    ],
}


def load_config():
    if not os.path.exists("config.json"):
        return {
            "indexers": [],
            "qualities": CONTENT_PROFILES["video"],
            "min_seeds": 3,
        }
        # the default values for the json file if it's not detected/exists

    with open("config.json") as f:
        try:
            content = f.read().strip()
            if not content:
                return {
                    "indexers": [],
                    "qualities": CONTENT_PROFILES["video"],
                    "min_seeds": 3,
                }
            return json.loads(content)
        except json.JSONDecodeError:
            print("Config file is corrupted, resetting.")
            return {
                "indexers": [],
                "qualities": CONTENT_PROFILES["video"],
                "min_seeds": 3,
            }  # return {
            #     "indexers": [],
            #     "qualities": [
            #         "480p",
            #         "720p",
            #         "1080p",
            #         "2160p",
            #         "WEB-DL",
            #         "WEBRip",
            #         "BluRay",
            #     ],
            #     "min_seeds": 3,
            # }


# saving the configuration that are passed to it and formatting it
def save_config(config):
    with open("config.json", "w") as f:
        json.dump(config, f, indent=4)
