"""qBittorrent nova3 plugin API compatibility, using small hand-written plugins."""

import gzip
import os
import ssl
import sys
import tempfile
import textwrap

import pytest
import responses

from helm.search import lite_plugin_loader, plugin_download
from helm.search.rss_fetcher import TorrentItem

sys.path.insert(0, lite_plugin_loader.BUNDLED_PLUGIN_DIR)
import helpers  # noqa: E402
import novaprinter  # noqa: E402

TORRENT = b"d4:infod4:name1:a12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"6:lengthi1eee"


# ------------------------------------------------------------------ helpers


@responses.activate
def test_retrieve_url_second_positional_argument_is_custom_headers():
    responses.add(responses.GET, "https://site.test/s", body="ok")
    assert helpers.retrieve_url("https://site.test/s", {"Cookie": "a=b"}) == "ok"
    sent = responses.calls[0].request.headers
    assert sent["Cookie"] == "a=b" and sent["User-Agent"] == helpers.headers["User-Agent"]


@responses.activate
def test_retrieve_url_posts_form_data():
    responses.add(responses.POST, "https://site.test/api", body="posted")
    assert helpers.retrieve_url("https://site.test/api", {}, b"q=ubuntu") == "posted"
    request = responses.calls[0].request
    assert request.body == b"q=ubuntu" and request.headers["Content-Type"] == "application/x-www-form-urlencoded"


@responses.activate
def test_retrieve_url_unescapes_by_default_and_honours_the_flag():
    responses.add(responses.GET, "https://site.test/", body="a &amp; b &lt;c&gt;")
    assert helpers.retrieve_url("https://site.test/") == "a & b <c>"
    assert helpers.retrieve_url("https://site.test/", unescape_html_entities=False) == "a &amp; b &lt;c&gt;"


@responses.activate
def test_retrieve_url_decodes_charset_and_bare_gzip():
    responses.add(
        responses.GET, "https://site.test/l1", body="café".encode("latin-1"), content_type="text/html; charset=latin-1"
    )
    responses.add(responses.GET, "https://site.test/gz", body=gzip.compress("zipped é".encode()))
    assert helpers.retrieve_url("https://site.test/l1") == "café"
    assert helpers.retrieve_url("https://site.test/gz") == "zipped é"


@responses.activate
def test_retrieve_url_accepts_an_unverified_ssl_context():
    responses.add(responses.GET, "https://self-signed.test/", body="ok")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    assert helpers.retrieve_url("https://self-signed.test/", ssl_context=context) == "ok"
    assert helpers._verify(context) is False and helpers._verify(None) is True


@responses.activate
def test_download_file_returns_path_and_url_and_sends_referer():
    responses.add(responses.GET, "https://site.test/a.torrent", body=TORRENT)
    path, url = helpers.download_file("https://site.test/a.torrent", "https://site.test/details").split(" ", 1)
    try:
        assert url == "https://site.test/a.torrent"
        with open(path, "rb") as stream:
            assert stream.read() == TORRENT
        assert responses.calls[0].request.headers["Referer"] == "https://site.test/details"
    finally:
        os.remove(path)


def test_old_and_new_header_names_and_socks_proxy(monkeypatch):
    assert helpers.headers is helpers._headers and "Mozilla" in helpers.getBrowserUserAgent()
    assert helpers.htmlentitydecode("&amp;") == "&"
    monkeypatch.setenv("qbt_socks_proxy", "socks5h://127.0.0.1:1080")
    helpers.enable_socks_proxy(True)
    assert helpers._proxies == {"http": "socks5h://127.0.0.1:1080", "https": "socks5h://127.0.0.1:1080"}
    helpers.enable_socks_proxy(False)
    assert helpers._proxies == {}


# ------------------------------------------------------------------ novaprinter


