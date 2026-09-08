from helm.core.http import get_shared_session


def retrieve_url(url, request_data=None):
    """
    Fetches the content of a URL. Used by qBittorrent search plugins.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    session = get_shared_session()
    if request_data:
        r = session.post(url, data=request_data, headers=headers, timeout=15)
    else:
        r = session.get(url, headers=headers, timeout=15)
    return r.text


def download_file(url, filename=None):
    """
    Downloads a file to a specific location. Used by qBittorrent search plugins.
    """
    r = get_shared_session().get(url, timeout=15)
    if filename:
        with open(filename, "wb") as f:
            f.write(r.content)
        return filename
    return r.content


def htmlentitydecode(s):
    """
    Decodes HTML entities. Used by qBittorrent search plugins.
    """
    import html

    return html.unescape(s)
