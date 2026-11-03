"""Progress output, the tagged one-shot wait, qBittorrent errors and daemon pruning."""

import io
import json
import os
import subprocess
import sys

import pytest
import requests
import responses

import helm.stack.oneshot as oneshot
import helm.torrents.qbittorrent_client as qbc
from helm.services import runtime
from helm.ui.ansi import Progress


class Pipe(io.StringIO):
    def isatty(self):
        return False


class Tty(io.StringIO):
    def isatty(self):
        return True


# ---------------------------------------------------------------- Progress


def test_progress_redraws_in_place_on_a_terminal():
    out = Tty()
    progress = Progress(out)
    progress.update(1, "\033[1m1%\033[0m")
    progress.update(1, "\033[1m1%\033[0m")
    assert out.getvalue() == "\r\033[K\033[1m1%\033[0m" * 2


def test_progress_writes_plain_lines_once_per_percent_elsewhere():
    now = [0.0]
    out = Pipe()
    progress = Progress(out, interval=30, clock=lambda: now[0])
    for percent in (0.1, 0.5, 0.9, 1.0, 1.5, 2.2):
        progress.update(percent, f"\033[33m{percent}%\033[0m")
    assert out.getvalue().splitlines() == ["0.1%", "1.0%", "2.2%"]

    now[0] = 31.0  # no new percentage, but the interval passed
    progress.update(2.3, "still 2.3%")
    now[0] = 32.0
    progress.update(2.4, "quiet")
    assert out.getvalue().splitlines()[-1] == "still 2.3%"
    assert "\033" not in out.getvalue() and "\r" not in out.getvalue()


# ---------------------------------------------------------------- wait_for_download


def torrent(name, progress, digest="a"):
    return {"name": name, "progress": progress, "hash": digest}


@pytest.fixture
def quiet_wait(monkeypatch, capsys):
    """No sleeping; output is read with capsys, which is not a terminal."""
    monkeypatch.setattr(oneshot.time, "sleep", lambda seconds: None)
    return capsys


def test_wait_watches_only_the_given_tag(monkeypatch, quiet_wait):
    seen = []
    replies = iter([[], [torrent("mine", 0.5)], [torrent("mine", 1.0)]])

    def tagged(tag):
        seen.append(tag)
        return next(replies)

    monkeypatch.setattr(qbc, "tagged_torrents", tagged)
    oneshot.wait_for_download("helm-ui-abc", poll=0, settle=0)
    assert seen == ["helm-ui-abc"] * 3
    captured = quiet_wait.readouterr()
    assert "Downloading 'mine...' (50.0%)" in captured.err
    assert "100% complete" in captured.err
    assert captured.out == ""
    assert "\r" not in captured.err


def test_wait_defaults_to_every_helm_torrent(monkeypatch, quiet_wait):
    seen = []
    monkeypatch.setattr(qbc, "tagged_torrents", lambda tag: seen.append(tag) or [torrent("x", 1.0)])
    oneshot.wait_for_download(poll=0, settle=0)
    assert seen == ["helm"]


def test_wait_keeps_going_through_transient_errors(monkeypatch, quiet_wait):
    replies = [requests.ConnectionError("restarting"), ValueError("bad json"), [torrent("x", 1.0)]]

    def tagged(tag):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(qbc, "tagged_torrents", tagged)
    oneshot.wait_for_download("t", poll=0, settle=0)
    assert replies == []


def test_wait_stops_on_missing_credentials(monkeypatch, quiet_wait):
    def tagged(tag):
        raise qbc.QBittorrentAuthError("Please set QB_PASSWORD")

    monkeypatch.setattr(qbc, "tagged_torrents", tagged)
    with pytest.raises(qbc.QBittorrentAuthError):
        oneshot.wait_for_download("t", poll=0, settle=0)


def test_ctrl_c_removes_only_unfinished_torrents_of_the_tag(monkeypatch, quiet_wait):
    calls = []
    state = {"interrupted": False}

    def tagged(tag):
        calls.append(("list", tag))
        if not state["interrupted"]:
            state["interrupted"] = True
            raise KeyboardInterrupt
        return [torrent("done", 1.0, "d1"), torrent("half", 0.5, "h1"), torrent("new", 0.0, "n1")]

    monkeypatch.setattr(qbc, "tagged_torrents", tagged)
    monkeypatch.setattr(qbc, "qb_request", lambda method, path, **kw: calls.append((method, path, kw["data"])))
    with pytest.raises(KeyboardInterrupt):
        oneshot.wait_for_download("helm-ui-1", poll=0, settle=0)
    assert calls[-1] == ("POST", "/api/v2/torrents/delete", {"hashes": "h1|n1", "deleteFiles": "true"})
    assert all(call[1] == "helm-ui-1" for call in calls if call[0] == "list")


def test_ctrl_c_cleanup_survives_an_unreachable_qbittorrent(monkeypatch, quiet_wait):
    def tagged(tag):
        if not tagged.calls:
            tagged.calls = 1
            raise KeyboardInterrupt
        raise requests.ConnectionError("gone")

    tagged.calls = 0
    monkeypatch.setattr(qbc, "tagged_torrents", tagged)
    with pytest.raises(KeyboardInterrupt):
        oneshot.wait_for_download("t", poll=0, settle=0)


