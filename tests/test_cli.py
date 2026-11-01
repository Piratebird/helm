import json
import sys
import tempfile

import pytest

import helm.cli
from helm.core.settings import Settings


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
        monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "--json", "ubuntu"])

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
        monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "--json", "definitely-not-found"])

        with pytest.raises(SystemExit) as exc_info:
            helm.cli.main()

        assert exc_info.value.code == 1
        out, _ = capsys.readouterr()
        assert json.loads(out) == []


def test_json_without_config_defaults_to_lite_without_prompting(monkeypatch, capsys):
    # A fresh install with no Jackett key must never emit the interactive
    # "No configuration found" prompt into the JSON stream.
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("HELM_CONFIG_DIR", tmp)
        monkeypatch.delenv("JACKETT_API_KEY", raising=False)
        captured = {}

        def fake_search(query, content_type, lite_mode=False, show_spinner=True):
            captured["lite_mode"] = lite_mode
            return (_make_items(), lite_mode)

        monkeypatch.setattr(helm.cli, "animated_search", fake_search)
        monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "--json", "ubuntu"])

        with pytest.raises(SystemExit) as exc_info:
            helm.cli.main()

        assert exc_info.value.code == 0
        out, _ = capsys.readouterr()
        # stdout must be parseable as pure JSON with no prompt text
        data = json.loads(out)
        assert len(data) == 2
        assert captured["lite_mode"] is True
        assert "No configuration found" not in out


@pytest.mark.parametrize("query", ["", "   "])
def test_search_rejects_blank_query(monkeypatch, capsys, query):
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "--lite", query])
    with pytest.raises(SystemExit) as exc_info:
        helm.cli.main()
    assert exc_info.value.code == 2
    assert "search query must not be empty" in capsys.readouterr().err


@pytest.mark.parametrize("has_raw_results", [False, True])
def test_empty_search_explains_sources_or_filters(monkeypatch, capsys, has_raw_results):
    items = _make_items() if has_raw_results else []
    monkeypatch.setattr(helm.cli, "animated_search", lambda *a, **kw: (items, True))
    monkeypatch.setattr(helm.cli, "load_settings", lambda: Settings.from_legacy({"min_seeds": 1000}))
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "--lite", "ubuntu"])
    with pytest.raises(SystemExit) as exc_info:
        helm.cli.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    if has_raw_results:
        assert "removed by filters" in err
        assert "minimum seeders: 1000" in err
    else:
        assert "unreachable or have no matches" in err
        assert "helm-torrent logs" in err


def test_lite_downloader_without_libtorrent_shows_magnet(monkeypatch, capsys):
    from helm.core.lite_downloader import download_magnet

    monkeypatch.setitem(sys.modules, "libtorrent", None)
    magnet = "magnet:?xt=urn:btih:cafe"
    assert download_magnet(magnet) is False
    output = capsys.readouterr().out
    assert magnet in output
    assert "helm-torrent[download]" in output


@pytest.mark.parametrize("outcome", ["complete", "metadata_timeout", "interrupt"])
def test_lite_downloader_without_legacy_dht_router_api(monkeypatch, tmp_path, capsys, outcome):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from helm.core import lite_downloader

    # Match bindings that expose session settings but no add_dht_router method.
    session = Mock(spec=["add_torrent", "pause"])
    status = SimpleNamespace(has_metadata=outcome == "complete", state=5)
    session.add_torrent.return_value.status.return_value = status
    params = SimpleNamespace(save_path=None)
    lt = SimpleNamespace(
        session=Mock(return_value=session),
        parse_magnet_uri=Mock(return_value=params),
        torrent_status=SimpleNamespace(seeding=5),
    )
    monkeypatch.setitem(sys.modules, "libtorrent", lt)

    def sleep(seconds):
        if outcome == "interrupt":
            raise KeyboardInterrupt

    monkeypatch.setattr(lite_downloader.time, "sleep", sleep)
    magnet = "magnet:?xt=urn:btih:" + "a" * 40
    destination = str(tmp_path / "downloads")

    assert lite_downloader.download_magnet(magnet, destination) is (outcome == "complete")
    settings = lt.session.call_args[0][0]
    assert settings["enable_dht"] is True
    assert "router.bittorrent.com:6881" in settings["dht_bootstrap_nodes"].split(",")
    lt.parse_magnet_uri.assert_called_once_with(magnet)
    session.add_torrent.assert_called_once_with(params)
    assert params.save_path == destination
    assert (tmp_path / "downloads").is_dir()
    output = capsys.readouterr().out
    if outcome == "complete":
        assert "Download complete!" in output
    else:
        session.pause.assert_called_once_with()
        if outcome == "metadata_timeout":
            assert magnet in output
        else:
            assert "interrupted by user" in output


