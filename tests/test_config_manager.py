import tempfile

from helm.core.config_manager import load_config, save_config


def test_save_and_load_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        config = {"JACKETT_API_KEY": "12345"}
        save_config(config)
        loaded = load_config()
        assert loaded["JACKETT_API_KEY"] == "12345"
