import tempfile

from helm.core.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, load_config, save_config


def test_save_and_load_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        monkeypatch.delenv("JACKETT_API_KEY", raising=False)
        config = {"indexers": ["yts"], "JACKETT_API_KEY": "12345"}
        save_config(config)
        loaded = load_config()
        # Secrets must never persist inside config.json
        assert "JACKETT_API_KEY" not in loaded
        assert loaded["indexers"] == ["yts"]


def test_secrets_are_migrated_out_of_config(monkeypatch):
    import os

    from helm.core.secret_manager import get_secret

    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        monkeypatch.delenv("JACKETT_API_KEY", raising=False)
        save_config({"JACKETT_API_KEY": "abc123", "indexers": ["yts"]})
        loaded = load_config()
        assert "JACKETT_API_KEY" not in loaded
        assert get_secret("JACKETT_API_KEY") == "abc123"
        # And it was persisted to the secrets file, not the config file
        config_path = os.path.join(tmpdirname, "config.json")
        with open(config_path) as f:
            assert "JACKETT_API_KEY" not in f.read()


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