# ---------------------------------------------------------------- qBittorrent client


def test_session_tags_are_unique_and_recognisable():
    tags = {qbc.session_tag() for _ in range(50)}
    assert len(tags) == 50 and all(tag.startswith("helm-ui-") and len(tag) == 20 for tag in tags)


def test_send_magnet_tags_the_torrent(monkeypatch):
    from helm.cli import deps
    from helm.cli.commands import session

    sent = []
    monkeypatch.setattr(deps, "add_magnet", lambda link, tag=None: sent.append((link, tag)))
    item = type("Item", (), {"link": "magnet:?xt=urn:btih:" + "a" * 40})()
    assert session.send_magnet(item, lite_mode=False, json_mode=True, tag="helm-ui-1") is True
    assert session.send_magnet(item, lite_mode=False, json_mode=True) is True
    assert [tag for _link, tag in sent] == ["helm,helm-ui-1", "helm"]


@pytest.fixture
def qb_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("HELM_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("QB_WEBUI", "http://qb.test")
    monkeypatch.setattr(qbc, "_session", None)
    monkeypatch.setattr(qbc, "_session_key", None)


def test_missing_password_is_an_auth_error(monkeypatch, qb_settings):
    monkeypatch.setattr(qbc, "get_secret", lambda key: "")
    with pytest.raises(qbc.QBittorrentAuthError, match="QB_PASSWORD"):
        qbc.login_qbittorrent()
    assert issubclass(qbc.QBittorrentAuthError, ConnectionError)


@responses.activate
def test_rejected_login_is_an_auth_error(monkeypatch, qb_settings):
    monkeypatch.setattr(qbc, "get_secret", lambda key: "pw")
    responses.add(responses.GET, "http://qb.test/api/v2/app/version", status=403)
    responses.add(responses.POST, "http://qb.test/api/v2/auth/login", status=401, body="Fails.")
    with pytest.raises(qbc.QBittorrentAuthError, match="HTTP 401"):
        qbc.login_qbittorrent()


@responses.activate
def test_failed_verification_keeps_its_message(monkeypatch, qb_settings):
    monkeypatch.setattr(qbc, "get_secret", lambda key: "pw")
    responses.add(responses.GET, "http://qb.test/api/v2/app/version", status=403)
    responses.add(responses.POST, "http://qb.test/api/v2/auth/login", status=200, body="Ok.")
    responses.add(responses.GET, "http://qb.test/api/v2/app/version", status=403)
    with pytest.raises(qbc.QBittorrentAuthError, match="Failed to verify qbittorrent authentication: Secondary"):
        qbc.login_qbittorrent()


@responses.activate
def test_unreachable_verification_is_a_connection_error(monkeypatch, qb_settings):
    monkeypatch.setattr(qbc, "get_secret", lambda key: "pw")
    responses.add(responses.GET, "http://qb.test/api/v2/app/version", status=403)
    responses.add(responses.POST, "http://qb.test/api/v2/auth/login", status=200, body="Ok.")
    responses.add(responses.GET, "http://qb.test/api/v2/app/version", body=requests.ConnectionError("down"))
    with pytest.raises(ConnectionError, match="Failed to verify qbittorrent authentication: down") as error:
        qbc.login_qbittorrent()
    assert not isinstance(error.value, qbc.QBittorrentAuthError)


# ---------------------------------------------------------------- daemon pruning


def write_record(directory, identifier, pid, identity):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{identifier}.json").write_text(json.dumps({"id": identifier, "pid": pid, "identity": identity}))
    (directory / f"{identifier}.log").write_text("log")


def test_prune_removes_only_stopped_daemons(monkeypatch, tmp_path):
    monkeypatch.setenv("HELM_STATE_DIR", str(tmp_path))
    directory = tmp_path / "daemons"
    alive = "a" * 12
    dead = "b" * 12
    write_record(directory, alive, os.getpid(), runtime.process_start(os.getpid()))
    write_record(directory, dead, 2**22 + 7, "gone")
    (directory / "notes.json").write_text("not a record")
    (directory / "cccccccccccc.json").write_text(json.dumps({"id": "../escape", "pid": 2**22 + 8, "identity": "x"}))

    assert runtime.prune_daemons() == [dead]
    assert sorted(p.name for p in directory.iterdir()) == sorted(
        [f"{alive}.json", f"{alive}.log", "notes.json", "cccccccccccc.json"]
    )
    assert runtime.prune_daemons() == []


def test_daemon_prune_command(tmp_path):
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    env = {
        **os.environ,
        "PYTHONPATH": src,
        "HELM_STATE_DIR": str(tmp_path),
        "HELM_CONFIG_DIR": str(tmp_path / "config"),
    }
    write_record(tmp_path / "daemons", "d" * 12, 2**22 + 9, "gone")
    result = subprocess.run(
        [sys.executable, "-m", "helm.cli", "daemon", "prune"], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"pruned": ["d" * 12]}
