"""
the functionality to load magnet links into qbittorrent
i downloaded qbittorrent-nox to access the webui easier for me,
through the package manager for fedora

core/qbittorrent_client.py

"""

import requests
import os
from dotenv import load_dotenv

load_dotenv()

# reading from .env fallback to the second parameter
QB_WEBUI = os.getenv("QB_WEBUI", "http://localhost:8080")
QB_USERNAME = os.getenv("QB_USERNAME")
QB_PASSWORD = os.getenv("QB_PASSWORD")

if not QB_USERNAME or not QB_PASSWORD:
    #####IMP!!######
    # later add functionality for user input rather than .env
    raise Exception("Please set QB_USERNAME and QB_PASSWORD in .env file !!")


_session = None

def login_qbittorrent():
    """
    logs in to qbittorrent web ui and returns a sessio with the fresh SID cookie
    """
    global _session
    if _session is not None:
        return _session

    session = requests.Session()
    login_url = f"{QB_WEBUI}/api/v2/auth/login"
    data = {"username": QB_USERNAME, "password": QB_PASSWORD}
    r = session.post(login_url, data=data)

    if r.status_code == 200:
        if r.text.strip().lower() != "ok." and "SID" not in session.cookies:
            raise Exception(f"Failed to login to qbittorrent: {r.text}")
    elif r.status_code != 204:
        raise Exception(f"Failed to login to qbittorrent: HTTP {r.status_code} {r.text}")
    
    print("Logged in to qbittorrent web ui !!")
    _session = session
    return session


def add_magnet(magnet):
    """
    adds a magnet link to qbitorrent using a logged-in session
    """
    session = login_qbittorrent()
    add_url = f"{QB_WEBUI}/api/v2/torrents/add"
    r = session.post(add_url, data={"urls": magnet})

    if r.status_code in (200, 202):
        # add some decorations later like check-mark
        print(f"Magnet added: {magnet}")
    else:
        print(f"Failed to add magnet: {r.status_code} | {r.text}")
