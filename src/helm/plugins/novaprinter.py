import threading
from typing import List

from helm.core.rss_fetcher import TorrentItem


class _ThreadLocalResults(threading.local):
    """Per-thread collector so a stalled plugin thread can never append results into another search."""

    def __init__(self) -> None:
        super().__init__()
        self.items: List[TorrentItem] = []


_local_results = _ThreadLocalResults()


def get_results() -> List[TorrentItem]:
    """Return the calling thread's private result list (used by the loader)."""
    return _local_results.items


def prettyPrinter(result_dict):
    """
    Called by qBittorrent search plugins to yield a parsed torrent result.
    It takes a dictionary and converts it into Helm's TorrentItem format.
    """
    try:
        # Convert size to bytes
        size_str = result_dict.get("size", "0 B")
        size_bytes = 0
        if "TB" in size_str:
            size_bytes = int(float(size_str.replace("TB", "").strip()) * 1024**4)
        elif "GB" in size_str:
            size_bytes = int(float(size_str.replace("GB", "").strip()) * 1024**3)
        elif "MB" in size_str:
            size_bytes = int(float(size_str.replace("MB", "").strip()) * 1024**2)
        elif "KB" in size_str:
            size_bytes = int(float(size_str.replace("KB", "").strip()) * 1024)
        else:
            size_bytes = int(float(size_str.replace("B", "").strip()))

        seeders = result_dict.get("seeds", -1)
        if isinstance(seeders, str):
            seeders = int(seeders) if seeders.isdigit() else -1

        leechers = result_dict.get("leech", -1)
        if isinstance(leechers, str):
            leechers = int(leechers) if leechers.isdigit() else -1

        item = TorrentItem(
            title=result_dict.get("name", "Unknown"),
            link=result_dict.get("link", ""),
            size=size_bytes,
            pubdate="Unknown",
            seeders=seeders,
            leechers=leechers,
        )
        _local_results.items.append(item)
    except Exception:
        pass
