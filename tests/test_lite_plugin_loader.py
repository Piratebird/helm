import os
import shutil
import sys
import tempfile
import threading

import pytest

# Ensure src is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins")))

import novaprinter  # noqa: E402

from helm.core.lite_plugin_loader import run_plugins  # noqa: E402

# A dummy plugin script mimicking a qBittorrent plugin
DUMMY_PLUGIN_CODE = """
from novaprinter import prettyPrinter
from helpers import download_file, retrieve_url

class dummy_plugin(object):
    name = "DummyPlugin"
    url = "https://dummy.com"

    def search(self, what, cat='all'):
        # Ensure helpers are loaded properly
        assert retrieve_url is not None
        assert download_file is not None

        # Return fake torrent data
        prettyPrinter({
            'link': 'magnet:?xt=urn:btih:12345&dn=Test',
            'name': 'Dummy Torrent (' + what + ')',
            'size': '1.5 GB',
            'seeds': 1337,
            'leech': 42,
            'engine_url': self.url
        })
"""


@pytest.fixture
def plugin_dir():
    # Create a temporary directory for the dummy plugin
    temp_dir = tempfile.mkdtemp()
    plugin_path = os.path.join(temp_dir, "dummy_plugin.py")

    with open(plugin_path, "w") as f:
        f.write(DUMMY_PLUGIN_CODE)

    yield temp_dir

    # Clean up after tests
    shutil.rmtree(temp_dir)


def test_plugin_loader_parses_results(plugin_dir):
    # Run the plugins using our dummy directory
    results = run_plugins("ubuntu", [plugin_dir])

    assert len(results) == 1

    item = results[0]
    assert item.title == "Dummy Torrent (ubuntu)"
    assert item.link == "magnet:?xt=urn:btih:12345&dn=Test"
    assert item.seeders == 1337
    assert item.leechers == 42

    # 1.5 GB should be correctly converted to bytes by the mock
    expected_bytes = int(1.5 * 1024**3)
    assert item.size == expected_bytes


def test_plugin_loader_handles_empty_dir():
    temp_dir = tempfile.mkdtemp()
    try:
        results = run_plugins("test", [temp_dir])
        assert len(results) == 0
    finally:
        shutil.rmtree(temp_dir)


def test_plugin_results_are_thread_local():
    # Two plugins running concurrently each see only the items they emit; a
    # stalled plugin can never append into another thread's (or search's) results.
    collected = {}

    def worker(tag):
        novaprinter.prettyPrinter(
            {"name": tag, "link": "magnet:?xt=urn:btih:1111", "size": "1 MB", "seeds": 1, "leech": 0}
        )
        collected[tag] = [item.title for item in novaprinter.get_results()]

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert collected["a"] == ["a"]
    assert collected["b"] == ["b"]


def test_run_plugins_does_not_leak_between_runs(plugin_dir):
    # Repeated searches must not accumulate results (regression test for the
    # shared-global-list race that polluted later queries).
    first = run_plugins("ubuntu", [plugin_dir])
    second = run_plugins("ubuntu", [plugin_dir])
    assert len(first) == 1
    assert len(second) == 1
