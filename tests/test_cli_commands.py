"""In-process tests for helm.cli.commands: status, indexer management and the interactive session."""

import builtins
from types import SimpleNamespace

import pytest

import helm.ui.tui as tui
from helm.cli import deps
from helm.cli.commands import indexers, session, status
from helm.config.settings import Settings

# ---------------------------------------------------------------- status


@pytest.fixture
def plain_settings(monkeypatch):
    monkeypatch.setattr(deps, "load_settings", lambda: Settings.from_legacy({}))


def test_status_without_an_engine(monkeypatch, plain_settings, capsys):
    monkeypatch.setattr(status.shutil, "which", lambda name: None)
    status.run()
    out = capsys.readouterr().out
    assert "Native Mode - No containers running." in out
    assert "Jackett:     http://localhost:19117" in out


def test_status_lists_only_helm_containers_and_marks_missing_services(monkeypatch, plain_settings, capsys):
    monkeypatch.setattr(status.shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout="helm-jackett: Up 2 minutes\nother-db: Up\nhelm-qbittorrent: Up\n")

    monkeypatch.setattr(status.subprocess, "run", run)
    status.run()
    out = capsys.readouterr().out
    assert calls[0][0] == "podman"
    assert "helm-jackett: Up 2 minutes" in out and "other-db" not in out
    assert "Prowlarr:    not running" in out
    assert "qBittorrent: http://localhost:18080" in out


def test_status_survives_a_failing_engine(monkeypatch, plain_settings, capsys):
    monkeypatch.setattr(status.shutil, "which", lambda name: "/usr/bin/docker")

    def run(cmd, **kwargs):
        raise OSError("daemon down")

    monkeypatch.setattr(status.subprocess, "run", run)
    status.run()
    out = capsys.readouterr().out
    assert "Failed to run docker ps." in out and "Prowlarr:    http://localhost:19696" in out


