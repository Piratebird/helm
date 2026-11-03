import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from helm.search.torrent_filter import dedupe, filter_items


class MockItem:
    def __init__(self, title, link="", seeders=0):
        self.title = title
        self.link = link
        self.seeders = seeders


def test_filter_items_basic():
    items = [
        MockItem("Movie 2024 1080p Bluray", seeders=100),
        MockItem("Movie 2024 720p HDCAM", seeders=50),
        MockItem("Movie 2024 4K REMUX", seeders=10),
        MockItem("Random File", seeders=5),
    ]

    cat_profiles = {"video": ["1080p", "bluray", "4k", "remux"]}
    negatives = ["cam", "hdcam"]

    filtered = filter_items(items, cat_profiles, negatives, min_score=1)

    assert len(filtered) == 2
    assert filtered[0].title == "Movie 2024 1080p Bluray"
    assert filtered[0].score == 2
    assert filtered[0].media_type == "video"

    assert filtered[1].title == "Movie 2024 4K REMUX"
    assert filtered[1].score == 2
    assert filtered[1].media_type == "video"


def test_filter_items_multi_category():
    items = [
        MockItem("Game.Name.2024.REPACK", seeders=100),
        MockItem("Movie 2024 1080p", seeders=50),
        MockItem("Random Generic File", seeders=5),
    ]

    cat_profiles = {"video": ["1080p"], "games": ["repack"]}

    filtered = filter_items(items, cat_profiles, negatives=[], min_score=0)

    assert len(filtered) == 3
    # They should be sorted by score descending, then original order
    # Both Game and Movie have score=1
    assert filtered[0].media_type in ("games", "video")
    assert filtered[2].title == "Random Generic File"
    assert filtered[2].score == 0
    assert getattr(filtered[2], "media_type", None) is None


def test_dedupe():
    items = [
        MockItem("A", link="magnet:?xt=urn:btih:1234567890abcdef1234567890abcdef12345678&dn=A"),
        MockItem("B", link="magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678&dn=B"),
        MockItem("C", link="magnet:?xt=urn:btih:0987654321fedcba0987654321fedcba09876543&dn=C"),
    ]
    unique = dedupe(items)
    assert len(unique) == 2


def test_filter_items_keeps_unknown_seeders():
    items = [
        MockItem("Movie 1080p", seeders=-1),
        MockItem("Movie 720p", seeders=0),
        MockItem("Movie 1080p", seeders=2),
    ]
    results = filter_items(items, {"video": ["1080p", "720p"]}, [], min_score=0, min_seeds=3)
    assert len(results) == 1
    assert results[0].title == "Movie 1080p"
    assert results[0].seeders == -1


from helm.search.rss_fetcher import TorrentItem  # noqa: E402
from helm.search.torrent_filter import item_hash, normalize_hash  # noqa: E402

HASH = "c9e15763f722f23e98a29decdfae341b98d53056"
BASE32 = "ZHQVOY7XELZD5GFCTXWN7LRUDOMNKMCW"  # the same hash in base32


def test_normalize_hash_accepts_hex_and_base32():
    assert normalize_hash(HASH.upper()) == HASH
    assert normalize_hash(BASE32) == HASH
    for bad in (None, "", "xyz", HASH[:-1], 123):
        assert normalize_hash(bad) is None


def test_item_hash_reads_field_magnet_and_torrent_url():
    assert item_hash(TorrentItem("a", "http://jackett/dl/x?path=abc", 1, info_hash=HASH.upper())) == HASH
    assert item_hash(TorrentItem("a", f"magnet:?xt=urn:btih:{BASE32}&dn=a", 1)) == HASH
    assert item_hash(TorrentItem("a", f"https://itorrents.org/torrent/{HASH.upper()}.torrent", 1)) == HASH
    assert item_hash(TorrentItem("a", "http://localhost:19117/dl/tpb/?jackett_apikey=k&path=Q2ZESjhBRk", 1)) is None


def test_dedupe_merges_jackett_link_with_lite_magnet():
    jackett = TorrentItem(
        "Movie.2019.1080p", "http://localhost:19117/dl/tpb/?path=x", 418, 84, 3_600_000_000, None, "Jackett (TPB)", HASH
    )
    apibay = TorrentItem("Movie 2019 1080p", f"magnet:?xt=urn:btih:{HASH}", 400, 80, 0, "2019-10-11", "Apibay")
    (merged,) = dedupe([jackett, apibay])
    assert merged.seeders == 418 and merged.leechers == 84  # best-seeded copy wins
    assert merged.link.startswith("magnet:")  # works without Jackett running
    assert merged.pubdate == "2019-10-11" and merged.size == 3_600_000_000  # filled in from the other copy
    assert merged.sources == ["Jackett (TPB)", "Apibay"]
    assert merged.indexer == "Jackett (TPB) +1"
    assert jackett.link.startswith("http") and jackett.sources is None  # inputs are not changed


def test_dedupe_falls_back_to_normalized_title_and_size():
    a = TorrentItem("Spider-Man.Far.From.Home.2019", "http://a/1", 5, size=100, indexer="A")
    b = TorrentItem("spider man far from home 2019", "http://b/2", 9, size=100, indexer="B")
    c = TorrentItem("Spider-Man.Far.From.Home.2019", "http://c/3", 1, size=200, indexer="C")
    d = TorrentItem("No size", "http://d/4", 1, indexer="D")
    e = TorrentItem("No size", "http://e/5", 1, indexer="E")
    unique = dedupe([a, b, c, d, e])
    assert [i.indexer for i in unique] == ["B +1", "C", "D", "E"]


def test_dedupe_is_stable_when_run_twice():
    items = [
        TorrentItem("x", f"magnet:?xt=urn:btih:{HASH}", s, indexer=name)
        for s, name in ((3, "Apibay"), (7, "YTS"), (5, "Nyaa"))
    ]
    once = dedupe(items)
    twice = dedupe(once + [TorrentItem("x", f"magnet:?xt=urn:btih:{HASH}", 1, indexer="Apibay")])
    assert once[0].indexer == "YTS +2" and once[0].sources == ["YTS", "Apibay", "Nyaa"]
    assert twice[0].indexer == "YTS +2" and twice[0].sources == ["YTS", "Apibay", "Nyaa"]


def test_torrent_item_round_trips_through_worker_json():
    import json

    item = TorrentItem("x", "http://l", 1, info_hash=HASH, sources=["A", "B"])
    assert vars(TorrentItem(**json.loads(json.dumps(vars(item))))) == vars(item)


def test_dedupe_joins_a_rounded_size_copy_to_the_hashed_one():
    exact = TorrentItem(
        "Movie.2026.1080p.WEBRip-Group", f"magnet:?xt=urn:btih:{HASH}", 3498, size=4_512_345_678, indexer="Apibay"
    )
    rounded = TorrentItem(
        "Movie 2026 1080p WEBRip Group",
        "https://lime.test/movie.html",
        4174,
        size=4_509_715_660,
        indexer="LimeTorrents",
    )
    far = TorrentItem(
        "Movie 2026 1080p WEBRip Group", "https://lime.test/other.html", 9, size=2_000_000_000, indexer="LimeTorrents"
    )
    unique = dedupe([exact, rounded, far])
    assert len(unique) == 2
    merged = unique[0]
    assert merged.seeders == 4174 and merged.link.startswith("magnet:") and merged.indexer == "LimeTorrents +1"
    assert unique[1].size == 2_000_000_000
