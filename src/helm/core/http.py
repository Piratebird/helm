"""Connection reuse within each worker thread without sharing mutable cookies."""

import threading

import requests

_MAX_SOCKETS = 8
_local = threading.local()


def get_shared_session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=_MAX_SOCKETS, pool_maxsize=_MAX_SOCKETS)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _local.session = session
    return session
