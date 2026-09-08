import requests

_MAX_SOCKETS = 32

_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=_MAX_SOCKETS, pool_maxsize=_MAX_SOCKETS)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def get_shared_session() -> requests.Session:
    """Return the process-wide session so keep-alive connections are reused across fetchers.

    The session is not used for cookies/auth, so sharing it across worker threads is safe.
    """
    return _session
