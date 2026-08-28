import os
import tempfile

from helm.core.secret_manager import (
    get_secret,
    get_secrets_file,
    load_secrets,
    migrate_config_secrets,
    redact_config,
    sanitize_link,
    set_secrets,
)


def _is_0600(path):
    return (os.stat(path).st_mode & 0o777) == 0o600


def test_set_and_load_secrets(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        set_secrets({"JACKETT_API_KEY": "abc123", "QB_PASSWORD": "hunter2"})
        secrets = load_secrets()
        assert secrets["JACKETT_API_KEY"] == "abc123"
        assert secrets["QB_PASSWORD"] == "hunter2"


def test_secrets_file_is_0600(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        set_secrets({"JACKETT_API_KEY": "abc123"})
        assert _is_0600(get_secrets_file())


def test_set_secrets_tightens_preexisting_loose_permissions(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        path = get_secrets_file()
        with open(path, "w") as f:
            f.write("OLD=1\n")
        os.chmod(path, 0o644)

        set_secrets({"NEW_KEY": "val"})

        assert _is_0600(get_secrets_file())
        secrets = load_secrets()
        assert secrets["OLD"] == "1"
        assert secrets["NEW_KEY"] == "val"


def test_secrets_file_preserves_existing(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        set_secrets({"JACKETT_API_KEY": "abc123"})
        set_secrets({"JACKETT_PASSWORD": "pw"})
        secrets = load_secrets()
        assert secrets["JACKETT_API_KEY"] == "abc123"
        assert secrets["JACKETT_PASSWORD"] == "pw"


def test_get_secret_env_takes_priority(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        set_secrets({"JACKETT_API_KEY": "from_file"})
        monkeypatch.setenv("JACKETT_API_KEY", "from_env")
        assert get_secret("JACKETT_API_KEY") == "from_env"


def test_migrate_config_secrets_strips_and_persists(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        monkeypatch.delenv("JACKETT_API_KEY", raising=False)
        config = {"indexers": ["yts"], "JACKETT_API_KEY": "abc123", "QB_PASSWORD": "pw"}
        result = migrate_config_secrets(config)
        assert "JACKETT_API_KEY" not in result
        assert "QB_PASSWORD" not in result
        assert result["indexers"] == ["yts"]
        assert get_secret("JACKETT_API_KEY") == "abc123"
        assert get_secret("QB_PASSWORD") == "pw"


def test_redact_config():
    redacted = redact_config({"JACKETT_API_KEY": "abc", "indexers": ["yts"]})
    assert redacted["JACKETT_API_KEY"] == "***REDACTED***"
    assert redacted["indexers"] == ["yts"]


def test_sanitize_link_strips_apikey():
    link = "http://localhost:9117/dl/yts/?jackett_apikey=topsecret&path=abc&file=x"
    assert "topsecret" not in sanitize_link(link)
    assert sanitize_link(link).startswith("http://localhost:9117/dl/yts/?path=abc&file=x")


def test_sanitize_link_leaves_magnets_alone():
    magnet = "magnet:?xt=urn:btih:abcd"
    assert sanitize_link(magnet) == magnet
