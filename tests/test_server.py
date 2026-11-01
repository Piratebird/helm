"""The HTTP API, spoken to over a real socket."""

import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import requests

import helm.core.server as server_module
from helm.core.config_manager import get_dl_dir
from helm.core.jobs import JobStore
from helm.core.secret_manager import get_secret
from helm.core.server import byte_range, is_loopback, make_server, run_server

TOKEN = "t" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setenv("HELM_DL_DIR", str(root))
    return root


def launch(tmp_path, read_only=False):
    store = JobStore(tmp_path / "state" / "jobs.sqlite3")
    server, _ = make_server("127.0.0.1", 0, store=store, token=TOKEN, lite=True, read_only=read_only)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, store


@pytest.fixture
def api(tmp_path, downloads):
    server, thread, store = launch(tmp_path)
    yield f"http://127.0.0.1:{server.server_port}", store
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def read_only_api(tmp_path, downloads):
    server, thread, store = launch(tmp_path, read_only=True)
    yield f"http://127.0.0.1:{server.server_port}", store
    server.shutdown()
    server.server_close()
    thread.join()


def add_file(store, name="movie.bin", content=b"0123456789"):
    path = Path(get_dl_dir()) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    job = store.enqueue("magnet:?xt=urn:btih:" + "f" * 40, lite=True)
    store.register_files(job["id"], [path])
    return path


@pytest.mark.parametrize(
    "header,size,expected",
    [
        (None, 10, (0, 9, False)),
        (None, 0, (0, 0, False)),
        ("bytes=0-", 10, (0, 9, True)),
        ("bytes=2-4", 10, (2, 4, True)),
        ("bytes=8-100", 10, (8, 9, True)),
        ("bytes=-3", 10, (7, 9, True)),
        ("bytes=-30", 10, (0, 9, True)),
    ],
)
def test_byte_range(header, size, expected):
    assert byte_range(header, size) == expected


@pytest.mark.parametrize(
    "header,size", [("bytes=10-", 10), ("bytes=5-2", 10), ("bytes=-0", 10), ("x", 10), ("bytes=0-", 0), ("bytes=-", 10)]
)
def test_invalid_byte_range(header, size):
    with pytest.raises(ValueError):
        byte_range(header, size)


@pytest.mark.parametrize(
    "host,expected",
    [("127.0.0.1", True), ("::1", True), ("localhost", True), ("0.0.0.0", False), ("example.test", False)],
)
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


@pytest.mark.parametrize("path", ["/health", "/jobs", "/files", "/files/x", "/anything"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic " + TOKEN}])
def test_every_route_requires_the_token(api, path, headers):
    assert requests.get(api[0] + path, headers=headers).status_code == 401


def test_health_and_unknown_route(api):
    assert requests.get(api[0] + "/health", headers=AUTH).json() == {"status": "ok", "read_only": False}
    assert requests.get(api[0] + "/nope", headers=AUTH).status_code == 404


def test_submit_list_get_and_cancel_job(api):
    base, store = api
    created = requests.post(base + "/jobs", headers=AUTH, json={"source": MAGNET})
    assert created.status_code == 202
    identifier = created.json()["id"]
    assert store.get(identifier)["lite"] == 1
    assert [job["id"] for job in requests.get(base + "/jobs", headers=AUTH).json()] == [identifier]
    assert requests.get(f"{base}/jobs/{identifier}", headers=AUTH).json()["status"] == "pending"
    assert requests.get(base + "/jobs/" + "0" * 32, headers=AUTH).status_code == 404
    assert requests.delete(f"{base}/jobs/{identifier}", headers=AUTH).json() == {"cancelled": True}
    assert requests.delete(f"{base}/jobs/{identifier}", headers=AUTH).status_code == 409
    assert requests.delete(base + "/jobs/" + "0" * 32, headers=AUTH).status_code == 404
    assert requests.delete(base + "/jobs/not-an-id", headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"source": 1}',
        b'{"source": "/etc/passwd"}',
        b'{"source": "a.torrent"}',
        b'{"source": "http://"}',
        b'{"source": "magnet:?xt=urn:btih:12345"}',
        b"\xff\xfe",
        b"[" * 60000,
    ],
)
def test_invalid_submissions_are_rejected(api, body):
    base, store = api
    response = requests.post(base + "/jobs", headers={**AUTH, "Content-Type": "application/json"}, data=body)
    assert response.status_code == 400
    assert store.list() == []


