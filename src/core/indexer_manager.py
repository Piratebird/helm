import os
import requests

def get_jackett_url_and_api():
    jackett_url = os.getenv("JACKETT_URL", "http://localhost:9117")
    api_key = os.getenv("JACKETT_API_KEY")
    if not api_key:
        raise RuntimeError("JACKETT_API_KEY environment variable not set")
    return jackett_url, api_key

def list_configured_indexers():
    url, api_key = get_jackett_url_and_api()
    endpoint = f"{url}/api/v2.0/indexers/configured"
    try:
        r = requests.get(endpoint, params={"apikey": api_key}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Failed to list configured indexers: {e}")
        return []

def list_unconfigured_indexers():
    url, api_key = get_jackett_url_and_api()
    endpoint = f"{url}/api/v2.0/indexers/unconfigured"
    try:
        r = requests.get(endpoint, params={"apikey": api_key}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Failed to list unconfigured indexers: {e}")
        return []
