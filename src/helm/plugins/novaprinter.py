import datetime
import re
import threading
from typing import List, TypedDict, Union

from helm.search.rss_fetcher import TorrentItem, source_status


class _ThreadLocalResults(threading.local):
    """Per-thread collector so a stalled plugin thread can never append results into another search."""

    def __init__(self) -> None:
        super().__init__()
        self.items: List[TorrentItem] = []
        self.errors: List[str] = []


_local_results = _ThreadLocalResults()


def get_results() -> List[TorrentItem]:
    """Return the calling thread's private result list (used by the loader)."""
    return _local_results.items


def get_errors():
    return _local_results.errors


def report_error(error):
    """Record a swallowed plugin failure for the loader's source outcome."""
    _local_results.errors.append(source_status(error))


# The result dict plugins pass to prettyPrinter, as in qBittorrent's nova3.
# total=False keeps it usable on Python 3.8, which has no NotRequired.
SearchResults = TypedDict(
    "SearchResults",
    {
        "link": str,
        "name": str,
        "size": Union[float, int, str],
        "seeds": int,
        "leech": int,
        "engine_url": str,
        "desc_link": str,
        "pub_date": int,
    },
    total=False,
)

_SIZE = re.compile(r"^(?P<size>\d*\.?\d+) *(?P<unit>[a-z]+)?", re.IGNORECASE)


def anySizeToBytes(size_string):
    """Convert "1.5 GB", "700 MiB", "1,234 MB", 123 or 1.5e9 to bytes, or -1 if unknown (qBittorrent's rules)."""
    if isinstance(size_string, bool):
        return -1
    if isinstance(size_string, int):
        return size_string
    if isinstance(size_string, float):
        return round(size_string)
    match = _SIZE.match(str(size_string).strip().replace(",", ""))
    if match is None:
        return -1
    size = float(match.group("size"))
    unit = match.group("unit")
    if unit is not None:
        size *= 2 ** {"T": 40, "G": 30, "M": 20, "K": 10}.get(unit[0].upper(), 0)
    return round(size)


def _count(value):
    """Seeders or leechers as an int; -1 when the plugin does not know."""
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value if value is not None else "").strip().replace(",", "")
    return int(text) if text.lstrip("-").isdigit() else -1


def _day(pub_date):
    """A Unix timestamp as YYYY-MM-DD in UTC, or None when missing or invalid."""
    try:
        stamp = int(pub_date)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def prettyPrinter(result_dict):
    """
    Called by qBittorrent search plugins to yield a parsed torrent result.
    It takes a dictionary and converts it into Helm's TorrentItem format.
    """
    try:
        item = TorrentItem(
            title=str(result_dict.get("name") or "Unknown"),
            link=str(result_dict.get("link") or ""),
            size=max(anySizeToBytes(result_dict.get("size", -1)), 0),
            pubdate=_day(result_dict.get("pub_date")),
            seeders=_count(result_dict.get("seeds", -1)),
            leechers=_count(result_dict.get("leech", -1)),
            desc_link=result_dict.get("desc_link") or None,
        )
        _local_results.items.append(item)
    except Exception as error:
        report_error(error)