def test_bug_report_omits_unredactable_config(monkeypatch, tmp_path, capsys):
    import zipfile

    from helm.core import config_manager, secret_manager

    config_file = tmp_path / "config.json"
    config_file.write_text('{"JACKETT_API_KEY": "must-stay-private"}')
    monkeypatch.setattr(config_manager, "get_config_file", lambda: str(config_file))
    monkeypatch.setattr(config_manager, "get_log_dir", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr(helm.cli.os.path, "expanduser", lambda path: str(tmp_path))

    def failed_redaction(config):
        raise ValueError("Cannot redact")

    monkeypatch.setattr(secret_manager, "redact_config", failed_redaction)
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "bug-report"])
    with pytest.raises(SystemExit) as exc_info:
        helm.cli.main()
    assert exc_info.value.code == 0
    with zipfile.ZipFile(tmp_path / "helm-bug-report.zip") as archive:
        assert "config.json" not in archive.namelist()
    assert "omitting it" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [["--type", "unknown"], ["--litte"], ["--typo", "music"]])
def test_search_rejects_invalid_options_before_setup(monkeypatch, capsys, arguments):
    from unittest.mock import Mock

    setup = Mock(side_effect=AssertionError("Validation must precede setup"))
    monkeypatch.setattr(helm.cli, "load_settings", setup)
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "ubuntu"] + arguments)
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
    setup.assert_not_called()


def test_cli_normalizes_content_type(monkeypatch):
    captured = []
    monkeypatch.setattr(
        helm.cli, "animated_search", lambda query, category, **kw: captured.append(category) or ([], True)
    )
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "ubuntu", "--lite", "--json", "--type", "MUSIC"])
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 1
    assert captured == ["music"]


@pytest.mark.parametrize("explicit_lite", [True, False])
def test_lite_oneshot_never_starts_or_stops_containers(monkeypatch, explicit_lite):
    from unittest.mock import Mock

    from helm.core import oneshot

    start = Mock()
    stop = Mock()
    monkeypatch.setattr(oneshot, "spin_up_oneshot", start)
    monkeypatch.setattr(oneshot, "teardown_oneshot", stop)
    monkeypatch.setattr(helm.cli, "load_settings", lambda: Settings.from_legacy({"LITE_MODE_ONLY": True}))
    monkeypatch.setattr(helm.cli, "animated_search", lambda *a, **kw: (_make_items(), True))
    arguments = ["helm-torrent", "search", "ubuntu", "--oneshot", "--json"]
    monkeypatch.setattr(sys, "argv", arguments + (["--lite"] if explicit_lite else []))
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 0
    start.assert_not_called()
    stop.assert_not_called()


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "json",
        "empty",
        "send_failure",
        "send_exception",
        "search_exception",
        "interrupt",
        "startup_failure",
        "wait_failure",
    ],
)
def test_oneshot_cleans_up_once_on_every_exit(monkeypatch, outcome):
    from helm.core import oneshot

    events = []
    monkeypatch.setenv("JACKETT_API_KEY", "testkey")
    monkeypatch.setattr(helm.cli, "ensure_config", lambda: None)
    monkeypatch.setattr(helm.cli, "load_settings", lambda: Settings.from_legacy({"min_seeds": 3}))

    def start():
        events.append("start")
        if outcome == "startup_failure":
            raise RuntimeError("partial startup failed")

    def search(*args, **kwargs):
        if outcome == "search_exception":
            raise RuntimeError("search failed")
        if outcome == "interrupt":
            raise KeyboardInterrupt
        return ([] if outcome == "empty" else _make_items(), False)

    def send(*args, **kwargs):
        events.append("send")
        if outcome == "send_exception":
            raise RuntimeError("download failed")
        return outcome != "send_failure"

    def wait():
        events.append("wait")
        if outcome == "wait_failure":
            raise RuntimeError("wait failed")

    monkeypatch.setattr(oneshot, "spin_up_oneshot", start)
    monkeypatch.setattr(oneshot, "teardown_oneshot", lambda: events.append("stop"))
    monkeypatch.setattr(helm.cli, "animated_search", search)
    monkeypatch.setattr(helm.cli, "_send_magnet", send)
    monkeypatch.setattr(helm.cli, "wait_for_download", wait)
    monkeypatch.setattr(
        sys,
        "argv",
        ["helm-torrent", "search", "ubuntu", "--oneshot", "--json" if outcome in ("json", "empty") else "--auto"],
    )
    with pytest.raises((SystemExit, RuntimeError)) as error:
        helm.cli.main()
    assert events[0] == "start"
    assert events[-1] == "stop"
    assert events.count("stop") == 1
    if outcome in ("empty", "send_failure"):
        assert isinstance(error.value, SystemExit) and error.value.code == 1
    elif outcome in ("success", "json", "interrupt"):
        assert isinstance(error.value, SystemExit) and error.value.code == 0
    else:
        assert isinstance(error.value, RuntimeError)
    if outcome == "success":
        assert events == ["start", "send", "wait", "stop"]


