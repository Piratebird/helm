"""Helpers for qBittorrent search plugins, with qBittorrent's nova3 signatures.

Plugins written for qBittorrent import ``retrieve_url``, ``download_file``,
``headers`` (older plugins) or ``_headers``, ``htmlentitydecode`` and
``enable_socks_proxy`` from here. Requests go through Helm's shared session,
and failures raise instead of returning "", so the search reports which source
failed.
"""

import gzip
import html
import os
import ssl
import tempfile
from typing import Dict

from helm.net.http import get_shared_session

TIMEOUT = 15


def _getBrowserUserAgent():
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"


getBrowserUserAgent = _getBrowserUserAgent

# Old plugins import ``headers``, current ones ``_headers``; both are the same dict.
_headers = {"User-Agent": _getBrowserUserAgent()}
headers = _headers

# Kept for old plugins; current qBittorrent unescapes in retrieve_url instead.
htmlentitydecode = html.unescape

_proxies: Dict[str, str] = {}


def enable_socks_proxy(enable):
    """Route plugin requests through ``qbt_socks_proxy`` (for example socks5h://host:1080) when set."""
    _proxies.clear()
    url = os.environ.get("qbt_socks_proxy")
    if enable and url:
        _proxies.update({"http": url, "https": url})


def _verify(ssl_context):
    # Plugins pass an unverified context to accept a site's bad certificate.
    if isinstance(ssl_context, ssl.SSLContext) and ssl_context.verify_mode == ssl.CERT_NONE:
        return False
    return True


def _body(response):
    data = response.content
    # Some sites send gzip without a Content-Encoding header.
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    return data


def _decode(response):
    content_type = response.headers.get("Content-Type", "")
    charset = content_type.split("charset=", 1)[1].split(";")[0].strip() if "charset=" in content_type else "utf-8"
    try:
        return _body(response).decode(charset, "replace")
    except LookupError:
        return _body(response).decode("utf-8", "replace")


def retrieve_url(url, custom_headers={}, request_data=None, ssl_context=None, unescape_html_entities=True):  # noqa: B006
    """Return the page at *url* as text, like qBittorrent's helper.

    *custom_headers* are added to the browser headers, *request_data* makes it a
    POST, and HTML entities are unescaped unless *unescape_html_entities* is false.
    """
    request_headers = {**_headers, **(custom_headers or {})}
    session = get_shared_session()
    options = {"headers": request_headers, "timeout": TIMEOUT, "verify": _verify(ssl_context)}
    if _proxies:
        options["proxies"] = dict(_proxies)
    if request_data is not None:
        if not any(key.lower() == "content-type" for key in request_headers):
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        response = session.post(url, data=request_data, **options)
    else:
        response = session.get(url, **options)
    response.raise_for_status()
    text = _decode(response)
    return html.unescape(text) if unescape_html_entities else text


def download_file(url, referer=None, ssl_context=None):
    """Save *url* to a temporary file and return "<path> <url>", like qBittorrent's helper."""
    request_headers = dict(_headers)
    if referer is not None:
        request_headers["Referer"] = referer
    options = {"headers": request_headers, "timeout": TIMEOUT, "verify": _verify(ssl_context)}
    if _proxies:
        options["proxies"] = dict(_proxies)
    response = get_shared_session().get(url, **options)
    response.raise_for_status()
    handle, path = tempfile.mkstemp(suffix=".torrent")
    with os.fdopen(handle, "wb") as stream:
        stream.write(_body(response))
    return f"{path} {url}"


def validate_html(html_text, markers):
    lower = html_text.lower()
    if any(text in lower for text in ("just a moment", "cf-chl-", "captcha")):
        raise ValueError("Source returned a challenge page")
    if not any(marker.lower() in lower for marker in markers) and not any(
        marker in lower for marker in ("no results", "nothing found", "0 results", "no torrents")
    ):
        raise ValueError("Unrecognized search result page")
