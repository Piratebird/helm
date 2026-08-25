import responses

from helm.core.qbittorrent_client import add_magnet, login_qbittorrent


@responses.activate
def test_login_qbittorrent_bypassed(monkeypatch):
    monkeypatch.setenv("QB_PASSWORD", "testpass")
    responses.add(responses.GET, "http://localhost:8080/api/v2/app/version", status=200)

    # reset global _session
    import helm.core.qbittorrent_client

    helm.core.qbittorrent_client._session = None

    session = login_qbittorrent()
    assert session is not None


@responses.activate
def test_add_magnet(monkeypatch):
    monkeypatch.setenv("QB_PASSWORD", "testpass")
    responses.add(responses.GET, "http://localhost:8080/api/v2/app/version", status=200)
    responses.add(responses.POST, "http://localhost:8080/api/v2/torrents/add", status=200)

    import helm.core.qbittorrent_client

    helm.core.qbittorrent_client._session = None

    add_magnet("magnet:?xt=urn:btih:12345")
