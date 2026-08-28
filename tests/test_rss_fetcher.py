import helm.core.rss_fetcher as rf
from helm.core.rss_fetcher import TorrentItem, _parse_feed, search_jackett

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
