"""
the functionality to load magnet links into qbittorrent
i downloaded qbittorrent-nox to access the webui easier for me,
through the package manager for fedora

core/qbittorrent_client.py

"""

import threading

import requests

from helm.config.secret_manager import get_secret
from helm.config.settings import get_qb_url, load_settings
from helm.logger import get_logger
from helm.torrents.torrent_links import resolve_torrent

logger = get_logger(__name__)

# Applied to every Helm job so reconciliation needs one list call per pass.
SHARED_TAG = "helm"

_session = None
_session_key = None
_session_lock = threading.Lock()


def login_qbittorrent(force=False):
    """
    logs in to qbittorrent web ui and returns a sessio with the fresh SID cookie
    """
    global _session, _session_key
    qb_webui = get_qb_url()
    qb_username = load_settings().get("services.qbittorrent_user")
    qb_password = get_secret("QB_PASSWORD")
    key = (qb_webui, qb_username, qb_password)
    with _session_lock:
        if _session is not None and _session_key == key and not force:
            return _session
        if not qb_password:
            raise Exception("Please set QB_PASSWORD in .env file !!")

        session = requests.Session()

        # Check if auth is already bypassed (e.g. via AuthSubnetWhitelist)
        version_url = f"{qb_webui}/api/v2/app/version"
        try:
            r_test = session.get(version_url, timeout=10)
            if r_test.status_code == 200:
                logger.info("qBittorrent subnet authentication accepted")
                _session, _session_key = session, key
                return session
        except Exception:
            pass

        login_url = f"{qb_webui}/api/v2/auth/login"
        data = {"username": qb_username, "password": qb_password}
        r = session.post(login_url, data=data, timeout=10)

        if r.status_code == 200 or r.status_code == 204:
            # Verify authentication by making a secondary request
            try:
                r_verify = session.get(version_url, timeout=10)
                if r_verify.status_code != 200:
                    raise ConnectionError("Secondary verification request failed, authentication was not successful.")
            except Exception as e:
                # catching errors like pokemons
                raise ConnectionError(f"Failed to verify qbittorrent authentication: {e}")  # noqa: B904
        else:
            raise ConnectionError(f"Failed to login to qbittorrent: HTTP {r.status_code} {r.text}")

        logger.info("qBittorrent authentication accepted")
        _session, _session_key = session, key
        return session


def qb_request(method, path, **kwargs):
    """Call the Web API, logging in again once if the SID cookie has expired."""
    kwargs.setdefault("timeout", 15)
    url = get_qb_url() + path
    response = login_qbittorrent().request(method, url, **kwargs)
    if response.status_code == 403:
        response = login_qbittorrent(force=True).request(method, url, **kwargs)
    response.raise_for_status()
    return response


def add_magnet(magnet, save_path=None, tag=None):
    """Resolve a link or local metadata and submit it through qBittorrent's API."""
    source = resolve_torrent(magnet)
    data = {}
    if save_path:
        data["savepath"] = save_path
    if tag:
        data["tags"] = tag
    if source.data:
        files = {"torrents": ("download.torrent", source.data, "application/x-bittorrent")}
        response = qb_request("POST", "/api/v2/torrents/add", data=data, files=files)
    else:
        data["urls"] = source.magnet
        response = qb_request("POST", "/api/v2/torrents/add", data=data)
    if response.text.strip().lower() in ("fails.", "fail", "error"):
        raise RuntimeError("qBittorrent rejected torrent metadata")
    logger.info("Torrent accepted by qBittorrent")
    return True


def tagged_torrents(tag=SHARED_TAG):
    return qb_request("GET", "/api/v2/torrents/info", params={"tag": tag}).json()


def delete_tagged(tag):
    """Remove a tag's torrents, keeping any data already written to disk."""
    hashes = [torrent["hash"] for torrent in tagged_torrents(tag)]
    if hashes:
        qb_request("POST", "/api/v2/torrents/delete", data={"hashes": "|".join(hashes), "deleteFiles": "false"})
