import os
import xml.etree.ElementTree as ET

import requests

from helm.core.logger import get_logger
from helm.core.secret_manager import get_secret

logger = get_logger(__name__)


class JackettManager:
    def __init__(self):
        self.url = os.getenv("JACKETT_URL", "http://localhost:9117")
        self.api_key = get_secret("JACKETT_API_KEY")
        if not self.api_key:
            raise RuntimeError("JACKETT_API_KEY environment variable not set")
        self.password = get_secret("JACKETT_PASSWORD") or ""
        self.session = requests.Session()
        self._authenticate()

    def _authenticate(self):
        # Authenticate to get the session cookie for config endpoints
        auth_url = f"{self.url}/UI/Dashboard"
        self.session.post(auth_url, data={"password": self.password})

    def get_all_indexers(self):
        endpoint = f"{self.url}/api/v2.0/indexers/all/results/torznab/api"
        try:
            r = requests.get(endpoint, params={"apikey": self.api_key, "t": "indexers"}, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            indexers = []
            for idx in root.findall("indexer"):
                indexers.append(
                    {
                        "id": idx.get("id"),
                        "configured": idx.get("configured") == "true",
                        "title": idx.find("title").text if idx.find("title") is not None else idx.get("id"),
                        "type": idx.find("type").text if idx.find("type") is not None else "unknown",
                    }
                )
            return indexers
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("Failed to fetch Jackett indexers: %s", e, exc_info=True)
            return []

    def add_indexer(self, indexer_id):
        config_url = f"{self.url}/api/v2.0/indexers/{indexer_id}/config"
        try:
            r = self.session.get(config_url, timeout=15)
            r.raise_for_status()
            config_payload = r.json()
            r_post = self.session.post(config_url, json=config_payload, timeout=15)
            r_post.raise_for_status()
            return True
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("Failed to add Jackett indexer: %s", e, exc_info=True)
            return False

    def remove_indexer(self, indexer_id):
        endpoint = f"{self.url}/api/v2.0/indexers/{indexer_id}"
        try:
            r = self.session.delete(endpoint, timeout=15)
            r.raise_for_status()
            return True
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("Failed to remove Jackett indexer: %s", e, exc_info=True)
            return False


class ProwlarrManager:
    # Public trackers Helm enables by default when none are configured.
    DEFAULT_SEED_NAMES = ("1337x", "yts", "torrentgalaxy", "nyaapantsu", "eztv")

    def __init__(self):
        self.url = os.getenv("PROWLARR_URL", "http://localhost:19696")
        self.api_key = get_secret("PROWLARR_API_KEY")
        if not self.api_key:
            raise RuntimeError("PROWLARR_API_KEY environment variable not set")
        self.headers = {"X-Api-Key": self.api_key}
        self._definitions = {}

    def _fetch(self, path, timeout=15):
        r = requests.get(f"{self.url}{path}", headers=self.headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def get_all_indexers(self):
        try:
            definitions = self._fetch("/api/v1/indexer/schema", timeout=60)
            active = self._fetch("/api/v1/indexer", timeout=60)
        except Exception as e:
            logger.error("Failed to connect to Prowlarr: %s", e, exc_info=True)
            return []

        # Index resources don't aggressively repeat schema keys, so key the
        # active list by every identifier Prowlarr exposes and match a schema
        # definition against each candidate in order of specificity.
        active_map = {}
        for idx in active:
            for key in (idx.get("definitionName"), idx.get("implementation"), idx.get("name")):
                if key:
                    active_map[key] = idx

        indexers = []
        for d in definitions:
            name = d.get("name")
            impl = d.get("implementation")
            definition_name = d.get("definitionName")

            act = None
            for key in (definition_name, impl, name):
                if key and key in active_map:
                    act = active_map[key]
                    break

            if act:
                is_configured = act.get("enable", False)
                idx_id = str(act.get("id"))
            else:
                is_configured = False
                # definitionName is unique per schema def (unlike implementation,
                # where every Cardigann indexer shares the same implementation).
                idx_id = str(definition_name) if definition_name else str(impl) if impl else str(name)

            self._definitions[idx_id] = d

            indexers.append(
                {
                    "id": idx_id,
                    "configured": is_configured,
                    "title": name,
                    "type": d.get("privacy", "unknown").lower().replace("semiprivate", "semi-private"),
                }
            )
        return indexers

    def add_indexer(self, indexer_id):
        endpoint = f"{self.url}/api/v1/indexer/{indexer_id}"
        try:
            r = requests.get(endpoint, headers=self.headers, timeout=5)
            if r.status_code == 200:
                payload = r.json()
                payload["enable"] = True
                r_put = requests.put(endpoint, json=payload, headers=self.headers, timeout=5)
                r_put.raise_for_status()
                return True
        except Exception as e:
            logger.error("Failed to update Prowlarr indexer: %s", e, exc_info=True)

        if indexer_id in self._definitions:
            payload = dict(self._definitions[indexer_id])
            payload["enable"] = True
            r_post = requests.post(f"{self.url}/api/v1/indexer", json=payload, headers=self.headers, timeout=5)
            r_post.raise_for_status()
            return True

        raise Exception(f"Indexer ID {indexer_id} not found in Prowlarr definitions.")

    def remove_indexer(self, indexer_id):
        endpoint = f"{self.url}/api/v1/indexer/{indexer_id}"
        try:
            r = requests.get(endpoint, headers=self.headers, timeout=5)
            if r.status_code == 200:
                payload = r.json()
                payload["enable"] = False
                r_put = requests.put(endpoint, json=payload, headers=self.headers, timeout=5)
                r_put.raise_for_status()
                return True
        except Exception as e:
            logger.error("Failed to connect to Prowlarr: %s", e, exc_info=True)
        return False

    def seed_default_indexers(self):
        # Enable a small set of well-known public trackers if none are configured.
        try:
            definitions = self._fetch("/api/v1/indexer/schema", timeout=60)
            active = self._fetch("/api/v1/indexer", timeout=60)
        except Exception as e:
            logger.error("Failed to fetch Prowlarr definitions: %s", e, exc_info=True)
            return 0

        active_names = set()
        for idx in active:
            for key in (idx.get("definitionName"), idx.get("implementation"), idx.get("name")):
                if key:
                    active_names.add(key.lower())

        enabled = 0
        for d in definitions:
            name = d.get("name") or ""
            key = d.get("definitionName") or d.get("implementation") or name
            if key in active_names or name in active_names:
                continue
            if key.lower() in self.DEFAULT_SEED_NAMES or name.lower() in self.DEFAULT_SEED_NAMES:
                payload = dict(d)
                payload["enable"] = True
                try:
                    r = requests.post(f"{self.url}/api/v1/indexer", json=payload, headers=self.headers, timeout=5)
                    r.raise_for_status()
                    enabled += 1
                except Exception as e:
                    logger.error("Failed to seed Prowlarr indexer '%s': %s", name, e, exc_info=True)
        return enabled
