import os
import re
import sys

import responses

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins")))

import novaprinter  # noqa: E402

from helm.core.lite_plugin_loader import load_plugins  # noqa: E402

BUNDLED_PLUGINS = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins"))
API_URL = "https://eztvx.to/api/get-torrents"


def _payload():
    return {
        "torrents_count": 2,
        "limit": 100,
        "page": 1,
        "torrents": [
            {
                "id": 1,
                "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "imdb_id": "tt1234",
                "title": "One Piece - S22E1089",
                "filename": "One.Piece.S22E1089.1080p.WEB.h264",
                "season": "22",
                "episode": "1089",
                "seeds": 45,
                "peers": 3,
                "size_bytes": 734003200,
                "magnet_url": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&dn=One+Piece",
                "date_released_unix": 1700000000,
            }
        ],
    }


def _plugin():
    plugins = load_plugins(BUNDLED_PLUGINS)
    for plugin in plugins:
        if plugin.__class__.__name__ == "eztv":
            return plugin
    raise AssertionError("eztv plugin failed to load from bundled plugins")


def _collect():
    results = list(novaprinter.get_results())
    novaprinter.get_results().clear()
    return results


@responses.activate
def test_eztv_plugin_filters_latest_by_query():
    responses.add(responses.GET, API_URL, json=_payload(), status=200)

    _plugin().search("one piece")
    items = _collect()

    assert len(items) == 1
    assert items[0].title == "One Piece - S22E1089"
    assert "urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in items[0].link
    assert items[0].seeders == 45
    assert items[0].size == 734003200


@responses.activate
def test_eztv_plugin_skips_non_matching_latest():
    responses.add(responses.GET, API_URL, json=_payload(), status=200)

    _plugin().search("the boys")
    assert _collect() == []


@responses.activate
def test_eztv_plugin_handles_connection_failure():
    _plugin().search("one piece")
    assert _collect() == []


def test_eztv_title_regex_is_sane():
    title = "One Piece - S22E1089"
    assert re.search(r"S\d+E\d+", title) is not None
