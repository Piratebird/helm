import os
import sys

import responses

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins")))

import novaprinter  # noqa: E402

from helm.core.lite_plugin_loader import load_plugins  # noqa: E402

BUNDLED_PLUGINS = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src/helm/plugins"))
API_URL = "https://yts.lt/api/v2/list_movies.json"


def _make_payload():
    return {
        "status": "ok",
        "data": {
            "movie_count": 2,
            "movies": [
                {
                    "title": "Dune: Part Two",
                    "year": 2024,
                    "url": "https://yts.lt/movies/dune-part-two",
                    "torrents": [
                        {
                            "quality": "1080p",
                            "type": "web",
                            "hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                            "seeds": 120,
                            "peers": 30,
                            "size": "1.5 GB",
                            "size_bytes": 1610612736,
                            "date_uploaded_unix": 1700000000,
                        },
                        {
                            "quality": "2160p",
                            "type": "bluray",
                            "hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                            "seeds": 4,
                            "peers": 1,
                            "size": "12.1 GB",
                            "size_bytes": None,
                            "date_uploaded_unix": 1700000001,
                        },
                    ],
                },
                {"title": "Dune", "year": 2021, "url": None, "torrents": []},
            ],
        },
    }


def _yts_plugin():
    plugins = load_plugins(BUNDLED_PLUGINS)
    for plugin in plugins:
        if plugin.__class__.__name__ == "yts":
            return plugin
    raise AssertionError("yts plugin failed to load from bundled plugins")


def _collect():
    results = list(novaprinter.get_results())
    novaprinter.get_results().clear()
    return results


@responses.activate
def test_yts_plugin_parses_api_results():
    responses.add(responses.GET, API_URL, json=_make_payload(), status=200)

    _yts_plugin().search("dune")
    items = _collect()

    assert len(items) == 2

    web = items[0]
    assert web.title == "Dune: Part Two (2024) 1080p web"
    assert "urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in web.link
    assert web.seeders == 120
    assert web.leechers == 30
    assert web.size == 1610612736

    bluray = items[1]
    assert bluray.title == "Dune: Part Two (2024) 2160p bluray"
    assert "urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in bluray.link
    assert bluray.size == int(12.1 * 1024**3)


@responses.activate
def test_yts_plugin_ignores_bad_api_status():
    responses.add(responses.GET, API_URL, json={"status": "error", "data": None}, status=200)

    _yts_plugin().search("dune")
    assert _collect() == []


@responses.activate
def test_yts_plugin_handles_connection_failure():
    # No response registered -> requests raises ConnectionError, plugin must no-op
    _yts_plugin().search("dune")
    assert _collect() == []
