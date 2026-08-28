import os
import sys

import responses

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins")))

import novaprinter  # noqa: E402
import subsplease  # noqa: E402

from helm.core.lite_plugin_loader import load_plugins  # noqa: E402

BUNDLED_PLUGINS = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins"))
API_URL = "https://subsplease.org/api/"

BASE32_HASH = "C2LQTASUYP3G6DFXQVH4VFAE4MEVK3GX"
MAGNET_480 = f"magnet:?xt=urn:btih:{BASE32_HASH}&dn=x"
MAGNET_1080 = "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb&xl=1610612736&dn=y"


def _payload():
    return {
        "show": {
            "show": "One Piece",
            "episode": "1089",
            "page": "https://subsplease.org/shows/one-piece/",
            "downloads": [
                {"res": "480", "magnet": MAGNET_480},
                {"res": "1080", "magnet": MAGNET_1080},
            ],
        }
    }


def _plugin():
    plugins = load_plugins(BUNDLED_PLUGINS)
    for plugin in plugins:
        if plugin.__class__.__name__ == "subsplease":
            return plugin
    raise AssertionError("subsplease plugin failed to load from bundled plugins")


def _collect():
    results = list(novaprinter.get_results())
    novaprinter.get_results().clear()
    return results


@responses.activate
def test_subsplease_plugin_parses_api_results():
    responses.add(responses.GET, API_URL, json=_payload(), status=200)

    _plugin().search("one piece")
    items = _collect()

    assert len(items) == 1
    assert items[0].title == "One Piece - 1089 [1080p]"
    assert "urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in items[0].link
    assert items[0].size == 1610612736


@responses.activate
def test_subsplease_plugin_handles_empty_results():
    responses.add(responses.GET, API_URL, json={}, status=200)

    _plugin().search("the boys")
    assert _collect() == []


@responses.activate
def test_subsplease_plugin_handles_connection_failure():
    _plugin().search("one piece")
    assert _collect() == []


def test_parse_magnet_accepts_base32_and_hex():
    base32 = subsplease.parse_magnet(MAGNET_480)
    assert base32 is not None and base32[0] == BASE32_HASH.lower() and base32[1] == 0

    hex_hash, size = subsplease.parse_magnet(MAGNET_1080)
    assert hex_hash == "b" * 40
    assert size == 1610612736


def test_parse_magnet_rejects_garbage():
    assert subsplease.parse_magnet("http://not-a-magnet") is None
