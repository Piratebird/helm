from helm.search import query_variants
from helm.search.query_variants import better_query, candidates, matching
from helm.search.rss_fetcher import SearchResults, TorrentItem


def titles(*names):
    return [TorrentItem(name, "magnet:?", 1) for name in names]


def test_candidates_split_only_long_alphabetic_words():
    found = [variant for _, variant in candidates("spiderman brand new day 2026")]
    assert found == [
        "spi derman brand new day 2026",
        "spid erman brand new day 2026",
        "spide rman brand new day 2026",
        "spider man brand new day 2026",
    ]
    assert candidates("brand new day") == [] and candidates("x264 1080p") == []


def test_matching_counts_titles_that_still_contain_the_word():
    found = titles(
        "Spider-Man Brand New Day 2026 1080p", "Spider.Man.Brand.New.Day.2160p", "Spider Woman Brand New Day"
    )
    assert matching(found, "spiderman", "spider man brand new day") == 2
    assert matching(titles("Spi Derman and the Brand New Day"), "spiderman", "spi derman brand new day") == 1


def probe_from(table):
    return lambda query: table.get(query, [])


RELEASES = titles(*[f"Spider-Man.Brand.New.Day.2026.{n}.1080p" for n in range(40)])


def test_better_query_picks_the_split_that_release_names_use():
    probe = probe_from(
        {"spiderman brand new day": titles("Spiderman brand new day"), "spider man brand new day": RELEASES}
    )
    assert better_query("spiderman brand new day", raw_count=6, probe=probe) == "spider man brand new day"


def test_better_query_stays_quiet_when_the_search_was_not_thin_or_nothing_wins():
    probe = probe_from({"spider man brand new day": RELEASES})
    assert better_query("spiderman brand new day", raw_count=300, probe=probe) is None
    # A split must beat the typed query clearly, not just tie it.
    even = probe_from({"batman begins": RELEASES[:10], "bat man begins": titles(*["Batman Begins"] * 12)})
    assert better_query("batman begins", raw_count=10, probe=even) is None
    assert better_query("brand new day", raw_count=3, probe=probe) is None


def test_search_merges_the_variant_and_says_so(monkeypatch, capsys):
    from helm.ui import tui

    queries = []

    def collect(query, content_type, lite_mode):
        queries.append(query)
        return SearchResults(titles(query)), lite_mode

    monkeypatch.setattr(tui, "_collect", collect)
    monkeypatch.setattr(tui, "_variant_for", lambda query, count: "spider man brand new day")
    res, _ = tui.animated_search("spiderman brand new day", "video", lite_mode=True, show_spinner=False)
    assert queries == ["spiderman brand new day", "spider man brand new day"]
    assert [i.title for i in res] == queries
    assert "Also searched for 'spider man brand new day'" in capsys.readouterr().err


def test_spelling_variants_can_be_turned_off(monkeypatch, tmp_path):
    from helm.ui import tui

    monkeypatch.setenv("HELM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(query_variants, "better_query", lambda query, count: "spider man")
    monkeypatch.setenv("HELM_SPELLING_VARIANTS", "false")
    assert tui._variant_for("spiderman", 1) is None
    monkeypatch.setenv("HELM_SPELLING_VARIANTS", "true")
    assert tui._variant_for("spiderman", 1) == "spider man"


def test_repack_no_longer_hides_video():
    from helm.config.config_manager import NEGATIVE_KEYWORDS, WEAK_NEGATIVE_KEYWORDS

    assert "repack" not in NEGATIVE_KEYWORDS["video"] and "repack" in WEAK_NEGATIVE_KEYWORDS["video"]
    assert "fitgirl" in NEGATIVE_KEYWORDS["video"] and "repack" in NEGATIVE_KEYWORDS["software"]
