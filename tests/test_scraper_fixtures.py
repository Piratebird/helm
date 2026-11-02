"""Offline response fixtures for every bundled HTML scraper and plugin errors."""

import sys
from pathlib import Path

import pytest
import requests
import responses

from helm.search.lite_plugin_loader import _execute_plugin, load_plugin, plugin_categories

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "helm" / "plugins"
sys.path.insert(0, str(PLUGIN_DIR))

HTML_FIXTURES = {
    "bitsearch": """<div class="bg-white rounded-lg shadow-sm"><h3><a href="/torrent/1">Generated fixture</a></h3>
<a href="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">Download</a>
<i class="fa-hdd"></i><span>1.5 GB</span><i class="fa-arrow-up"></i><span>12</span>
<i class="fa-arrow-down"></i><span>3</span></div>""",
    "limetorrents": """<table class="table2"><tr bgcolor="#F4F4F4"><td><a href="/fixture.html">Generated fixture</a></td>
<td>2 days</td><td>1.5 GB</td><td>12</td><td>3</td></tr></table>""",
    "torlock": """<article><table><tr><td><a href="/torrent/123/fixture.html">Generated fixture</a></td>
<td class="td">Today</td><td class="ts">1.5 GB</td><td class="tul">12</td><td class="tdl">3</td></tr></table></article>""",
    "torrentproject": """<div id="similarfiles"><div><span><a href="/fixture">Generated fixture</a></span>
<span>verified</span><span>12</span><span>3</span><span>2026-01-01 00:00:00</span><span>1.5 GB</span></div></div>""",
}


@pytest.mark.parametrize("name", sorted(HTML_FIXTURES))
@responses.activate
def test_html_scraper_fixture_roundtrip(name):
    plugin = load_plugin(str(PLUGIN_DIR / (name + ".py")))
    responses.add(responses.GET, __import__("re").compile("https://.*"), body=HTML_FIXTURES[name])
    result = _execute_plugin(plugin, "generated", "all")
    assert len(result) == 1
    assert result[0].title == "Generated fixture"
    assert result[0].seeders == 12
    assert result[0].leechers == 3
    assert result[0].size == int(1.5 * 1024**3)
    assert result.outcomes[0].status == "success"


@pytest.mark.parametrize("name", sorted(HTML_FIXTURES))
@responses.activate
def test_html_scraper_distinguishes_blocked_or_changed_page(name):
    plugin = load_plugin(str(PLUGIN_DIR / (name + ".py")))
    responses.add(responses.GET, __import__("re").compile("https://.*"), body="<html>Just a moment...</html>")
    result = _execute_plugin(plugin, "generated", "all")
    assert result == []
    assert result.outcomes[0].status == "parse_error"


@pytest.mark.parametrize(
    "name",
    ["bitsearch", "limetorrents", "torlock", "torrentproject", "eztv", "subsplease", "yts", "bittorrented", "x1337"],
)
@pytest.mark.parametrize(
    "failure,status", [(requests.exceptions.ConnectTimeout("unreachable"), "timeout"), (None, "http_error")]
)
@responses.activate
def test_bundled_plugins_report_swallowed_http_failures(name, failure, status):
    plugin = load_plugin(str(PLUGIN_DIR / (name + ".py")))
    responses.add(responses.GET, __import__("re").compile("https://.*"), body=failure or "forbidden", status=403)
    result = _execute_plugin(plugin, "generated", "all")
    assert result == []
    assert result.outcomes[0].status == status


@pytest.mark.parametrize("name", ["eztv", "subsplease", "yts", "bittorrented"])
@responses.activate
def test_api_plugins_report_invalid_json(name):
    plugin = load_plugin(str(PLUGIN_DIR / (name + ".py")))
    responses.add(responses.GET, __import__("re").compile("https://.*"), body="broken JSON")
    result = _execute_plugin(plugin, "generated", "all")
    assert result == []
    assert result.outcomes[0].status == "parse_error"


@responses.activate
def test_piratebay_fixture_and_http_error(monkeypatch):
    import io

    plugin = load_plugin(str(PLUGIN_DIR / "piratebay.py"))
    payload = b'[{"id":"1","added":"1700000000","name":"Generated fixture","info_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","seeders":"12","leechers":"3","size":"123"}]'

    class Response(io.BytesIO):
        def getheader(self, name, default):
            return "application/json; charset=utf-8"

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: Response(payload))
    result = _execute_plugin(plugin, "generated", "music")
    assert len(result) == 1 and result[0].seeders == 12
    import urllib.error

    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fail)
    result = _execute_plugin(plugin, "generated", "music")
    assert result == [] and result.outcomes[0].status == "http_error"


@pytest.mark.parametrize(
    "content,expected",
    [
        ("video", ["movies", "tv", "anime"]),
        ("music", ["music"]),
        ("video,music", ["movies", "tv", "anime", "music"]),
        ("books", ["all"]),
    ],
)
def test_category_mapping_for_supported_plugin(content, expected):
    plugin = load_plugin(str(PLUGIN_DIR / "limetorrents.py"))
    assert plugin_categories(plugin, content) == expected


def test_loader_passes_category_keys_and_avoids_duplicate_all():
    calls = []

    class Plugin:
        supported_categories = {"all": "0", "music": "100", "movies": "200", "tv": "500"}

        def search(self, query, cat="all"):
            calls.append(cat)

    _execute_plugin(Plugin(), "generated", "video,music")
    assert calls == ["movies", "tv", "music"]
    calls.clear()
    _execute_plugin(Plugin(), "generated", "books,music")
    assert calls == ["all"]


@responses.activate
def test_unknown_subsplease_seeders_survive_filter():
    from helm.search.torrent_filter import filter_items

    plugin = load_plugin(str(PLUGIN_DIR / "subsplease.py"))
    responses.add(
        responses.GET,
        "https://subsplease.org/api/",
        json={
            "show": {"show": "Generated", "downloads": [{"res": "1080", "magnet": "magnet:?xt=urn:btih:" + "a" * 40}]}
        },
    )
    result = _execute_plugin(plugin, "generated")
    assert result[0].seeders == -1
    assert filter_items(result, {"video": []}, [], min_seeds=3) == result


def test_http_sessions_are_reused_per_thread_but_not_shared():
    import threading

    from helm.net.http import get_shared_session

    main = get_shared_session()
    assert main is get_shared_session()
    others = []
    thread = threading.Thread(target=lambda: others.append(get_shared_session()))
    thread.start()
    thread.join()
    assert others[0] is not main
