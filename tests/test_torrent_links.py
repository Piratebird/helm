"""Link resolution, exercised through a real local HTTP server."""

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from helm.core import torrent_links
from helm.core.torrent_links import (
    TorrentSource,
    decode_metadata,
    info_hash,
    metadata_source,
    resolve_torrent,
    valid_files,
    validate_magnet,
)

HASH = "a" * 40
MAGNET = "magnet:?xt=urn:btih:" + HASH
INFO = b"d6:lengthi5e4:name4:file12:piece lengthi16384e6:pieces20:" + b"p" * 20 + b"e"
TORRENT = b"d8:announce14:http://t.local4:info" + INFO + b"e"


class Routes(BaseHTTPRequestHandler):
    routes: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        status, headers, body = self.routes[self.path]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Routes)
    Routes.routes = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize(
    "value",
    [MAGNET, "magnet:?xt=urn:btih:" + "A" * 32, "magnet:?xt=urn:btmh:1220" + "b" * 64],
)
def test_validate_magnet_accepts_supported_hashes(value):
    assert validate_magnet(value) == value


@pytest.mark.parametrize(
    "value",
    ["http://x", "magnet:?dn=x", "magnet:?xt=urn:btih:12345", "magnet:?xt=urn:btih:" + "a" * 70000],
)
def test_validate_magnet_rejects_invalid(value):
    with pytest.raises(ValueError):
        validate_magnet(value)


def test_metadata_hash_uses_exact_info_bytes():
    source = metadata_source(TORRENT)
    assert hashlib.sha1(INFO).hexdigest() in source.magnet
    assert "dn=file&" in source.magnet
    assert "tr=http%3A%2F%2Ft.local" in source.magnet
    assert source.data == TORRENT


def test_v2_and_hybrid_metadata_produce_their_topics():
    v2 = b"4:name1:x12:meta versioni2e12:piece lengthi16384e"
    source = metadata_source(b"d4:infod" + v2 + b"ee")
    assert "urn:btmh:1220" in source.magnet and "urn:btih:" not in source.magnet
    hybrid = metadata_source(
        b"d4:infod6:lengthi1e4:name1:x12:meta versioni2e12:piece lengthi16384e6:pieces20:" + b"p" * 20 + b"ee"
    )
    assert "urn:btmh:1220" in hybrid.magnet and "urn:btih:" in hybrid.magnet


def test_trackers_are_deduplicated_across_announce_list():
    data = b"d8:announce3:u:113:announce-listll3:u:1el3:u:2ee4:info" + INFO + b"e"
    assert metadata_source(data).magnet.count("tr=") == 2


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"x",
        b"i1e",
        b"d4:infoi1ee",
        b"d4:infod",
        TORRENT + b"junk",
        b"l" * 100,
        b"di01e1:xe",
        b"d1:ai-0ee",
        b"d01:ai1ee",
        b"d1:ai1e1:ai2ee",
        b"d999:xe",
        b"d4:infod4:name1:x12:piece lengthi0e6:pieces20:" + b"p" * 20 + b"ee",
        b"d4:infod4:name1:x12:piece lengthi1eee",
        b"d4:infod6:lengthi1e4:name1:x12:piece lengthi1e6:pieces3:abcee",
        b"d4:infod4:name1:x12:piece lengthi1e6:pieces20:" + b"p" * 20 + b"ee",
        b"d4:infod6:lengthi-1e4:name1:x12:piece lengthi1e6:pieces20:" + b"p" * 20 + b"ee",
        b"d4:infod5:filesl"
        + b"d6:lengthi1e4:pathl2:..ee"
        + b"e4:name1:x12:piece lengthi1e6:pieces20:"
        + b"p" * 20
        + b"ee",
    ],
)
def test_malformed_metadata_is_rejected(data):
    with pytest.raises(ValueError):
        decode_metadata(data)


def test_oversized_metadata_is_rejected():
    with pytest.raises(ValueError, match="8 MiB"):
        decode_metadata(b"d" * (torrent_links.MAX_METADATA + 1))


@pytest.mark.parametrize(
    "files,expected",
    [
        ([{b"length": 1, b"path": [b"a", b"b"]}], True),
        ([], False),
        (None, False),
        (["x"], False),
        ([{b"length": -1, b"path": [b"a"]}], False),
        ([{b"length": 1, b"path": []}], False),
        ([{b"length": 1, b"path": [b"a/b"]}], False),
        ([{b"length": 1, b"path": [b""]}], False),
    ],
)
def test_valid_files(files, expected):
    assert valid_files(files) is expected


