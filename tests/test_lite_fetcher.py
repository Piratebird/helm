import asyncio

import pytest
import responses

from helm.search.lite_fetcher import search_all_plugins, search_lite


@pytest.fixture(autouse=True)
def inline_sources_in_process(monkeypatch):
    # Parsing/HTTP unit tests execute synchronously; subprocess behavior has its
    # own integration tests with real workers and disposable fixture plugins.
    from helm.search import lite_plugin_loader, search_runner
    from helm.search.rss_fetcher import SearchResults

    monkeypatch.setattr(lite_plugin_loader, "plugin_jobs", lambda directories: [])

    def run(jobs, query, content_type="video", **kwargs):
        result = SearchResults()
        for job in jobs:
            result.extend(search_runner.execute_job(job, query, content_type))
        return result

    monkeypatch.setattr(search_runner, "run_searches", run)


NYAA_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:torrent="http://nyaa.si/xmlns/nyaa" version="2.0">
  <channel>
    <item>
      <title>Ubuntu 24.04 Anime Edition</title>
      <pubDate>Sun, 08 Dec 2024 16:53:15 +0000</pubDate>
      <torrent:infoHash>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</torrent:infoHash>
      <torrent:seeders>20</torrent:seeders>
      <torrent:leechers>3</torrent:leechers>
      <torrent:size>1.5 GiB</torrent:size>
    </item>
  </channel>