@pytest.mark.parametrize(
    "size, expected",
    [
        ("1.5 GB", 1610612736),
        ("1.5 GiB", 1610612736),
        ("700 MiB", 734003200),
        ("2.1 gb", 2254857830),
        ("1,234.5 MB", 1294467072),
        ("12 KiB", 12288),
        ("3 TiB", 3298534883328),
        ("1234567", 1234567),
        (1234567, 1234567),
        (1.5e9, 1500000000),
        ("-1", -1),
        ("unknown", -1),
    ],
)
def test_any_size_to_bytes_matches_qbittorrent(size, expected):
    assert novaprinter.anySizeToBytes(size) == expected


def test_pretty_printer_keeps_every_size_form_and_the_date():
    novaprinter.get_results().clear()
    for size in ("1.5 GiB", "1,234 MB", 1234567, 1.5e9, "lots"):
        novaprinter.prettyPrinter(
            {"link": "magnet:?x", "name": "a", "size": size, "seeds": "1,204", "leech": None, "engine_url": "e",
             "desc_link": "https://site.test/t/1", "pub_date": 1696870394}
        )  # fmt: skip
    items = novaprinter.get_results()
    assert [i.size for i in items] == [1610612736, 1293942784, 1234567, 1500000000, 0]
    assert {i.pubdate for i in items} == {"2023-10-09"}
    assert {i.seeders for i in items} == {1204} and {i.leechers for i in items} == {-1}
    assert {i.desc_link for i in items} == {"https://site.test/t/1"}
    novaprinter.get_results().clear()


def test_pretty_printer_without_a_date_and_search_results_type():
    novaprinter.get_results().clear()
    novaprinter.prettyPrinter(novaprinter.SearchResults(link="magnet:?x", name="b", size="1 MB", seeds=1, leech=0))
    (item,) = novaprinter.get_results()
    assert item.pubdate is None and item.size == 1048576
    novaprinter.get_results().clear()


# ------------------------------------------------------------------ community-style plugins

COMMUNITY_PLUGIN = """
# VERSION: 1.0
from helpers import download_file, headers, retrieve_url
from novaprinter import SearchResults, anySizeToBytes, prettyPrinter


class community(object):
    url = "https://community.test"
    name = "Community"
    supported_categories = {"all": "0", "movies": "1"}

    def search(self, what, cat="all"):
        result: SearchResults = {
            "link": self.url + "/details/" + what,
            "name": "Seen " + what,
            "size": str(anySizeToBytes("1.5 GiB")),
            "seeds": 7,
            "leech": 2,
            "engine_url": self.url,
            "desc_link": self.url + "/details/" + what,
            "pub_date": 1696870394,
        }
        prettyPrinter(result)

    def download_torrent(self, info):
        print(MAGNET + " " + info)
"""


def write_plugin(directory, name, body, **constants):
    path = directory / f"{name}.py"
    extra = "".join(f"{key} = {value!r}\n" for key, value in constants.items())
    path.write_text(textwrap.dedent(body).replace("# VERSION: 1.0\n", "# VERSION: 1.0\n" + extra, 1))
    return path


def test_community_plugin_loads_gets_an_encoded_query_and_remembers_itself(tmp_path):
    path = write_plugin(tmp_path, "community", COMMUNITY_PLUGIN, MAGNET="magnet:?xt=urn:btih:" + "a" * 40)
    plugin = lite_plugin_loader.load_plugin(str(path))
    (item,) = lite_plugin_loader._execute_plugin(plugin, "spider man & co", "all", path=str(path))
    assert item.title == "Seen spider%20man%20%26%20co"  # nova2 URL-encodes the query
    assert item.size == 1610612736 and item.pubdate == "2023-10-09" and item.indexer == "Community"
    assert item.plugin == str(path) and item.desc_link == "https://community.test/details/spider%20man%20%26%20co"