def test_info_hash_forms():
    assert info_hash(MAGNET) == HASH
    assert info_hash("magnet:?xt=urn:btih:" + "A" * 32) == "00" * 20
    assert info_hash("magnet:?xt=urn:btmh:1220" + "B" * 64) == "b" * 64
    with pytest.raises(ValueError):
        info_hash("magnet:?dn=x")


def test_resolve_passes_through_sources_and_magnets():
    source = TorrentSource(MAGNET)
    assert resolve_torrent(source) is source
    assert resolve_torrent("  " + MAGNET + " ").magnet == MAGNET


def test_resolve_local_torrent(tmp_path):
    path = tmp_path / "a.torrent"
    path.write_bytes(TORRENT)
    assert resolve_torrent(str(path)).data == TORRENT
    with pytest.raises(ValueError, match="existing"):
        resolve_torrent(str(tmp_path / "missing.torrent"))
    with pytest.raises(ValueError):
        resolve_torrent(str(path), allow_local=False)


@pytest.mark.parametrize("value", ["file:///etc/passwd", "ftp://example.test/x.torrent"])
def test_unsupported_schemes_are_rejected(value):
    with pytest.raises(ValueError, match="supported"):
        resolve_torrent(value)


def test_direct_torrent_download(site):
    Routes.routes["/a.torrent"] = (200, {"Content-Type": "application/x-bittorrent"}, TORRENT)
    assert resolve_torrent(site + "/a.torrent").data == TORRENT


def test_detail_page_prefers_magnet(site):
    Routes.routes["/d"] = (200, {}, f'<a href="/a.torrent">t</a><a href="{MAGNET}">m</a>'.encode())
    assert resolve_torrent(site + "/d").magnet == MAGNET


def test_detail_page_follows_relative_torrent_anchor(site):
    Routes.routes["/x/d"] = (200, {}, b'<a href="/nope">n</a><a href="a.TORRENT?k=1">t</a>')
    Routes.routes["/x/a.TORRENT?k=1"] = (200, {}, TORRENT)
    assert resolve_torrent(site + "/x/d").data == TORRENT


def test_redirect_to_magnet_and_to_relative_location(site):
    Routes.routes["/m"] = (302, {"Location": MAGNET}, b"")
    assert resolve_torrent(site + "/m").magnet == MAGNET
    Routes.routes["/r"] = (301, {"Location": "/a.torrent"}, b"")
    Routes.routes["/a.torrent"] = (200, {}, TORRENT)
    assert resolve_torrent(site + "/r").data == TORRENT


def test_redirect_failures(site):
    Routes.routes["/a"] = (302, {"Location": "/b"}, b"")
    Routes.routes["/b"] = (302, {"Location": "/a"}, b"")
    with pytest.raises(ValueError, match="loop"):
        resolve_torrent(site + "/a")
    Routes.routes["/none"] = (302, {}, b"")
    with pytest.raises(ValueError, match="Location"):
        resolve_torrent(site + "/none")
    Routes.routes["/file"] = (302, {"Location": "file:///etc/passwd"}, b"")
    with pytest.raises(ValueError, match="supported"):
        resolve_torrent(site + "/file")


def test_hop_limit(site):
    for index in range(8):
        Routes.routes[f"/{index}"] = (302, {"Location": f"/{index + 1}"}, b"")
    with pytest.raises(ValueError, match="limit"):
        resolve_torrent(site + "/0")


def test_http_error_is_raised(site):
    Routes.routes["/gone"] = (404, {}, b"")
    with pytest.raises(Exception, match="404"):
        resolve_torrent(site + "/gone")


def test_page_without_links_is_rejected(site):
    Routes.routes["/empty"] = (200, {}, b"<html>nothing</html>")
    with pytest.raises(ValueError, match="no magnet"):
        resolve_torrent(site + "/empty")


def test_invalid_declared_charset_does_not_crash(site):
    Routes.routes["/d"] = (
        200,
        {"Content-Type": "text/html; charset=not-a-charset"},
        f'<a href="{MAGNET}">m</a>'.encode(),
    )
    assert resolve_torrent(site + "/d").magnet == MAGNET


def test_text_page_starting_with_d_still_parses_as_html(site):
    Routes.routes["/d"] = (200, {}, f'download here <a href="{MAGNET}">m</a>'.encode())
    assert resolve_torrent(site + "/d").magnet == MAGNET


def test_response_size_limit(site, monkeypatch):
    monkeypatch.setattr(torrent_links, "MAX_METADATA", 1024)
    Routes.routes["/big"] = (200, {}, b"x" * 200000)
    with pytest.raises(ValueError, match="exceeds"):
        resolve_torrent(site + "/big")


def test_exhausted_deadline(site):
    with pytest.raises(TimeoutError):
        resolve_torrent(site + "/any", timeout=0)
