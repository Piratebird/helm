from helm.config.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, WEAK_NEGATIVE_KEYWORDS


def test_negative_keywords_derived_from_other_categories():
    # Every category's positives must be a negative (hard or weak) for every other
    # category, so adding a keyword to one profile propagates everywhere automatically.
    for category in CONTENT_PROFILES:
        for other, other_keywords in CONTENT_PROFILES.items():
            if other == category:
                continue
            for kw in other_keywords:
                assert (kw in NEGATIVE_KEYWORDS[category]) != (kw in WEAK_NEGATIVE_KEYWORDS[category])


def test_video_negatives_do_not_block_media_terms():
    # "season"/"episode" are video-relevant and must not be filtered from video
    # results, unlike every other category.
    assert "season" not in NEGATIVE_KEYWORDS["video"]
    assert "episode" in NEGATIVE_KEYWORDS["games"]


def test_audio_codecs_and_ep_only_weakly_count_against_video():
    for word in ("aac", "mp3", "flac", "ep"):
        assert word not in NEGATIVE_KEYWORDS["video"] and word in WEAK_NEGATIVE_KEYWORDS["video"]
    for word in ("pc", "dodi", "fitgirl", "soundtrack", "epub"):
        assert word in NEGATIVE_KEYWORDS["video"]
    assert "mp3" in NEGATIVE_KEYWORDS["games"] and not WEAK_NEGATIVE_KEYWORDS["games"]