def test_status_with_no_helm_containers(monkeypatch, plain_settings, capsys):
    monkeypatch.setattr(status.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(status.subprocess, "run", lambda cmd, **kw: SimpleNamespace(stdout="db: Up\n"))
    status.run()
    assert "No helm containers found." in capsys.readouterr().out


# ---------------------------------------------------------------- indexers


class Manager:
    url = "http://jackett.test"

    def __init__(self, fail_add=None):
        self.fail_add = fail_add
        self.log = []

    def get_all_indexers(self):
        return [
            {"id": "a", "title": "Alpha", "configured": True, "type": "public"},
            {"id": "b", "title": "Beta", "configured": False, "type": "private"},
            {"id": "c", "title": "Gamma", "configured": False, "type": "public"},
        ]

    def remove_indexer(self, identifier):
        self.log.append(("remove", identifier))

    def add_indexer(self, identifier):
        self.log.append(("add", identifier))
        if self.fail_add:
            raise RuntimeError(self.fail_add)


def indexer_args(**kw):
    return SimpleNamespace(**{"lite": False, "prowlarr": False, "jackett": False, **kw})


@pytest.fixture
def indexer_env(monkeypatch):
    monkeypatch.setattr(deps, "ensure_config", lambda: None)
    monkeypatch.setattr(deps, "load_settings", lambda: Settings.from_legacy({"INDEXER_MANAGER": "jackett"}))
    monkeypatch.setattr(indexers.time, "sleep", lambda seconds: None)

    def setup(manager, picks, reply="n"):
        import helm.search.indexer_manager as managers

        monkeypatch.setattr(managers, "JackettManager", lambda: manager)
        monkeypatch.setattr(managers, "ProwlarrManager", lambda: manager)
        queue = list(picks)

        def pick(all_indexers):
            item = queue.pop(0)
            if item is KeyboardInterrupt:
                raise KeyboardInterrupt
            return next(i for i in all_indexers if i["id"] == item) if item else None

        monkeypatch.setattr(deps, "interactive_indexer_selector", pick)
        monkeypatch.setattr(builtins, "input", lambda prompt="": reply)

    return setup


def test_indexers_add_remove_and_refuse_private(indexer_env, capsys):
    manager = Manager()
    indexer_env(manager, ["a", "b", "c", None, KeyboardInterrupt])
    with pytest.raises(SystemExit) as stop:
        indexers.manage_indexers(indexer_args())
    assert stop.value.code == 0
    assert manager.log == [("remove", "a"), ("add", "c")]
    out = capsys.readouterr().out
    assert "Removing Alpha" in out and "Adding Gamma" in out
    assert "Cannot add private trackers via CLI" in out and "http://jackett.test" in out


def test_indexers_explain_a_jackett_500(indexer_env, capsys):
    indexer_env(Manager(fail_add="HTTP 500 Server Error"), ["c", KeyboardInterrupt])
    with pytest.raises(SystemExit):
        indexers.manage_indexers(indexer_args(prowlarr=True))
    assert "Jackett rejected the default config" in capsys.readouterr().out


def test_indexers_continue_to_search_when_asked(indexer_env):
    indexer_env(Manager(), [KeyboardInterrupt], reply="y")
    # Answering yes returns to the caller, which then starts the search session.
    with pytest.raises(IndexError):
        indexers.manage_indexers(indexer_args(jackett=True))


def test_indexers_refuse_lite_mode_and_report_manager_errors(monkeypatch, indexer_env, capsys):
    with pytest.raises(SystemExit) as stop:
        indexers.manage_indexers(indexer_args(lite=True))
    assert stop.value.code == 1 and "Lite Mode" in capsys.readouterr().err

    import helm.search.indexer_manager as managers

    def broken():
        raise RuntimeError("no API key")

    monkeypatch.setattr(managers, "JackettManager", broken)
    with pytest.raises(SystemExit) as stop:
        indexers.manage_indexers(indexer_args())
    assert stop.value.code == 1 and "no API key" in capsys.readouterr().err


# ---------------------------------------------------------------- interactive session


def item(title="Ubuntu 24.04"):
    return SimpleNamespace(title=title, link="magnet:?xt=urn:btih:" + "a" * 40, seeders=10, indexer="test")


def session_args(**kw):
    base = {"command": "ui", "query": None, "json": False, "lite": True, "oneshot": False, "auto": False}
    return SimpleNamespace(**{**base, "type": "video", **kw})


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(session, "print_header", lambda args, subtitle: None)
    monkeypatch.setattr(deps, "load_settings", lambda: Settings.from_legacy({}))
    monkeypatch.setattr(deps, "dedupe", lambda items: items)
    monkeypatch.setattr(deps, "filter_items", lambda items, *a, **kw: items)
    events = []

    def setup(inputs, checkboxes, results, picks):
        inputs, checkboxes, results, picks = map(list, (inputs, checkboxes, results, picks))

        def fake_input(prompt=""):
            events.append(("input", prompt))
            reply = inputs.pop(0)
            if reply is EOFError:
                raise EOFError
            return reply

        def search(query, categories, **kw):
            events.append(("search", query, categories))
            return results.pop(0), kw.get("lite_mode", True)

        monkeypatch.setattr(builtins, "input", fake_input)
        monkeypatch.setattr(tui, "interactive_checkbox_selector", lambda options, title: checkboxes.pop(0))
        monkeypatch.setattr(deps, "animated_search", search)
        monkeypatch.setattr(deps, "interactive_selector", lambda found, *a, **kw: picks.pop(0))
        monkeypatch.setattr(session, "send_magnet", lambda chosen, *a, **kw: events.append(("send", chosen.title)))
        return events

    return setup


def test_prompt_back_then_books_strips_punctuation(ui):
    events = ui(["ubuntu", "dune: part one!"], ["BACK", ["books"]], [[item("Dune")]], [(item("Dune"), False)])
    session.run(session_args(), teardown_oneshot=lambda: None)
    assert ("search", "dune part one", "books") in events
    assert events[-1] == ("send", "Dune")


def test_empty_query_is_asked_again(ui):
    events = ui(["", "linux"], [["software", "video"]], [[item()]], [(item(), False)])
    session.run(session_args(), teardown_oneshot=lambda: None)
    assert ("search", "linux", "software,video") in events


def test_no_results_returns_to_the_prompt(ui, capsys):
    ui(["missing", "", EOFError], [["video"]], [[]], [])
    with pytest.raises(SystemExit) as stop:
        session.run(session_args(), teardown_oneshot=lambda: None)
    assert stop.value.code == 0
    err = capsys.readouterr().err
    assert "No torrents were found" in err and "Lite sources may be unreachable" in err


def test_back_from_results_goes_to_the_prompt_or_exits_for_a_query(ui):
    events = ui(["first", "second"], [["video"], ["video"]], [[item()], [item("Second")]], [
        (None, "BACK"),
        (item("Second"), False),
    ])  # fmt: skip
    session.run(session_args(), teardown_oneshot=lambda: None)
    assert [e for e in events if e[0] == "search"] == [("search", "first", "video"), ("search", "second", "video")]

    ui([], [], [[item()]], [(None, "BACK")])
    with pytest.raises(SystemExit) as stop:
        session.run(session_args(query="ubuntu"), teardown_oneshot=lambda: None)
    assert stop.value.code == 0


def test_teardown_choice_waits_and_stops_a_permanent_stack(ui, monkeypatch):
    events = ui([], [], [[item()]], [(item(), True)])
    monkeypatch.setattr(deps, "wait_for_download", lambda tag: events.append(("wait", tag[:8])))
    session.run(session_args(query="ubuntu", lite=False), teardown_oneshot=lambda: events.append(("teardown",)))
    assert events[-2:] == [("wait", "helm-ui-"), ("teardown",)]


def test_ctrl_c_in_results_tears_down_only_when_asked(ui, monkeypatch):
    events = ui([], [], [[item()]], [])

    def interrupt(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(deps, "interactive_selector", interrupt)
    with pytest.raises(SystemExit):
        session.run(session_args(query="ubuntu", lite=False), teardown_oneshot=lambda: events.append("teardown"))
    assert "teardown" not in events


def test_lite_send_uses_the_built_in_downloader(monkeypatch):
    import helm.torrents.lite_downloader as downloader

    monkeypatch.setattr(downloader, "download_torrent", lambda link: f"downloaded {link[:20]}")
    assert session.send_magnet(item(), lite_mode=True) == "downloaded magnet:?xt=urn:btih:"


def test_send_falls_back_to_lite_when_qbittorrent_fails(monkeypatch, capsys):
    import helm.torrents.lite_downloader as downloader

    def refuse(link, tag=None):
        raise ConnectionError("refused")

    monkeypatch.setattr(deps, "add_magnet", refuse)
    monkeypatch.setattr(downloader, "download_torrent", lambda link: True)
    assert session.send_magnet(item(), lite_mode=False) is True
    assert "Falling back to LITE MODE" in capsys.readouterr().err


def test_session_hides_with_real_filters_and_passes_hidden_to_selector(ui, monkeypatch):
    from helm.search.rss_fetcher import TorrentItem
    from helm.search.torrent_filter import dedupe, filter_items

    monkeypatch.setattr(deps, "dedupe", dedupe)
    monkeypatch.setattr(deps, "filter_items", filter_items)
    found = [
        TorrentItem("Movie 2019 1080p XviD MP3", "magnet:?xt=urn:btih:" + "a" * 40, 5),
        TorrentItem("Movie 2019 [DODI Repack]", "magnet:?xt=urn:btih:" + "b" * 40, 5),
        TorrentItem("Movie 2019 720p", "magnet:?xt=urn:btih:" + "c" * 40, 0),
        TorrentItem("Movie 2019 PC Edition", "magnet:?xt=urn:btih:" + "d" * 40, 5, categories=[2000]),
    ]
    seen = {}

    def selector(kept, *a, **kw):
        seen["kept"] = [i.title for i in kept]
        seen["hidden"] = {i.title: reason for i, reason in kw["hidden"]}
        return kept[0], False

    ui(["movie"], [["video"]], [found], [])
    monkeypatch.setattr(deps, "interactive_selector", selector)
    session.run(session_args(), teardown_oneshot=lambda: None)
    assert sorted(seen["kept"]) == ["Movie 2019 1080p XviD MP3", "Movie 2019 PC Edition"]
    assert seen["hidden"] == {"Movie 2019 [DODI Repack]": "keyword:dodi", "Movie 2019 720p": "seeds"}
