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


def login_qbittorrent():
    """
    logs in to qbittorrent web ui and returns a sessio with the fresh SID cookie
    """

    session = requests.Session()
    login_url = f"{QB_WEBUI}/api/v2/auth/login"
    data = {"username": QB_USERNAME, "password": QB_PASSWORD}
    r = session.post(login_url, data=data)

    if r.text != "ok.":
        raise Exception(f"Failed to login to qbittorrent: {r.text}")
    print("Loffed in to qbittorrent web ui !!")
    return session


def add_magnet(magnet):
    """
    adds a magnet link to qbitorrent using a logged-in session
    """
    # IMP: fix later by making the session presistant
    # fresh session everytime we add a magnet link
    session = login_qbittorrent()
    add_url = f"{QB_WEBUI}/api/v2/torrents/add"
    r = session.post(add_url, data={"urls": magnet})

    if r.status_code == 200:
        # add some decorations later like check-mark
        print(f"Magnet added: {magnet}")
    else:
        print(f"Failed to add magnet: {r.status_code} | {r.text}")
