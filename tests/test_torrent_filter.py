import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from helm.core.torrent_filter import dedupe, filter_items


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
