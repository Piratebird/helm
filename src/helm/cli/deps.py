"""Functions the commands call through this module, so tests replace them in one place.

Heavy modules load on first call, which keeps --help and headless commands fast.
"""

import importlib

from helm.config.secret_manager import get_secret
from helm.config.settings import load_settings, remember

__all__ = [
    "add_magnet",
    "animated_search",
    "dedupe",
    "ensure_config",
    "filter_items",
    "get_secret",
    "interactive_indexer_selector",
    "interactive_selector",
    "load_settings",
    "remember",
    "wait_for_download",
]


def _lazy(module, name):
    """Defer a heavy import so --help and headless commands start quickly."""

    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    return call


add_magnet = _lazy("helm.torrents.qbittorrent_client", "add_magnet")
animated_search = _lazy("helm.ui.tui", "animated_search")
dedupe = _lazy("helm.search.torrent_filter", "dedupe")
ensure_config = _lazy("helm.config.config_wizard", "ensure_config")
filter_items = _lazy("helm.search.torrent_filter", "filter_items")
interactive_indexer_selector = _lazy("helm.ui.tui", "interactive_indexer_selector")
interactive_selector = _lazy("helm.ui.tui", "interactive_selector")
wait_for_download = _lazy("helm.stack.oneshot", "wait_for_download")
