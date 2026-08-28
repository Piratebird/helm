import os
import sys

import responses

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins")))

import bittorrented  # noqa: E402
import novaprinter  # noqa: E402

from helm.core.lite_plugin_loader import load_plugins  # noqa: E402

BUNDLED_PLUGINS = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins"))
API_URL = "https://bittorrented.com/api/search/torrents"


def _payload():
    return {
        "results": [
            {
                "torrent_name": "Dune.2021.1080p.WEBRip",
                "torrent_infohash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "torrent_total_size": 1610612736,
                "torrent_seeders": 25,
                "torrent_leechers": 4,
                "torrent_id": 42,
            },
            {
                "torrent_name": "Bad hash row",
                "torrent_infohash": "not-a-valid-hash",
                "torrent_total_size": 100,
                "torrent_seeders": 1,
                "torrent_leechers": 0,
            },
        ]
    }


def _plugin():
    plugins = load_plugins(BUNDLED_PLUGINS)
    for plugin in plugins:
        if plugin.__class__.__name__ == "bittorrented":
            return plugin
    raise AssertionError("bittorrented plugin failed to load from bundled plugins")


def _collect():
    results = list(novaprinter.get_results())
    novaprinter.get_results().clear()
    return results


@responses.activate
def test_bittorrented_plugin_parses_api_results():
    responses.add(responses.GET, API_URL, json=_payload(), status=200)

    _plugin().search("dune")
    items = _collect()

    assert len(items) == 1
    assert items[0].title == "Dune.2021.1080p.WEBRip"
    assert "urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in items[0].link
    assert items[0].seeders == 25
    assert items[0].size == 1610612736


@responses.activate
def test_bittorrented_plugin_needs_min_query_length():
    _plugin().search("ab")
    assert _collect() == []


@responses.activate
def test_bittorrented_plugin_handles_connection_failure():
    _plugin().search("dune")
    assert _collect() == []


def test_map_results_drops_invalid_hashes():
    mapped = bittorrented.map_results(_payload()["results"])
    assert len(mapped) == 1
    assert "not-a-valid-hash" not in mapped[0]["link"]
