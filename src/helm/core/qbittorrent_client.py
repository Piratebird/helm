"""
the functionality to load magnet links into qbittorrent
i downloaded qbittorrent-nox to access the webui easier for me,
through the package manager for fedora

core/qbittorrent_client.py

"""

import requests
import os


_session = None

def login_qbittorrent():
    """
    logs in to qbittorrent web ui and returns a sessio with the fresh SID cookie
    """
    global _session
    if _session is not None:
        return _session

    qb_webui = os.getenv("QB_WEBUI", "http://localhost:8080")
    qb_username = os.getenv("QB_USERNAME", "admin")
    qb_password = os.getenv("QB_PASSWORD")
    if not qb_password:
        raise Exception("Please set QB_PASSWORD in .env file !!")

    session = requests.Session()
    
    # Check if auth is already bypassed (e.g. via AuthSubnetWhitelist)
    version_url = f"{qb_webui}/api/v2/app/version"
    try:
        r_test = session.get(version_url)
        if r_test.status_code == 200:
            print("Auth bypassed via subnet whitelist !!")
            _session = session
            return session
    except Exception:
        pass
    
    login_url = f"{qb_webui}/api/v2/auth/login"
    data = {"username": qb_username, "password": qb_password}
    r = session.post(login_url, data=data)

    if r.status_code == 200 or r.status_code == 204:
        # Verify authentication by making a secondary request
        try:
            r_verify = session.get(version_url)
            if r_verify.status_code != 200:
                raise ConnectionError("Secondary verification request failed, authentication was not successful.")
        except Exception as e:
            # catching errors like pokemons 
            raise ConnectionError(f"Failed to verify qbittorrent authentication: {e}")
    else:
        raise ConnectionError(f"Failed to login to qbittorrent: HTTP {r.status_code} {r.text}")
    
    print("Logged in to qbittorrent web ui !!")
    _session = session
    return session


def add_magnet(magnet):
    """
    adds a magnet link to qbitorrent using a logged-in session
    """
    qb_webui = os.getenv("QB_WEBUI", "http://localhost:8080")
    session = login_qbittorrent()
    add_url = f"{qb_webui}/api/v2/torrents/add"
    r = session.post(add_url, data={"urls": magnet})

    if r.status_code in (200, 202):
        # add some decorations later like check-mark
        print(f"Magnet added: {magnet}")
    elif r.status_code == 409:
        print(f"\033[33mMagnet is already in your qBittorrent download list!\033[0m")
    else:
        print(f"Failed to add magnet: {r.status_code} | {r.text}")