def test_body_limits_and_content_type(api):
    base, _ = api
    json_type = {**AUTH, "Content-Type": "application/json"}
    assert requests.post(base + "/jobs", headers=json_type, data=b"x" * 65537).status_code == 413
    assert requests.post(base + "/jobs", headers=json_type, data=b"").status_code == 413
    response = requests.post(base + "/jobs", headers={**AUTH, "Content-Type": "text/plain"}, data=b"{}")
    assert response.status_code == 400
    assert requests.post(base + "/other", headers=json_type, data=b"{}").status_code == 404


def test_rejected_body_cannot_become_a_second_request(api):
    """An unread body on a kept-alive connection would otherwise parse as a request."""
    base, _ = api
    host, port = base.rsplit("//", 1)[1].split(":")
    smuggled = f"GET /health HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n\r\n".encode()
    head = (
        f"POST /jobs HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
        f"Content-Type: text/plain\r\nContent-Length: {len(smuggled)}\r\n\r\n"
    ).encode()
    with socket.create_connection((host, int(port)), timeout=5) as connection:
        connection.sendall(head + smuggled)
        received = b""
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            received += chunk
    assert received.count(b"HTTP/1.") == 1
    assert b" 400 " in received


def test_read_only_server_refuses_changes(read_only_api):
    base, store = read_only_api
    assert requests.post(base + "/jobs", headers=AUTH, json={"source": MAGNET}).status_code == 403
    job = store.enqueue(MAGNET)
    assert requests.delete(f"{base}/jobs/{job['id']}", headers=AUTH).status_code == 403
    assert store.get(job["id"])["status"] == "pending"
    assert requests.get(base + "/health", headers=AUTH).json()["read_only"] is True


def test_stream_full_file_and_catalog(api):
    base, store = api
    add_file(store, "sub dir/movie.bin")
    assert [item["path"] for item in requests.get(base + "/files", headers=AUTH).json()] == ["sub dir/movie.bin"]
    response = requests.get(base + "/files/sub%20dir/movie.bin", headers=AUTH)
    assert response.status_code == 200 and response.content == b"0123456789"
    assert response.headers["Accept-Ranges"] == "bytes"


def test_large_file_arrives_intact(api):
    base, store = api
    content = bytes(range(256)) * 40000
    add_file(store, "large.bin", content)
    assert requests.get(base + "/files/large.bin", headers=AUTH).content == content
    middle = requests.get(base + "/files/large.bin", headers={**AUTH, "Range": "bytes=5000000-5999999"})
    assert middle.content == content[5000000:6000000]


@pytest.mark.parametrize(
    "header,body,content_range",
    [("bytes=2-4", b"234", "bytes 2-4/10"), ("bytes=-2", b"89", "bytes 8-9/10"), ("bytes=8-", b"89", "bytes 8-9/10")],
)
def test_range_requests(api, header, body, content_range):
    base, store = api
    add_file(store)
    response = requests.get(base + "/files/movie.bin", headers={**AUTH, "Range": header})
    assert response.status_code == 206
    assert response.content == body
    assert response.headers["Content-Range"] == content_range


def test_unsatisfiable_range(api):
    base, store = api
    add_file(store)
    response = requests.get(base + "/files/movie.bin", headers={**AUTH, "Range": "bytes=50-"})
    assert response.status_code == 416
    assert response.headers["Content-Range"] == "bytes */10"


def test_keep_alive_survives_range_requests(api):
    base, store = api
    add_file(store)
    host, port = base.rsplit("//", 1)[1].split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=5)
    for _ in range(3):
        connection.request("GET", "/files/movie.bin", headers={**AUTH, "Range": "bytes=0-1"})
        assert connection.getresponse().read() == b"01"
    connection.close()


