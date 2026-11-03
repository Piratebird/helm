"""Turn a plugin result's link into a torrent with the plugin's own download_torrent.

qBittorrent calls ``download_torrent(link)`` for any result whose link is not a
magnet; the plugin prints "<magnet or .torrent path> <link>". Many plugins return
a details page as the link and only know how to get the torrent from it there.
The plugin runs in a child process with a timeout, like searches do, so a
stuck or crashing plugin cannot take the UI with it.
"""

import io
import json
import os
import subprocess
import sys
import tempfile

from helm.logger import get_logger

logger = get_logger(__name__)
TIMEOUT = 60


def needs_plugin(item):
    link = str(getattr(item, "link", "") or "")
    return bool(getattr(item, "plugin", None)) and not link.lower().startswith("magnet:")


def parse_output(text):
    """The magnet or existing .torrent path from download_torrent's output, or None."""
    for line in text.splitlines():
        first = line.strip().split(" ", 1)[0]
        if first.lower().startswith("magnet:"):
            return first
        if first and os.path.isfile(first):
            if _looks_like_torrent(first):
                return first
            # download_file saved an error or details page, not torrent metadata.
            if is_plugin_temp_file(first):
                _remove(first)
    return None


def _looks_like_torrent(path):
    try:
        with open(path, "rb") as stream:
            return stream.read(1) == b"d"
    except OSError:
        return False


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def resolve(item, timeout=TIMEOUT):
    """Return a magnet or local .torrent path for *item* from its plugin, or None."""
    if not needs_plugin(item):
        return None
    from helm.services.runtime import child_environment

    request = json.dumps({"plugin": item.plugin, "link": item.link})
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "helm.search.plugin_download", "--worker"],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=child_environment(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("Event: Plugin download_torrent timed out after %ss", timeout)
        return None
    if completed.returncode != 0:
        logger.warning("Event: Plugin download_torrent failed: %s", completed.stderr.strip()[-300:])
        return None
    found = parse_output(completed.stdout)
    if found is None:
        logger.warning("Event: Plugin download_torrent printed no magnet or file")
    return found


def is_plugin_temp_file(path):
    """True for a .torrent that a plugin's download_file left in the temp directory."""
    if not path or str(path).lower().startswith("magnet:"):
        return False
    path = os.path.abspath(str(path))
    return os.path.dirname(path) == os.path.abspath(tempfile.gettempdir()) and path.endswith(".torrent")


def worker():
    from helm.search.lite_plugin_loader import load_plugin

    request = json.load(sys.stdin)
    plugin = load_plugin(request["plugin"])
    captured = io.StringIO()
    module = sys.modules[type(plugin).__module__]
    # The loader routes plugin prints to the log; download_torrent's print is its answer.
    module.print = lambda *args, **kwargs: print(*args, file=captured)  # type: ignore[attr-defined]
    plugin.download_torrent(request["link"])
    sys.stdout.write(captured.getvalue())


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("This module is an internal plugin worker")
    worker()
