import sys

import pytest

from helm.config.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, TORZNAB_GROUPS, WEAK_NEGATIVE_KEYWORDS
from helm.search.rss_fetcher import TorrentItem, _parse_feed
from helm.search.torrent_filter import category_groups, filter_items

VIDEO = {"video": CONTENT_PROFILES["video"]}


def run(items, min_seeds=0, groups=TORZNAB_GROUPS["video"]):
    hidden = []
    kept = filter_items(
        items,
        VIDEO,
        NEGATIVE_KEYWORDS["video"],
        min_seeds=min_seeds,
        weak_negatives=WEAK_NEGATIVE_KEYWORDS["video"],
        hidden=hidden,
        groups=groups,
    )
    return [i.title for i in kept], {i.title: reason for i, reason in hidden}


def test_audio_codec_and_episode_words_no_longer_hide_video():
    # Real titles from a "spiderman" search that the old filter dropped.
    titles = [
        "The Amazing Spiderman 2 2014 HDCAM NEW AUDIO XviD MP3-RARBG",
        "Ultimate.Spiderman.S02.Ep 01-06.RoSubbed",
        "Spider-Man.2002.1080p.BluRay.x264.AAC",
    ]
    kept, hidden = run([TorrentItem(t, "magnet:?", 10) for t in titles])
    assert sorted(kept) == sorted(titles) and hidden == {}


def test_strong_other_category_words_still_hide_video():
    titles = ["Marvels SpiderMan 2 [DODI Repack]", "Spiderman 3 [English] [PC]", "Spiderman Soundtrack [Mp3 192kbps]"]
    kept, hidden = run([TorrentItem(t, "magnet:?", 10) for t in titles])
    assert kept == []
    assert hidden == {titles[0]: "keyword:dodi", titles[1]: "keyword:pc", titles[2]: "keyword:soundtrack"}


def test_weak_words_rank_lower_at_equal_score():
    plain = TorrentItem("Movie 2019 1080p", "magnet:?", 1)
    codec = TorrentItem("Movie 2019 1080p MP3", "magnet:?", 1)
    kept, _ = run([codec, plain])
    assert kept == ["Movie 2019 1080p", "Movie 2019 1080p MP3"]


def test_source_category_beats_title_keywords():
    # The tracker filed it under Movies, so a "PC" in the title does not hide it.
    movie = TorrentItem("The PC Movie 2020 1080p", "magnet:?", 5, categories=[2000, 2040, 100001])
    game = TorrentItem("Some Game 1080p", "magnet:?", 5, categories=[4050])
    other = TorrentItem("Unsorted pc upload", "magnet:?", 5, categories=[8000, 100002])
    kept, hidden = run([movie, game, other])
    assert kept == ["The PC Movie 2020 1080p"]
    assert hidden == {"Some Game 1080p": "category", "Unsorted pc upload": "keyword:pc"}


def test_low_seeders_are_hidden_with_a_reason_and_unknown_kept():
    items = [TorrentItem("A 1080p", "magnet:?", 0), TorrentItem("B 1080p", "magnet:?", 1), TorrentItem("C", "m", -1)]
    kept, hidden = run(items, min_seeds=1)
    assert sorted(kept) == ["B 1080p", "C"] and hidden == {"A 1080p": "seeds"}


@pytest.mark.parametrize(
    "values, groups",
    [([2000, 2040], {2000}), (["5030"], {5000}), ([6000, 8000, 100003, "x", None], set()), (None, set())],
)
def test_category_groups(values, groups):
    assert category_groups(TorrentItem("t", "l", 1, categories=values)) == groups


def test_torznab_feed_reads_categories_and_infohash():
    feed = """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
      <title>Movie</title><link>http://jackett/dl/1</link><category>2000</category><category>100001</category>
      <torznab:attr name="seeders" value="4"/><torznab:attr name="category" value="2040"/>
      <torznab:attr name="infohash" value="C9E15763F722F23E98A29DECDFAE341B98D53056"/>
    </item></channel></rss>"""
    (item,) = _parse_feed(feed)
    assert item.categories == ["2000", "100001", "2040"]
    assert item.info_hash == "C9E15763F722F23E98A29DECDFAE341B98D53056"
    assert category_groups(item) == {2000}


def drive_selector(monkeypatch, keys, items, hidden):
    from helm.ui import tui

    pressed = iter(keys)
    monkeypatch.setattr(tui, "_read_key", lambda fd: next(pressed))
    monkeypatch.setattr(tui, "_enter_raw_input", lambda fd: None)
    monkeypatch.setattr(tui, "_exit_raw_input", lambda fd, old: None)
    if not tui.IS_WINDOWS:
        monkeypatch.setattr(tui.termios, "tcgetattr", lambda fd: None)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0, raising=False)
    return tui.interactive_selector(items, hidden=hidden)


def test_selector_shows_hidden_items_only_with_show_all(monkeypatch, capsys):
    kept = TorrentItem("Kept 1080p", "magnet:?a", 9, indexer="YTS")
    low = TorrentItem("Dead 1080p", "magnet:?b", 0, indexer="Apibay")
    # Without show:all, the hidden item is counted but cannot be picked.
    assert drive_selector(monkeypatch, ["DOWN", "\r"], [kept], [(low, "seeds")]) == (kept, False)
    assert "1 hidden by filters, type show:all" in capsys.readouterr().out
    # With show:all, it is listed after the kept results and marked.
    keys = list("show:all") + ["DOWN", "\r"]
    assert drive_selector(monkeypatch, keys, [kept], [(low, "seeds")]) == (low, False)
    out = capsys.readouterr().out
    assert "[HIDDEN]" in out and "Apibay | seeds" in out
