from helm.config.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS


def test_negative_keywords_derived_from_other_categories():
    # Every category's positives must be a negative for every other category,
    # so adding a keyword to one profile propagates everywhere automatically.
    for category in CONTENT_PROFILES:
        for other, other_keywords in CONTENT_PROFILES.items():
            if other == category:
                continue
            for kw in other_keywords:
                assert kw in NEGATIVE_KEYWORDS[category]


def test_video_negatives_do_not_block_media_terms():
    # "season"/"episode" are video-relevant and must not be filtered from video
    # results, unlike every other category.
    assert "season" not in NEGATIVE_KEYWORDS["video"]
    assert "episode" in NEGATIVE_KEYWORDS["games"]
