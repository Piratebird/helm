import responses

from helm.core.lite_fetcher import search_lite

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
    monkeypatch.setattr("helm.core.lite_plugin_loader.run_plugins", lambda query, dirs: [])

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
