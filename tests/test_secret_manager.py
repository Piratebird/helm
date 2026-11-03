import os
import tempfile

from helm.config.secret_manager import (
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


def test_secrets_cache_follows_config_override(monkeypatch, tmp_path):
    from helm.config import secret_manager

    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "secrets.env").write_text("JACKETT_PASSWORD=first-private\n")
    (second / "secrets.env").write_text("JACKETT_PASSWORD=second-private\n")
    monkeypatch.delenv("JACKETT_PASSWORD", raising=False)
    monkeypatch.setenv("HELM_CONFIG_DIR", str(first))
    assert secret_manager.get_secret("JACKETT_PASSWORD") == "first-private"
    monkeypatch.setenv("HELM_CONFIG_DIR", str(second))
    assert secret_manager.get_secret("JACKETT_PASSWORD") == "second-private"


def test_redaction_handles_nested_config_and_encoded_secrets(monkeypatch):
    from helm.config.secret_manager import redact_config, redact_text

    monkeypatch.setenv("QB_PASSWORD", "p@ss word")
    original = {
        "nested": {"password": "unknown", "url": "https://alice:other@example.test/?apikey=abc123"},
        "values": ["p%40ss%20word", "p%40ss+word"],
    }
    result = redact_config(original)
    assert result["nested"]["password"] == "***REDACTED***"
    assert "abc123" not in str(result)
    assert "alice:other" not in str(result)
    assert all(value == "***REDACTED***" for value in result["values"])
    assert original["nested"]["password"] == "unknown"
    assert "header-secret" not in redact_text("Authorization: Bearer header-secret")
