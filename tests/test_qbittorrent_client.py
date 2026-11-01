from urllib.parse import parse_qs

import pytest
import responses

import helm.core.qbittorrent_client as client
from helm.core.qbittorrent_client import add_magnet, delete_tagged, login_qbittorrent, qb_request

BASE = "http://localhost:8080"
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


@pytest.fixture(autouse=True)
def qbittorrent(monkeypatch):
    monkeypatch.setenv("QB_WEBUI", BASE)
    monkeypatch.setenv("QB_PASSWORD", "testpass")
    monkeypatch.setattr(client, "_session", None)
    monkeypatch.setattr(client, "_session_key", None)


@responses.activate
def test_login_qbittorrent_bypassed():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    assert login_qbittorrent() is not None
    assert [call.request.method for call in responses.calls] == ["GET"]


@responses.activate
def test_add_magnet_submits_urls_and_tags():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.POST, BASE + "/api/v2/torrents/add", status=200, body="Ok.")

    add_magnet(MAGNET, save_path="/downloads/x", tag="helm,helm-job-1")

    body = parse_qs(responses.calls[-1].request.body)
    assert body == {"urls": [MAGNET], "savepath": ["/downloads/x"], "tags": ["helm,helm-job-1"]}


@responses.activate
def test_add_magnet_rejects_short_hash_before_any_request():
    with pytest.raises(ValueError, match="info hash"):
        add_magnet("magnet:?xt=urn:btih:12345")
    assert len(responses.calls) == 0


@responses.activate
def test_add_reports_qbittorrent_rejection():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.POST, BASE + "/api/v2/torrents/add", status=200, body="Fails.")
    with pytest.raises(RuntimeError, match="rejected"):
        add_magnet(MAGNET)


@responses.activate
def test_expired_session_logs_in_again_and_retries_once():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.GET, BASE + "/api/v2/torrents/info", status=403)
    responses.add(responses.GET, BASE + "/api/v2/torrents/info", status=200, json=[{"hash": "abc"}])

    assert qb_request("GET", "/api/v2/torrents/info").json() == [{"hash": "abc"}]
    paths = [call.request.url.rsplit("/", 1)[1] for call in responses.calls]
    assert paths == ["version", "info", "version", "info"]


@responses.activate
def test_persistent_403_is_raised():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.GET, BASE + "/api/v2/torrents/info", status=403)
    with pytest.raises(Exception, match="403"):
        qb_request("GET", "/api/v2/torrents/info")


@responses.activate
def test_session_cache_follows_changed_url(monkeypatch):
    other = "http://localhost:9090"
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.GET, other + "/api/v2/app/version", status=200)

    first = login_qbittorrent()
    assert login_qbittorrent() is first
    monkeypatch.setenv("QB_WEBUI", other)
    assert login_qbittorrent() is not first


@responses.activate
def test_login_failure_is_reported_and_not_cached():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=403)
    responses.add(responses.POST, BASE + "/api/v2/auth/login", status=401, body="nope")
    with pytest.raises(ConnectionError, match="401"):
        login_qbittorrent()
    assert client._session is None


@responses.activate
def test_delete_tagged_keeps_files_and_skips_empty_tags():
    responses.add(responses.GET, BASE + "/api/v2/app/version", status=200)
    responses.add(responses.GET, BASE + "/api/v2/torrents/info", json=[{"hash": "a"}, {"hash": "b"}])
    responses.add(responses.POST, BASE + "/api/v2/torrents/delete", status=200)
    delete_tagged("helm-job-1")
    assert parse_qs(responses.calls[-1].request.body) == {"hashes": ["a|b"], "deleteFiles": ["false"]}

    responses.replace(responses.GET, BASE + "/api/v2/torrents/info", json=[])
    count = len(responses.calls)
    delete_tagged("helm-job-2")
    assert len(responses.calls) == count + 1