def test_bundled_plugins_keep_the_raw_query():
    bundled = os.path.join(lite_plugin_loader.BUNDLED_PLUGIN_DIR, "yts.py")
    assert lite_plugin_loader.plugin_query("spider man", bundled) == "spider man"
    assert lite_plugin_loader.plugin_query("spider man", "/home/u/.config/helm/plugins/x.py") == "spider%20man"
    assert lite_plugin_loader.plugin_query("spider man") == "spider man"


def test_plugin_download_returns_the_magnet_it_prints(tmp_path):
    magnet = "magnet:?xt=urn:btih:" + "b" * 40
    path = write_plugin(tmp_path, "community", COMMUNITY_PLUGIN, MAGNET=magnet)
    item = TorrentItem("t", "https://community.test/details/1", 1, plugin=str(path))
    assert plugin_download.resolve(item) == magnet


TORRENT_PLUGIN = """
# VERSION: 1.0
import tempfile


class saver(object):
    url = "https://saver.test"
    name = "Saver"

    def search(self, what, cat="all"):
        pass

    def download_torrent(self, info):
        handle, path = tempfile.mkstemp(suffix=".torrent")
        with open(handle, "wb") as stream:
            stream.write(BODY)
        print(path + " " + info)
"""


def test_plugin_download_accepts_a_torrent_file_and_drops_an_html_one(tmp_path):
    good = write_plugin(tmp_path, "saver", TORRENT_PLUGIN, BODY=TORRENT)
    found = plugin_download.resolve(TorrentItem("t", "https://saver.test/1", 1, plugin=str(good)))
    try:
        assert found and plugin_download.is_plugin_temp_file(found) and open(found, "rb").read() == TORRENT
    finally:
        os.remove(found)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = write_plugin(bad_dir, "saver", TORRENT_PLUGIN, BODY=b"<html>details</html>")
    before = set(os.listdir(tempfile.gettempdir()))
    assert plugin_download.resolve(TorrentItem("t", "https://saver.test/1", 1, plugin=str(bad))) is None
    assert not {f for f in set(os.listdir(tempfile.gettempdir())) - before if f.endswith(".torrent")}


def test_plugin_download_survives_crashes_and_timeouts(tmp_path):
    crash = write_plugin(
        tmp_path, "crash", COMMUNITY_PLUGIN.replace("print(MAGNET", "raise RuntimeError(MAGNET"), MAGNET="x"
    )
    assert plugin_download.resolve(TorrentItem("t", "https://c.test/1", 1, plugin=str(crash))) is None
    slow_dir = tmp_path / "slow"
    slow_dir.mkdir()
    slow = write_plugin(
        slow_dir,
        "slow",
        COMMUNITY_PLUGIN.replace("print(MAGNET", "__import__('time').sleep(30); print(MAGNET"),
        MAGNET="x",
    )
    assert plugin_download.resolve(TorrentItem("t", "https://c.test/1", 1, plugin=str(slow)), timeout=2) is None


def test_magnets_and_plain_results_skip_the_plugin():
    assert not plugin_download.needs_plugin(TorrentItem("t", "magnet:?xt=urn:btih:" + "c" * 40, 1, plugin="/p.py"))
    assert not plugin_download.needs_plugin(TorrentItem("t", "https://x.test/a.torrent", 1))
    assert plugin_download.needs_plugin(TorrentItem("t", "https://x.test/details/1", 1, plugin="/p.py"))


def test_send_magnet_uses_the_plugin_result_and_removes_its_temp_file(monkeypatch):
    from helm.cli import deps
    from helm.cli.commands import session

    handle, path = tempfile.mkstemp(suffix=".torrent")
    os.close(handle)
    sent = []
    monkeypatch.setattr(plugin_download, "resolve", lambda item: path)
    monkeypatch.setattr(deps, "add_magnet", lambda link, tag=None: sent.append(link))
    item = TorrentItem("t", "https://x.test/details/1", 1, plugin="/p.py")
    assert session.send_magnet(item, lite_mode=False, json_mode=True) is True
    assert sent == [path] and not os.path.exists(path)
