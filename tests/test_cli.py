import json
import sys
import tempfile

import pytest

import helm.cli


class FakeItem:
    def __init__(self, title, link, seeders, leechers=0, size=0, pubdate=None):
        self.title = title
        self.link = link
        self.seeders = seeders
        self.leechers = leechers
        self.size = size
        self.pubdate = pubdate


def _make_items():
    return [
        FakeItem(
            "Ubuntu 24.04 1080p x264",
            "magnet:?xt=urn:btih:aaaabbbbccccddddeeeeffff0000111122223333",
            100,
            10,
            1024 * 1024,
            "2026-01-01",
        ),
        FakeItem(
            "Ubuntu 24.04 720p",
            "magnet:?xt=urn:btih:4444555566667777888899990000aaaabbbbcccc",
            50,
            5,
        ),
    ]


def test_search_json_output_is_machine_readable(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmp)
        monkeypatch.setenv("JACKETT_API_KEY", "testkey")
        monkeypatch.setattr(helm.cli, "ensure_config", lambda: None)
        monkeypatch.setattr(
            helm.cli,
            "animated_search",
            lambda query, content_type, lite_mode=False, show_spinner=True: (_make_items(), True),
        )
        monkeypatch.setattr(sys, "argv", ["helm", "search", "--json", "ubuntu"])

        with pytest.raises(SystemExit) as exc_info:
            helm.cli.main()

        assert exc_info.value.code == 0

        out, _ = capsys.readouterr()
        data = json.loads(out)
        # Sorted by keyword score: the 1080p x264 result first
        assert [t["title"] for t in data] == ["Ubuntu 24.04 1080p x264", "Ubuntu 24.04 720p"]
        assert data[0]["seeders"] == 100
        # secrets and jackett keys must never leak into stdout
        assert "testkey" not in out


def test_search_with_no_results_exits_one(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmp)
        monkeypatch.setenv("JACKETT_API_KEY", "testkey")
        monkeypatch.setattr(helm.cli, "ensure_config", lambda: None)
        monkeypatch.setattr(
            helm.cli,
            "animated_search",
            lambda query, content_type, lite_mode=False, show_spinner=True: ([], False),
        )
        monkeypatch.setattr(sys, "argv", ["helm", "search", "--json", "definitely-not-found"])

        with pytest.raises(SystemExit) as exc_info:
            helm.cli.main()

        assert exc_info.value.code == 1
        out, _ = capsys.readouterr()
        assert json.loads(out) == []
