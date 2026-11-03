"""
core/config_manager.py

responsible for creating the config files for jackett and saving the configuration,
once the user use "helm" once later on it'll be just detetced and loaded

"""

## imports##
import os
import shutil
import sys

## setting up the  URL and the API key from .env ##
# These are loaded dynamically in the modules that need them to avoid import crashes.

# Credentials that must never be persisted in config files; they live in secrets.env.
SECRET_KEYS = frozenset(
    [
        "JACKETT_API_KEY",
        "JACKETT_PASSWORD",
        "PROWLARR_API_KEY",
        "QB_PASSWORD",
        "HELM_SERVER_TOKEN",
    ]
)

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

# Ad-hoc terms that are not keyword lists of another category but are still
# strong signals that a result belongs elsewhere (episode-related media terms
# are irrelevant for everything except video).
RELATED_MEDIA_TERMS = ["season", "episode", "s01", "s02"]


# Another category's keywords that are also normal in this category's release
# names: video releases name their audio track (x264 AAC, XviD MP3, FLAC) and TV
# packs use "Ep 01-06", and scene releases are re-released as REPACK. These only lower a result's rank instead of hiding it.
WEAK_TERMS = {
    "video": ["aac", "alac", "flac", "mp3", "ogg", "wav", "ep", "single", "repack"],
}


def _derived_negatives(cat):
    derived = [kw for other, kws in CONTENT_PROFILES.items() if other != cat for kw in kws]
    if cat != "video":
        derived += RELATED_MEDIA_TERMS
    return set(derived)


def _build_negative_keywords():
    """Each category's negatives are every *other* category's positives, minus its weak terms."""
    return {cat: sorted(_derived_negatives(cat) - set(WEAK_TERMS.get(cat, ()))) for cat in CONTENT_PROFILES}


def _build_weak_negatives():
    return {cat: sorted(_derived_negatives(cat) & set(WEAK_TERMS.get(cat, ()))) for cat in CONTENT_PROFILES}


NEGATIVE_KEYWORDS = _build_negative_keywords()
WEAK_NEGATIVE_KEYWORDS = _build_weak_negatives()

# Torznab category ranges per content type (1000 console, 2000 movies, 3000
# audio, 4000 PC, 5000 TV, 7000 books). 6000 (XXX), 8000 (other) and the
# tracker-specific ids above 100000 say nothing about the content type.
TORZNAB_GROUPS = {
    "video": (2000, 5000),
    "games": (1000, 4000),
    "software": (4000,),
    "books": (7000,),
    "music": (3000,),
}


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


def get_state_dir():
    if "HELM_STATE_DIR" in os.environ:
        return os.environ["HELM_STATE_DIR"]
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/helm/state")
    return os.path.dirname(get_log_dir())


def get_dl_dir():
    if "HELM_DL_DIR" in os.environ:
        return os.environ["HELM_DL_DIR"]
    if sys.platform == "win32":
        return os.path.expanduser("~\\Downloads\\helm")
    else:
        return os.path.expanduser("~/Downloads/helm")


def worker_count(value=None):
    value = os.environ.get("HELM_LITE_WORKERS", "6") if value is None else value
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Lite worker count must be an integer from 1 to 12") from error
    if not 1 <= count <= 12:
        raise ValueError("Lite worker count must be an integer from 1 to 12")
    return count


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