def test_download_uses_cli_directory_override(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    session = Mock(spec=["add_torrent", "pause"])
    session.add_torrent.return_value.status.return_value = SimpleNamespace(has_metadata=True, state=5)
    params = SimpleNamespace(save_path=None)
    monkeypatch.setitem(
        sys.modules,
        "libtorrent",
        SimpleNamespace(
            session=lambda settings: session,
            parse_magnet_uri=lambda magnet: params,
            torrent_status=SimpleNamespace(seeding=5),
        ),
    )
    destination = tmp_path / "custom downloads"
    monkeypatch.setattr(helm.cli, "animated_search", lambda *a, **kw: (_make_items(), True))
    monkeypatch.setattr(
        sys, "argv", ["helm-torrent", "search", "ubuntu", "--lite", "--auto", "--dl-dir", str(destination)]
    )
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 0
    assert params.save_path == str(destination)
    assert destination.is_dir()
    session.pause.assert_called_once_with()


def test_real_search_fallback_keeps_json_stdout_clean(monkeypatch, capsys):
    from helm.core import lite_fetcher, rss_fetcher

    items = _make_items()
    results = rss_fetcher.SearchResults(items, [rss_fetcher.SourceOutcome("Nyaa", "timeout")])

    def unavailable(*args, **kwargs):
        raise RuntimeError("indexer unavailable with private-token")

    monkeypatch.setenv("JACKETT_API_KEY", "testkey")
    monkeypatch.setattr(helm.cli, "load_settings", lambda: Settings.from_legacy({"min_seeds": 3}))
    monkeypatch.setattr(helm.cli, "ensure_config", lambda: None)
    monkeypatch.setattr(rss_fetcher, "search_indexers", unavailable)
    monkeypatch.setattr(lite_fetcher, "search_lite", lambda query, content_type: results)
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "ubuntu", "--json"])
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 0
    out, err = capsys.readouterr()
    assert len(json.loads(out)) == 2
    assert "Nyaa: timeout (0)" in err
    assert "Indexer manager unavailable" in err
    assert "private-token" not in out + err


def test_indexer_exit_cleans_up_once(monkeypatch):
    from types import SimpleNamespace

    from helm.core import indexer_manager, oneshot

    events = []
    monkeypatch.setenv("JACKETT_API_KEY", "testkey")
    monkeypatch.setattr(helm.cli, "ensure_config", lambda: None)
    monkeypatch.setattr(helm.cli, "load_settings", lambda: Settings.from_legacy({}))
    monkeypatch.setattr(oneshot, "spin_up_oneshot", lambda: events.append("start"))
    monkeypatch.setattr(oneshot, "teardown_oneshot", lambda: events.append("stop"))
    monkeypatch.setattr(indexer_manager, "JackettManager", lambda: SimpleNamespace(get_all_indexers=lambda: []))

    def exit_indexers(*args):
        raise KeyboardInterrupt

    def end_input(*args):
        raise EOFError

    monkeypatch.setattr(helm.cli, "interactive_indexer_selector", exit_indexers)
    monkeypatch.setattr("builtins.input", end_input)
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "indexers", "--oneshot"])
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 0
    assert events == ["start", "stop"]


def test_bug_report_streams_and_redacts_historical_logs(monkeypatch, tmp_path):
    import zipfile

    from helm.core import config_manager

    config = tmp_path / "config.json"
    config.write_text('{"nested": {"password": "nested-private"}}')
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "helm.log").write_text(
        "old request https://alice:pass@example.test/?apikey=historical-key\nAuthorization: Bearer historical-auth\n"
    )
    monkeypatch.setattr(config_manager, "get_config_file", lambda: str(config))
    monkeypatch.setattr(config_manager, "get_log_dir", lambda: str(logs))
    monkeypatch.setattr(helm.cli.os.path, "expanduser", lambda path: str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "bug-report"])
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 0
    with zipfile.ZipFile(tmp_path / "helm-bug-report.zip") as archive:
        contents = archive.read("helm.log").decode() + archive.read("config.json").decode()
    assert all(
        secret not in contents for secret in ("historical-key", "historical-auth", "alice:pass", "nested-private")
    )


def test_cli_worker_limit_overrides_environment(monkeypatch):
    import os

    seen = []
    monkeypatch.setenv("HELM_LITE_WORKERS", "6")
    monkeypatch.setattr(
        helm.cli, "animated_search", lambda *a, **kw: seen.append(os.environ["HELM_LITE_WORKERS"]) or ([], True)
    )
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "generated", "--lite", "--json", "--lite-workers", "2"])
    with pytest.raises(SystemExit) as error:
        helm.cli.main()
    assert error.value.code == 1
    assert seen == ["2"]