def test_head_has_headers_and_no_body(api):
    base, store = api
    add_file(store)
    response = requests.head(base + "/files/movie.bin", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["Content-Length"] == "10"
    assert response.content == b""


def test_empty_file(api):
    base, store = api
    add_file(store, "empty.bin", b"")
    response = requests.get(base + "/files/empty.bin", headers=AUTH)
    assert response.status_code == 200 and response.content == b""


def test_only_cataloged_files_are_served(api):
    base, _ = api
    (Path(get_dl_dir()) / "partial.bin").write_bytes(b"secret")
    assert requests.get(base + "/files/partial.bin", headers=AUTH).status_code == 404


@pytest.mark.parametrize("path", ["../outside", "..%2Foutside", "%2e%2e/outside", "a/../../outside"])
def test_traversal_is_refused(api, path, tmp_path):
    base, _ = api
    (tmp_path / "outside").write_bytes(b"secret")
    prepared = requests.Request("GET", base + "/files/x", headers=AUTH).prepare()
    prepared.url = base + "/files/" + path
    assert requests.Session().send(prepared).status_code == 404


def test_cataloged_file_replaced_by_symlink_is_refused(api, tmp_path):
    base, store = api
    path = add_file(store)
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    path.unlink()
    path.symlink_to(outside)
    assert requests.get(base + "/files/movie.bin", headers=AUTH).status_code == 404


def test_cataloged_file_behind_symlinked_directory_is_refused(api, tmp_path):
    base, store = api
    add_file(store, "sub/movie.bin")
    root = Path(get_dl_dir())
    (root / "sub" / "movie.bin").unlink()
    (root / "sub").rmdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "movie.bin").write_bytes(b"secret")
    (root / "sub").symlink_to(outside)
    assert requests.get(base + "/files/sub/movie.bin", headers=AUTH).status_code == 404


def test_deleted_file_is_not_found(api):
    base, store = api
    add_file(store).unlink()
    assert requests.get(base + "/files/movie.bin", headers=AUTH).status_code == 404


def test_stalled_client_releases_its_thread(tmp_path, downloads, monkeypatch):
    monkeypatch.setattr(server_module, "REQUEST_TIMEOUT", 0.2)
    server, thread, _ = launch(tmp_path)
    try:
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=5) as connection:
            connection.sendall(b"POST /jobs HTTP/1.1\r\nHost: x\r\n")
            connection.settimeout(5)
            started = time.monotonic()
            assert connection.recv(1024) == b""
            assert time.monotonic() - started < 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_short_token_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="16"):
        make_server("127.0.0.1", 0, store=JobStore(tmp_path / "j.sqlite3"), token="short")


def test_token_is_generated_once(tmp_path):
    store = JobStore(tmp_path / "j.sqlite3")
    server, _ = make_server("127.0.0.1", 0, store=store)
    server.server_close()
    token = get_secret("HELM_SERVER_TOKEN")
    assert token and len(token) >= 16
    server, _ = make_server("127.0.0.1", 0, store=store)
    server.server_close()
    assert get_secret("HELM_SERVER_TOKEN") == token


@pytest.mark.parametrize("read_only", [False, True])
def test_run_server_serves_then_shuts_down(tmp_path, monkeypatch, capsys, read_only):
    store = JobStore(tmp_path / "j.sqlite3")
    monkeypatch.setattr(server_module, "JobStore", lambda: store)
    monkeypatch.setenv("HELM_SERVER_TOKEN", TOKEN)
    monkeypatch.setattr(server_module, "queue_loop", lambda store, stop: stop.wait())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    stop = threading.Event()
    thread = threading.Thread(
        target=run_server, args=("127.0.0.1", port), kwargs={"read_only": read_only, "stop": stop}
    )
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while True:
            try:
                response = requests.get(f"http://127.0.0.1:{port}/health", headers=AUTH, timeout=1)
                break
            except requests.ConnectionError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        assert json.loads(response.text)["read_only"] is read_only
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
    assert TOKEN not in capsys.readouterr().out


def test_public_binding_warns_about_plain_http(tmp_path, monkeypatch, capsys):
    store = JobStore(tmp_path / "j.sqlite3")
    monkeypatch.setattr(server_module, "JobStore", lambda: store)
    monkeypatch.setenv("HELM_SERVER_TOKEN", TOKEN)
    stop = threading.Event()
    stop.set()
    run_server("0.0.0.0", 0, read_only=True, stop=stop)
    assert "plain HTTP" in capsys.readouterr().err
