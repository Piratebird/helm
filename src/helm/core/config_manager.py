"""
core/config_manager.py

responsible for creating the config files for jackett and saving the configuration,
once the user use "helm" once later on it'll be just detetced and loaded

"""

## imports##
import json
import os
import shutil

## setting up the  URL and the API key from .env ##
# These are loaded dynamically in the modules that need them to avoid import crashes.

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
        # books
        "pdf",
        "epub",
        "mobi",
        "azw",
        "azw3",
        "djvu",
        "audiobook",
        "m4b",
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
    "video": [
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
        # books
        "pdf",
        "epub",
        "mobi",
        "azw",
        "azw3",
        "djvu",
        "audiobook",
        "m4b",
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
    "software": [
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
        # games
        "repack",
        "gog",
        "fitgirl",
        "dodi",
        "elamigos",
        "codex",
        "flt",
        "skidrow",
        "steamrip",
        # books
        "pdf",
        "epub",
        "mobi",
        "azw",
        "azw3",
        "djvu",
        "audiobook",
        "m4b",
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
    "music": [
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
        # books
        "pdf",
        "epub",
        "mobi",
        "azw",
        "azw3",
        "djvu",
        "audiobook",
        "m4b",
    ],
}


import sys  # noqa: E402


def get_config_dir():
    if "HELM_CONFIG_DIR" in os.environ:
        return os.environ["HELM_CONFIG_DIR"]
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "helm")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/helm")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return os.path.join(base, "helm")


def get_log_dir():
    if "HELM_STATE_DIR" in os.environ:
        return os.environ["HELM_STATE_DIR"]
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "helm", "logs")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/helm")
    else:
        base = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
        return os.path.join(base, "helm", "logs")


def get_dl_dir():
    if "HELM_DL_DIR" in os.environ:
        return os.environ["HELM_DL_DIR"]
    if sys.platform == "win32":
        return os.path.expanduser("~\\Downloads\\helm")
    else:
        return os.path.expanduser("~/Downloads/helm")


def get_config_file():
    config_dir = get_config_dir()
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "config.json")


def _migrate_old_config():
    old_dir = os.path.expanduser("~/.helm_data")
    old_file = os.path.join(old_dir, "config.json")
    new_file = get_config_file()

    if os.path.exists(old_file) and not os.path.exists(new_file):
        try:
            shutil.copy2(old_file, new_file)
            print(f"Migrated config from {old_file} to {new_file}")
        except Exception as e:
            print(f"Failed to migrate config: {e}")


def load_config():
    _migrate_old_config()
    config_file = get_config_file()

    default_config = {
        "indexers": [],
        "qualities": CONTENT_PROFILES["video"],
        "min_seeds": 3,
        "EXECUTION_MODE": "native",
        "LITE_MODE_ONLY": False,
    }

    if not os.path.exists(config_file):
        return default_config

    with open(config_file) as f:
        try:
            content = f.read().strip()
            if not content:
                return default_config

            loaded = json.loads(content)
            # Ensure new keys exist
            for k, v in default_config.items():
                if k not in loaded:
                    loaded[k] = v
            return loaded
        except json.JSONDecodeError:
            print("Config file is corrupted. Backing up to config.json.bak and resetting.")
            shutil.copy(config_file, config_file + ".bak")
            return default_config


def save_config(config):
    with open(get_config_file(), "w") as f:
        json.dump(config, f, indent=4)