</rss>
"""

APITBAY_ITEM = {
    "id": "1",
    "name": "Ubuntu 24.04 ISO",
    "info_hash": "cafecafecafecafecafecafecafecafecafecafe",
    "seeders": "100",
    "leechers": "5",
    "size": "1234567",
    "added": "1700000000",
}

CSV_ITEM = {
    "name": "Ubuntu 24.04 Torrent",
    "infohash": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    "seeders": "10",
    "leechers": "2",
    "size_bytes": "2000000",
    "created_unix": 1700000000,
}


@responses.activate
def test_search_lite_aggregates_all_sources(monkeypatch):
    monkeypatch.setattr("helm.search.lite_plugin_loader.run_plugins", lambda query, dirs: [])

    responses.add(
        responses.GET,
        "https://apibay.org/q.php",
        match=[responses.matchers.query_param_matcher({"q": "ubuntu"})],
        json=[APITBAY_ITEM],
    )
    responses.add(
        responses.GET,
        "https://torrents-csv.com/service/search",
        match=[responses.matchers.query_param_matcher({"q": "ubuntu", "size": 100})],
        json={"torrents": [CSV_ITEM]},
    )
    responses.add(responses.GET, "https://nyaa.si/?page=rss&q=ubuntu&c=0_0&f=0", body=NYAA_RSS)

    items = search_lite("ubuntu")

    assert len(items) == 3

    by_title = {i.title: i for i in items}
    apibay = by_title["Ubuntu 24.04 ISO"]
    assert apibay.seeders == 100
    assert apibay.leechers == 5
    assert apibay.size == 1234567
    assert apibay.pubdate is not None
    assert "btih:cafecafecafecafecafecafecafecafecafecafe" in apibay.link

    torrents_csv = by_title["Ubuntu 24.04 Torrent"]
    assert torrents_csv.size == 2000000
    assert "btih:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef" in torrents_csv.link

    nyaa = by_title["Ubuntu 24.04 Anime Edition"]
    assert nyaa.seeders == 20
    assert nyaa.leechers == 3
    assert nyaa.size == int(1.5 * 1024**3)
    assert nyaa.pubdate == "2024-12-08"


def test_search_all_plugins_delegates_to_search_lite(monkeypatch):
    from helm.search.rss_fetcher import TorrentItem

    item = TorrentItem("Ubuntu 24.04 ISO", "magnet:?xt=urn:btih:cafe", 42, 7, 999)
    monkeypatch.setattr("helm.search.lite_fetcher.search_lite", lambda query, content_type: [item])

    result = asyncio.run(search_all_plugins("ubuntu", "video"))

    assert result == [item]


def test_search_lite_rejects_empty_queries_without_network(monkeypatch):
    def unexpected_search(query, content_type):
        raise AssertionError("Blank queries must not reach sources")

    monkeypatch.setattr("helm.search.lite_fetcher._collect_all", unexpected_search)
    assert search_lite("  \t\n") == []


def test_search_lite_trims_query(monkeypatch):
    queries = []
    monkeypatch.setattr(
        "helm.search.lite_fetcher._collect_all", lambda query, content_type: queries.append(query) or []
    )
    assert search_lite("  ubuntu  ") == []
    assert queries == ["ubuntu"]


@responses.activate
def test_nyaa_timeout_preserves_other_source_results(monkeypatch):
    import requests

    monkeypatch.setattr("helm.search.lite_plugin_loader.run_plugins", lambda query, dirs: [])
    responses.add(responses.GET, "https://apibay.org/q.php", json=[APITBAY_ITEM])
    responses.add(responses.GET, "https://torrents-csv.com/service/search", json={"torrents": [CSV_ITEM]})
    responses.add(responses.GET, "https://nyaa.si/", body=requests.exceptions.ConnectTimeout("Nyaa timed out"))

    assert {item.indexer for item in search_lite("ubuntu")} == {"Apibay", "Torrents-csv"}


def test_search_uses_one_budget_for_inline_and_plugin_jobs(monkeypatch):
    from helm.search import lite_plugin_loader, search_runner

    jobs_seen = []
    plugin = search_runner.SearchJob("Fixture", "plugin", "fixture.py")
    monkeypatch.setattr(lite_plugin_loader, "plugin_jobs", lambda directories: [plugin])

    def run(jobs, query, content_type, timeout):
        jobs_seen.extend(jobs)
        assert query == "ubuntu"
        assert content_type == "music"
        assert timeout == lite_plugin_loader.PLUGIN_SEARCH_TIMEOUT
        return []

    monkeypatch.setattr(search_runner, "run_searches", run)
    assert search_lite("ubuntu", "music") == []
    assert [job.kind for job in jobs_seen] == ["inline", "inline", "inline", "plugin"]


@responses.activate
def test_search_outcomes_distinguish_source_failures(monkeypatch):
    import requests

    monkeypatch.setattr("helm.search.lite_plugin_loader.run_plugins", lambda query, dirs: [])
    responses.add(responses.GET, "https://apibay.org/q.php", json=[])
    responses.add(responses.GET, "https://torrents-csv.com/service/search", status=503)
    responses.add(responses.GET, "https://nyaa.si/", body=requests.exceptions.ConnectTimeout("private URL"))
    results = search_lite("ubuntu")
    assert results == []
    assert {o.source: o.status for o in results.outcomes} == {
        "Apibay": "empty",
        "Torrents-csv": "http_error",
        "Nyaa": "timeout",
    }


@responses.activate
def test_parse_failure_keeps_successful_sources_and_counts(monkeypatch):
    monkeypatch.setattr("helm.search.lite_plugin_loader.run_plugins", lambda query, dirs: [])
    responses.add(responses.GET, "https://apibay.org/q.php", json=[APITBAY_ITEM])
    responses.add(responses.GET, "https://torrents-csv.com/service/search", body="not-json")
    responses.add(responses.GET, "https://nyaa.si/", body="broken XML")
    results = search_lite("ubuntu")
    assert len(results) == 1
    assert {o.source: (o.status, o.count) for o in results.outcomes} == {
        "Apibay": ("success", 1),
        "Torrents-csv": ("parse_error", 0),
        "Nyaa": ("parse_error", 0),
    }


def test_search_outcomes_do_not_leak_between_calls(monkeypatch):
    from helm.search import lite_fetcher
    from helm.search.rss_fetcher import SearchResults, SourceOutcome

    monkeypatch.setattr(
        lite_fetcher, "_fetch_apibay", lambda query: SearchResults([], [SourceOutcome("Apibay", query)])
    )
    for name in ("_fetch_torrents_csv", "_fetch_nyaa"):
        monkeypatch.setattr(lite_fetcher, name, lambda query: [])
    first = search_lite("first")
    second = search_lite("second")
    assert first.outcomes[0].status == "first"
    assert second.outcomes[0].status == "second"
