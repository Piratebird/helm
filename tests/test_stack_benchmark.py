"""Everything in the stack exercise that does not need a container engine."""

import hashlib
import json
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
import responses

import helm.core.stack_benchmark as stack_benchmark
from helm.core.stack_benchmark import (
    DEFAULT_IMAGES,
    Stack,
    cgroup_path,
    fixture_handler,
    load_images,
    read_cgroup,
    scrub_logs,
    verify_limits,
)
from helm.core.torrent_links import metadata_source


def test_default_images_cover_every_service():
    assert set(DEFAULT_IMAGES) == set(stack_benchmark.PORTS)


def test_load_images_applies_manifest_then_overrides(tmp_path):
    manifest = tmp_path / "images.json"
    manifest.write_text(json.dumps({"jackett": "manifest:1", "prowlarr": "manifest:2"}))
    images = load_images(manifest, ["prowlarr=override:3"])
    assert images["jackett"] == "manifest:1"
    assert images["prowlarr"] == "override:3"
    assert images["qbittorrent"] == DEFAULT_IMAGES["qbittorrent"]


@pytest.mark.parametrize("overrides", [["jackett"], ["jackett="], ["unknown=x"]])
def test_invalid_image_overrides(overrides):
    with pytest.raises(ValueError):
        load_images(None, overrides)


def test_scrub_logs_removes_run_keys_and_temporary_password():
    text = (
        "The WebUI administrator username is: admin\n"
        "A temporary password is provided for this session: aBc123XyZ\n"
        "key abcdef0123456789 started"
    )
    scrubbed = scrub_logs(text, ["abcdef0123456789"])
    assert "aBc123XyZ" not in scrubbed
    assert "abcdef0123456789" not in scrubbed
    assert "administrator username" in scrubbed


def write_cgroup(path, memory_max="268435456", cpu_max="50000 100000"):
    path.mkdir(exist_ok=True)
    (path / "memory.max").write_text(memory_max + "\n")
    (path / "cpu.max").write_text(cpu_max + "\n")
    (path / "memory.current").write_text("1024\n")
    (path / "cpu.stat").write_text("usage_usec 77\nuser_usec 7\n")
    return path


def test_verify_limits_accepts_effective_limits(tmp_path):
    result = verify_limits(write_cgroup(tmp_path), 256 * 1024**2)
    assert result == {"memory_max": "268435456", "cpu_max": "50000 100000", "verified": True}


@pytest.mark.parametrize(
    "memory_max,cpu_max",
    [("max", "50000 100000"), ("1", "50000 100000"), ("268435456", "max 100000"), ("268435456", "100000 100000")],
)
def test_verify_limits_detects_unenforced_limits(tmp_path, memory_max, cpu_max):
    assert verify_limits(write_cgroup(tmp_path, memory_max, cpu_max), 256 * 1024**2)["verified"] is False


def test_read_cgroup(tmp_path):
    assert read_cgroup(write_cgroup(tmp_path)) == {"memory_current_bytes": 1024, "cpu_usage_usec": 77}


def test_cgroup_path_of_current_process():
    assert Path("/sys/fs/cgroup") in [*cgroup_path("self").parents, cgroup_path("self")]


def test_peer_addresses_prefer_network_gateway_and_deduplicate():
    routes = "default via 172.28.0.1 dev eth0\n"
    hosts = "172.17.0.1  host.docker.internal\n172.28.0.1  host.docker.internal\n"
    assert stack_benchmark.peer_addresses(routes, hosts) == ["172.28.0.1", "172.17.0.1"]
    assert stack_benchmark.peer_addresses("", "") == []


def test_seeder_is_reachable_on_every_interface_at_the_reported_port(tmp_path):
    pytest.importorskip("libtorrent")
    session, data, checksum = stack_benchmark.seed_payload(tmp_path / "seed", 1024)
    try:
        address = socket.gethostbyname(socket.gethostname())
        deadline = time.monotonic() + 5
        while True:
            try:
                socket.create_connection(("127.0.0.1", session.listen_port()), timeout=1).close()
                socket.create_connection((address, session.listen_port()), timeout=1).close()
                break
            except OSError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
    finally:
        session.pause()
    assert hashlib.sha256((tmp_path / "seed" / "generated-payload.bin").read_bytes()).hexdigest() == checksum
    assert metadata_source(data).magnet


