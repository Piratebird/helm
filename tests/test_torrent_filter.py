import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from helm.core.torrent_filter import dedupe, filter_items, is_negative_match, score_item


class MockItem:
    def __init__(self, title, link="", seeders=0):
        self.title = title
        self.link = link
        self.seeders = seeders


def test_is_negative_match():
    negatives = ["cam", "ts", "hdcam"]
    assert is_negative_match("Movie Name 2024 HDCAM x264", negatives) is True
    assert is_negative_match("Movie Name 2024 1080p Bluray", negatives) is False
    # Ensure word boundaries work ('cam' is in 'Camille' but shouldn't match)
    assert is_negative_match("Camille 2008", negatives) is False


def test_score_item():
    positives = ["1080p", "bluray", "remux"]
    assert score_item("Movie 2024 1080p Bluray", positives) == 2
    assert score_item("Movie 2024 720p WEB-DL", positives) == 0
    assert score_item("Movie 2024 1080p REMUX bluray", positives) == 3


def test_dedupe():
    items = [
        MockItem(title="A", link="magnet:?xt=urn:btih:1234567890123456789012345678901234567890&dn=A"),
        # Same hash as A
        MockItem(title="B", link="magnet:?xt=urn:btih:1234567890123456789012345678901234567890&dn=B"),
        MockItem(title="C", link="magnet:?xt=urn:btih:0987654321098765432109876543210987654321&dn=C"),
    ]
    unique = dedupe(items)
    assert len(unique) == 2
    assert unique[0].title == "A"
    assert unique[1].title == "C"


def test_filter_items():
    items = [
        MockItem("Movie 1080p", seeders=10),  # Score: 1
        MockItem("Movie 720p", seeders=50),  # Score: 0 (Generic)
        MockItem("Movie 1080p HDCAM", seeders=100),  # Negative match
        MockItem("Movie 1080p", seeders=0),  # Too few seeds
    ]

    positives = ["1080p"]
    negatives = ["hdcam"]

    # Test with min_score=0, min_seeds=1
    results = filter_items(items, positives, negatives, min_score=0, min_seeds=1)

    assert len(results) == 2
    # Should be sorted by score, so "Movie 1080p" (score 1) first, then "Movie 720p" (score 0)
    assert results[0].title == "Movie 1080p"
    assert getattr(results[0], "score", None) == 1
    assert results[1].title == "Movie 720p"
    assert getattr(results[1], "score", None) == 0
