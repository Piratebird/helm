import os
import xml.etree.ElementTree as ET

import requests


class JackettManager:
    def __init__(self):
        self.url = os.getenv("JACKETT_URL", "http://localhost:9117")
        self.api_key = os.getenv("JACKETT_API_KEY")
        if not self.api_key:
            raise RuntimeError("JACKETT_API_KEY environment variable not set")
        self.password = os.getenv("JACKETT_PASSWORD", "")
        self.session = requests.Session()
        self._authenticate()

    def _authenticate(self):
        # Authenticate to get the session cookie for config endpoints
        auth_url = f"{self.url}/UI/Dashboard"
        self.session.post(auth_url, data={"password": self.password})

    def get_all_indexers(self):
        # Torznab API returns all indexers (configured and unconfigured) in XML
        endpoint = f"{self.url}/api/v2.0/indexers/all/results/torznab/api"
        r = requests.get(endpoint, params={"apikey": self.api_key, "t": "indexers"})
        r.raise_for_status()

        root = ET.fromstring(r.content)
        indexers = []
        for idx in root.findall('indexer'):
            indexers.append({
                "id": idx.get("id"),
                "configured": idx.get("configured") == "true",
                "title": idx.find("title").text if idx.find("title") is not None else idx.get("id"),
                "type": idx.find("type").text if idx.find("type") is not None else "unknown"
            })
        return indexers

    def add_indexer(self, indexer_id):
        # 1. Fetch default config schema
        config_url = f"{self.url}/api/v2.0/indexers/{indexer_id}/config"
        r = self.session.get(config_url)
        r.raise_for_status()
        config_payload = r.json()

        # 2. Submit the config back
        r_post = self.session.post(config_url, json=config_payload)
        r_post.raise_for_status()
        return True

    def remove_indexer(self, indexer_id):
        endpoint = f"{self.url}/api/v2.0/indexers/{indexer_id}"
        r = self.session.delete(endpoint)
        r.raise_for_status()
        return True
