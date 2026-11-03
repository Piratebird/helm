import responses

import helm.search.rss_fetcher as rf
from helm.search.rss_fetcher import TorrentItem, _parse_feed, search_jackett

TORZNAB_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Ubuntu 24.04 1080p x264</title>
      <link>http://localhost:9117/dl/ubuntu</link>
      <pubDate>Sun, 08 Dec 2024 16:53:15 +0000</pubDate>
      <size>734003200</size>
      <torznab:attr name="seeders" value="150" />
      <torznab:attr name="peers" value="160" />
    </item>
  </channel>
</rss>
"""


def test_parse_feed_extracts_fields():
    items = _parse_feed(TORZNAB_FEED)
    assert len(items) == 1
    item = items[0]
    assert item.title == "Ubuntu 24.04 1080p x264"
    assert item.seeders == 150
    assert item.leechers == 10  # peers - seeders
    assert item.size == 734003200
    assert item.pubdate == "2024-12-08"


def test_search_jackett_dedupes_across_indexers(monkeypatch):
    monkeypatch.setenv("JACKETT_API_KEY", "testkey")
    monkeypatch.setenv("JACKETT_URL", "http://localhost:9117")
    monkeypatch.setattr(rf, "_get_configured_indexers", lambda url, key: ["yts", "1337x"])

    dup = TorrentItem("Ubuntu 24.04 1080p", "magnet:?xt=urn:btih:1", 10, 5, 1000)
    uniq = TorrentItem("Ubuntu 24.04 Software", "magnet:?xt=urn:btih:2", 20, 5, 2000)

    def fake_search(url, key, indexer_id, query, cat):
        return [dup, dup, uniq] if indexer_id == "1337x" else [dup]

    monkeypatch.setattr(rf, "_search_indexer", fake_search)

    items = search_jackett("ubuntu", "video")

    # Duplicate (title, size) collapses regardless of which indexer returned it
    assert len(items) == 2
    assert {(i.title, i.size) for i in items} == {("Ubuntu 24.04 1080p", 1000), ("Ubuntu 24.04 Software", 2000)}


def test_search_jackett_aggregate_fallback_when_no_indexers_configured(monkeypatch):
    monkeypatch.setenv("JACKETT_API_KEY", "testkey")
    monkeypatch.setenv("JACKETT_URL", "http://localhost:9117")
    monkeypatch.setattr(rf, "_get_configured_indexers", lambda url, key: [])

    agg = TorrentItem("Ubuntu 24.04 1080p", "magnet:?xt=urn:btih:3", 9)
    monkeypatch.setattr(rf, "_fetch_aggregate", lambda url, key, query, cat: [agg])

    items = search_jackett("ubuntu", "video")
    assert len(items) == 1
    assert items[0] is agg


def test_search_jackett_returns_empty_without_api_key(monkeypatch):
    monkeypatch.delenv("JACKETT_API_KEY", raising=False)
    assert search_jackett("ubuntu", "video") == []


def test_search_indexers_falls_back_to_lite_when_manager_returns_nothing(monkeypatch):
    from helm.config import settings
    from helm.search import lite_fetcher
    from helm.search.rss_fetcher import TorrentItem

    monkeypatch.setattr(
        settings, "load_settings", lambda: settings.Settings.from_legacy({"INDEXER_MANAGER": "jackett"})
    )
    monkeypatch.setattr(rf, "search_jackett", lambda *a, **k: [])

    lite = TorrentItem("Ubuntu 24.04 Lite", "magnet:?xt=urn:btih:cafe", 5)
    monkeypatch.setattr(lite_fetcher, "search_lite", lambda query, content_type: [lite])

    # Must NOT raise ImportError despite the async search_all_plugins wrapper.
    items = rf.search_indexers("ubuntu", "video")

    assert items == [lite]


def test_search_indexers_falls_back_to_lite_with_prowlarr_manager(monkeypatch):
    from helm.config import settings
    from helm.search import lite_fetcher
    from helm.search.rss_fetcher import TorrentItem

    monkeypatch.setattr(
        settings, "load_settings", lambda: settings.Settings.from_legacy({"INDEXER_MANAGER": "prowlarr"})
    )
    monkeypatch.setattr(rf, "search_prowlarr", lambda *a, **k: [])
    monkeypatch.setattr(rf, "search_jackett", lambda *a, **k: [])

    lite = TorrentItem("Ubuntu 24.04 Lite", "magnet:?xt=urn:btih:cafe", 5)
    monkeypatch.setattr(lite_fetcher, "search_lite", lambda query, content_type: [lite])

    ret = rf.search_indexers("ubuntu", "video")

    # Same list, deduped (no duplicates here).
    assert ret == [lite]


def test_iso_day_reads_jackett_and_prowlarr_timestamps():
    assert rf.iso_day("2019-07-02T12:34:56") == "2019-07-02"
    assert rf.iso_day("2021-03-04T05:06:07.1234567+01:00") == "2021-03-04"  # Jackett: 7 fractional digits
    assert rf.iso_day("2024-12-08T16:53:15Z") == "2024-12-08"  # Prowlarr
    assert rf.iso_day("0001-01-01T00:00:00") is None  # Jackett's "no date"
    for missing in (None, "", "not a date", 20240101):
        assert rf.iso_day(missing) is None


@responses.activate
def test_jackett_json_results_keep_their_dates(monkeypatch):
    monkeypatch.setenv("JACKETT_URL", "http://jackett.test")
    monkeypatch.setattr(rf, "get_secret", lambda key: "key")
    results = [
        {"Title": "Dated", "Size": 1, "Seeders": 5, "Peers": 1, "Tracker": "YTS",
         "MagnetUri": "magnet:?xt=urn:btih:" + "a" * 40, "PublishDate": "2019-07-02T12:34:56.1234567+00:00"},
        {"Title": "Undated", "Size": 2, "Seeders": 3, "Peers": 1, "Tracker": "TPB",
         "MagnetUri": "magnet:?xt=urn:btih:" + "b" * 40, "PublishDate": "0001-01-01T00:00:00"},
    ]  # fmt: skip
    responses.add(responses.GET, "http://jackett.test/api/v2.0/indexers/all/results", json={"Results": results})
    items = {item.title: item.pubdate for item in search_jackett("spider-man")}
    assert items == {"Dated": "2019-07-02", "Undated": None}


@responses.activate
def test_prowlarr_json_results_keep_their_dates(monkeypatch):
    monkeypatch.setenv("PROWLARR_URL", "http://prowlarr.test")
    monkeypatch.setattr(rf, "get_secret", lambda key: "key")
    responses.add(responses.GET, "http://prowlarr.test/api/v1/indexer", json=[{"id": 1}])
    responses.add(
        responses.GET,
        "http://prowlarr.test/api/v1/search",
        json=[{"title": "Dated", "size": 1, "seeders": 2, "leechers": 0, "indexer": "YTS",
               "magnetUrl": "magnet:?xt=urn:btih:" + "c" * 40, "publishDate": "2024-12-08T16:53:15Z"}],
    )  # fmt: skip
    (item,) = rf.search_prowlarr("ubuntu")
    assert item.pubdate == "2024-12-08"