def test_fixture_page_escapes_query_and_links_magnet():
    server = ThreadingHTTPServer(("127.0.0.1", 0), fixture_handler({"magnet": "magnet:?a=1&b=2", "size": 5}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.get(f"http://127.0.0.1:{server.server_port}/search", params={"q": "<b>"})
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert "&lt;b&gt; Linux ISO" in response.text
    assert "magnet:?a=1&amp;b=2" in response.text


def make_stack(tmp_path, monkeypatch, fail_on=()):
    calls = []

    def fake_command(engine, *arguments, timeout=120):
        calls.append(arguments)
        if arguments[0] in fail_on:
            raise RuntimeError("engine refused token-free")
        return ""

    monkeypatch.setattr(stack_benchmark, "command", fake_command)
    return Stack("docker", tmp_path, 1234), calls


def test_prepare_writes_isolated_configuration(tmp_path, monkeypatch):
    stack, _ = make_stack(tmp_path, monkeypatch)
    stack.prepare()
    assert stack.project.startswith("helm-bench-")
    assert json.loads((tmp_path / "jackett/Jackett/ServerConfig.json").read_text())["APIKey"] == stack.keys["jackett"]
    assert "host.docker.internal:1234" in (tmp_path / "jackett/cardigann/definitions/helmbenchmark.yml").read_text()
    assert stack.keys["prowlarr"] in (tmp_path / "prowlarr/config.xml").read_text()
    assert "DHTEnabled=false" in (tmp_path / "qbittorrent/qBittorrent/qBittorrent.conf").read_text()


def test_cleanup_only_touches_owned_resources(tmp_path, monkeypatch):
    stack, calls = make_stack(tmp_path, monkeypatch)
    assert stack.cleanup() == []
    assert calls == []
    stack.ids = {"jackett": "id1"}
    stack.network_created = True
    assert stack.cleanup() == []
    assert calls == [("rm", "-f", "id1"), ("network", "rm", stack.project)]


def test_cleanup_reports_each_failure(tmp_path, monkeypatch):
    stack, _ = make_stack(tmp_path, monkeypatch, fail_on=("rm", "network"))
    stack.ids = {"jackett": "id1"}
    stack.network_created = True
    assert len(stack.cleanup()) == 2


@responses.activate
def test_configure_indexers_gets_jackett_session_before_config(tmp_path, monkeypatch):
    stack, _ = make_stack(tmp_path, monkeypatch)
    stack.urls = {"jackett": "http://jackett.test", "prowlarr": "http://prowlarr.test"}
    config = "http://jackett.test/api/v2.0/indexers/helmbenchmark/config"
    responses.add(responses.GET, "http://jackett.test/UI/Dashboard", status=200, headers={"Set-Cookie": "Jackett=1"})
    responses.add(responses.GET, config, json=[{"id": "sitelink"}])
    responses.add(responses.POST, config, status=204)
    responses.add(
        responses.GET,
        "http://prowlarr.test/api/v1/indexer/schema",
        json=[
            {"implementation": "Other", "fields": []},
            {
                "implementation": "Torznab",
                "fields": [{"name": "baseUrl"}, {"name": "apiPath"}, {"name": "apiKey"}, {"name": "unrelated"}],
            },
        ],
    )
    responses.add(responses.POST, "http://prowlarr.test/api/v1/indexer", status=201)

    stack.configure_indexers()

    assert responses.calls[0].request.url.endswith("/UI/Dashboard")
    assert responses.calls[1].request.url == config
    posted = json.loads(responses.calls[-1].request.body)
    values = {field["name"]: field.get("value") for field in posted["fields"]}
    assert values["baseUrl"] == f"http://{stack.project}-jackett:9117"
    assert values["apiKey"] == stack.keys["jackett"]
    assert responses.calls[-1].request.headers["X-Api-Key"] == stack.keys["prowlarr"]


def test_failed_engine_run_is_reported_as_failure_and_cleaned(monkeypatch):
    def refuse(engine, *arguments, timeout=120):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(stack_benchmark, "command", refuse)
    report = stack_benchmark.run_engine("docker", repeats=1)
    assert report["success"] is False
    assert report["errors"] and report["trials"] == []
    assert report["cleanup_errors"] == []


def test_main_exit_code_and_output(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_run(engine, repeats, images, payload):
        seen.update(engine=engine, images=images)
        return {"success": False, "errors": ["x"], "cleanup_errors": []}

    monkeypatch.setattr(stack_benchmark, "run_engine", fake_run)
    output = tmp_path / "out" / "stack.json"
    code = stack_benchmark.main(["--engine", "podman", "--output", str(output), "--image", "jackett=x:1"])
    assert code == 1
    assert json.loads(output.read_text())["success"] is False
    assert seen["images"]["jackett"] == "x:1"


@pytest.mark.parametrize("arguments", [["--repeats", "0"], ["--image", "bad"]])
def test_main_rejects_invalid_arguments(tmp_path, arguments):
    with pytest.raises(SystemExit):
        stack_benchmark.main(["--engine", "docker", "--output", str(tmp_path / "s.json"), *arguments])
