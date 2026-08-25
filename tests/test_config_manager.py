import os
import tempfile
import json
import pytest
from helm.core.config_manager import save_config, load_config

def test_save_and_load_config(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmpdirname)
        config = {"JACKETT_API_KEY": "12345"}
        save_config(config)
        loaded = load_config()
        assert loaded["JACKETT_API_KEY"] == "12345"
