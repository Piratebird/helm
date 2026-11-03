"""Disposable production stack exercise with cgroup verification and raw samples."""

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from helm.benchmarks.benchmark import container_metadata
from helm.config.secret_manager import redact_text
from helm.torrents.torrent_links import metadata_source

DEFAULT_IMAGES = {
    "jackett": "sha256:d39ca96018ff40dc20730d80f96195a784ec71b1fb647b86a6db1d01431c85c5",
    "prowlarr": "sha256:48cd1d7e2a8b73178829c03e0606b8c820d067d6f7d7d92328573eb311e3e521",
    "qbittorrent": "sha256:66af76dac14d022d43e060ee2970bf149309b463346c45a37fa844a6265bcdc0",
    "flaresolverr": "sha256:894893ed4d4557764df3e74d27c5bc1b3944e5a33ed410e62ab7c744b348f415",
}
# qBittorrent prints its first-run WebUI password in a sentence generic redaction misses.
TEMPORARY_PASSWORD = re.compile(r"(temporary password[^:\n]*:\s*)\S+", re.IGNORECASE)
PORTS = {"jackett": 9117, "prowlarr": 9696, "qbittorrent": 18080, "flaresolverr": 8191}


def load_images(manifest=None, overrides=()):
    """Merge the recorded defaults with a JSON manifest and service=image overrides."""
    images = dict(DEFAULT_IMAGES)
    if manifest:
        images.update(json.loads(Path(manifest).read_text()))
    for override in overrides:
        name, separator, image = override.partition("=")
        if not separator or not image:
            raise ValueError("Image overrides use service=image")
        images[name] = image
    unknown = set(images) - set(PORTS)
    if unknown:
        raise ValueError("Unknown services: " + ", ".join(sorted(unknown)))
    return images


def scrub_logs(text, secrets_to_remove):
    for value in secrets_to_remove:
        text = text.replace(value, "***REDACTED***")
    return redact_text(TEMPORARY_PASSWORD.sub(r"\1***REDACTED***", text))


def command(engine, *arguments, timeout=120):
    return subprocess.run(
        [engine, *arguments], check=True, capture_output=True, text=True, timeout=timeout
    ).stdout.strip()


def cgroup_path(pid):
    text = Path(f"/proc/{pid}/cgroup").read_text()
    relative = next(line.split("::", 1)[1] for line in text.splitlines() if line.startswith("0::"))
    path = Path("/sys/fs/cgroup") / relative.lstrip("/")
    if Path("/sys/fs/cgroup") not in path.resolve().parents:
        raise ValueError("Cgroup path escaped cgroup root")
    return path


def read_cgroup(path):
    memory = int((path / "memory.current").read_text())
    cpu = dict(line.split() for line in (path / "cpu.stat").read_text().splitlines())
    return {"memory_current_bytes": memory, "cpu_usage_usec": int(cpu["usage_usec"])}


def verify_limits(path, memory_bytes, cpus=0.5):
    memory = (path / "memory.max").read_text().strip()
    quota, period = (path / "cpu.max").read_text().split()
    effective = None if quota == "max" else int(quota) / int(period)
    return {
        "memory_max": memory,
        "cpu_max": f"{quota} {period}",
        "verified": memory == str(memory_bytes) and effective is not None and abs(effective - cpus) < 0.00001,
    }


def fixture_handler(source):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query).get("q", ["Helm Benchmark"])[0]
            import html

            body = (
                f'<html><table><tr class="result"><td><a class="title" href="/detail">{html.escape(query)} Linux ISO</a>'
                f'<a class="download" href="{html.escape(source["magnet"], quote=True)}">Download</a></td>'
                f'<td class="size">{source["size"]} B</td></tr></table></html>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def peer_addresses(routes, hosts):
    """Order host addresses by directness: the network gateway, then the alias."""
    addresses = []
    for line in routes.splitlines():
        fields = line.split()
        if fields[:2] == ["default", "via"]:
            addresses.append(fields[2])
    addresses.extend(line.split()[0] for line in hosts.splitlines() if line.strip())
    return list(dict.fromkeys(addresses))


def free_port():
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", 0))
        return probe.getsockname()[1]


def seed_payload(directory, size_bytes):
    import libtorrent as lt

    directory.mkdir()
    payload = directory / "generated-payload.bin"
    block = bytes(range(256)) * 4096
    with payload.open("wb") as stream:
        remaining = size_bytes
        while remaining:
            chunk = block[: min(remaining, len(block))]
            stream.write(chunk)
            remaining -= len(chunk)
    storage = lt.file_storage()
    lt.add_files(storage, str(payload))
    create = lt.create_torrent(storage, 256 * 1024)
    lt.set_piece_hashes(create, str(directory))
    data = lt.bencode(create.generate())
    session = lt.session(
        {
            # libtorrent expands 0.0.0.0 per interface; port 0 would differ on each.
            "listen_interfaces": f"0.0.0.0:{free_port()}",
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": False,
            "enable_natpmp": False,
            "enable_incoming_utp": False,
            "enable_outgoing_utp": False,
        }
    )
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(lt.bdecode(data))
    params.save_path = str(directory)
    params.flags |= lt.torrent_flags.seed_mode
    params.flags &= ~lt.torrent_flags.paused
    params.flags &= ~lt.torrent_flags.auto_managed
    session.add_torrent(params)
    return session, data, hashlib.sha256(payload.read_bytes()).hexdigest()


class Stack:
    def __init__(self, engine, directory, fixture_port, images=None):
        self.engine = engine
        self.directory = Path(directory)
        self.images = images or DEFAULT_IMAGES
        self.project = "helm-bench-" + uuid.uuid4().hex[:10]
        self.ids = {}
        self.urls = {}
        self.cgroups = {}
        self.limits = {}
        self.keys = {name: secrets.token_hex(16) for name in ("jackett", "prowlarr")}
        self.fixture_port = fixture_port
        self.network_created = False

    def prepare(self):
        for name in PORTS:
            (self.directory / name).mkdir()
        (self.directory / "downloads").mkdir()
        jackett = self.directory / "jackett" / "Jackett"
        jackett.mkdir()
        (jackett / "ServerConfig.json").write_text(
            json.dumps(
                {
                    "Port": 9117,
                    "AllowExternal": True,
                    "APIKey": self.keys["jackett"],
                    "UpdateDisabled": True,
                    "FlareSolverrUrl": f"http://{self.project}-flaresolverr:8191",
                }
            )
        )
        definitions = self.directory / "jackett" / "cardigann" / "definitions"
        definitions.mkdir(parents=True)
        (definitions / "helmbenchmark.yml").write_text(f"""---
id: helmbenchmark
name: Helm Benchmark Fixture
description: Generated local benchmark content
language: en-US
type: public
encoding: UTF-8
links:
  - http://host.docker.internal:{self.fixture_port}/
caps:
  categorymappings:
    - {{id: 1, cat: PC/ISO}}
  modes:
    search: [q]
settings: []
search:
  paths:
    - path: search
  inputs:
    q: "{{{{ .Keywords }}}}"
  rows:
    selector: tr.result
  fields:
    category:
      text: 1
    title:
      selector: a.title
    details:
      selector: a.title
      attribute: href
    download:
      selector: a.download
      attribute: href
    size:
      selector: td.size
    seeders:
      text: 1
    leechers:
      text: 0
    date:
      text: now
""")
        (self.directory / "prowlarr" / "config.xml").write_text(
            f"<Config><BindAddress>*</BindAddress><Port>9696</Port><ApiKey>{self.keys['prowlarr']}</ApiKey><AuthenticationMethod>External</AuthenticationMethod><AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired><LaunchBrowser>False</LaunchBrowser><LogLevel>info</LogLevel><UpdateMechanism>Docker</UpdateMechanism></Config>"
        )
        qb = self.directory / "qbittorrent" / "qBittorrent"
        qb.mkdir()
        (qb / "qBittorrent.conf").write_text(r"""[BitTorrent]
Session\BTProtocol=TCP
Session\DHTEnabled=false
Session\LSDEnabled=false
Session\PeXEnabled=false
Session\DefaultSavePath=/downloads
[Preferences]
WebUI\Username=admin
WebUI\LocalHostAuth=false
WebUI\AuthSubnetWhitelistEnabled=true
WebUI\AuthSubnetWhitelist=127.0.0.1/32, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
WebUI\HostHeaderValidation=false
""")

    def start(self):
        command(self.engine, "network", "create", "--label", "com.docker.compose.project=" + self.project, self.project)
        self.network_created = True
        for name, port in PORTS.items():
            memory_mib = 512 if name == "flaresolverr" else 256
            args = [
                "run",
                "-d",
                "--name",
                self.project + "-" + name,
                "--network",
                self.project,
                "--label",
                "com.docker.compose.project=" + self.project,
                "--label",
                "com.docker.compose.service=" + name,
                "--memory",
                f"{memory_mib}m",
                "--cpus",
                "0.5",
                "--security-opt",
                "label=disable",
                "--add-host",
                "host.docker.internal:host-gateway",
                "-p",
                f"127.0.0.1::{port}",
            ]
            if name != "flaresolverr":
                user = 0 if self.engine == "podman" else os.getuid()
                group = 0 if self.engine == "podman" else os.getgid()
                args += ["-e", f"PUID={user}", "-e", f"PGID={group}", "-v", str(self.directory / name) + ":/config"]
            if name == "qbittorrent":
                args += ["-e", "WEBUI_PORT=18080", "-v", str(self.directory / "downloads") + ":/downloads"]
            args.append(self.images[name])
            identifier = command(self.engine, *args)
            self.ids[name] = identifier
            published = command(self.engine, "port", identifier, f"{port}/tcp").splitlines()[0]
            self.urls[name] = "http://" + published
        self.wait_ready()
        for name, identifier in self.ids.items():
            raw = json.loads(command(self.engine, "inspect", identifier))[0]
            path = cgroup_path(raw["State"]["Pid"])
            self.cgroups[name] = path
            self.limits[name] = verify_limits(path, (512 if name == "flaresolverr" else 256) * 1024**2)
            if not self.limits[name]["verified"]:
                raise RuntimeError("Effective cgroup limits differ for " + name)

    def wait_ready(self, timeout=120):
        paths = {
            "jackett": "/UI/Dashboard",
            "prowlarr": "/ping",
            "qbittorrent": "/api/v2/app/version",
            "flaresolverr": "/",
        }
        deadline = time.monotonic() + timeout
        for name, path in paths.items():
            while True:
                try:
                    response = requests.get(self.urls[name] + path, timeout=2)
                    if response.status_code in (200, 401):
                        break
                except requests.RequestException:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Production service did not become ready: " + name)
                time.sleep(0.5)

    def configure_indexers(self):
        session = requests.Session()
        # The config API redirects to the login page until this sets a session cookie.
        session.get(self.urls["jackett"] + "/UI/Dashboard", timeout=20).raise_for_status()
        url = self.urls["jackett"] + "/api/v2.0/indexers/helmbenchmark/config"
        response = session.get(url, timeout=10)
        response.raise_for_status()
        response = session.post(url, json=response.json(), timeout=20)
        response.raise_for_status()
        base = self.urls["prowlarr"]
        headers = {"X-Api-Key": self.keys["prowlarr"]}
        response = requests.get(base + "/api/v1/indexer/schema", headers=headers, timeout=20)
        response.raise_for_status()
        schema = next(item for item in response.json() if item.get("implementation") == "Torznab")
        schema.update(name="Helm local benchmark", enable=True, appProfileId=1, priority=25)
        for field in schema["fields"]:
            if field["name"] == "baseUrl":
                field["value"] = f"http://{self.project}-jackett:9117"
            elif field["name"] == "apiPath":
                field["value"] = "/api/v2.0/indexers/helmbenchmark/results/torznab/api"
            elif field["name"] == "apiKey":
                field["value"] = self.keys["jackett"]
        response = requests.post(base + "/api/v1/indexer", headers=headers, json=schema, timeout=30)
        response.raise_for_status()

    def stop(self):
        if self.ids:
            command(self.engine, "stop", "--time", "5", *self.ids.values(), timeout=40)

    def cleanup(self):
        errors = []
        if self.ids:
            try:
                command(self.engine, "rm", "-f", *self.ids.values(), timeout=40)
            except Exception as error:
                errors.append(redact_text(error))
        if self.network_created:
            try:
                command(self.engine, "network", "rm", self.project)
            except Exception as error:
                errors.append(redact_text(error))
        return errors


def run_engine(engine, repeats=1, images=None, payload_mib=16):
    phase = ["startup"]
    samples = []
    stop = threading.Event()
    source = {"magnet": "", "size": payload_mib * 1024**2}
    fixture = ThreadingHTTPServer(("0.0.0.0", 0), fixture_handler(source))
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    report = {
        "engine": engine,
        "samples": samples,
        "trials": [],
        "metric": "memory.current bytes and cpu.stat usage_usec per cgroup; do not add to host PSS",
        "errors": [],
    }
    temporary = tempfile.TemporaryDirectory(prefix="helm-stack-")
    stack = Stack(engine, temporary.name, fixture.server_port, images)
    seeder = None
    sampler = None
    started = time.monotonic()

    def sample_loop():
        while not stop.is_set():
            services = {}
            for name, path in list(stack.cgroups.items()):
                try:
                    services[name] = read_cgroup(path)
                except (OSError, ValueError):
                    services[name] = {"unavailable": True}
            samples.append({"elapsed_seconds": time.monotonic() - started, "phase": phase[0], "services": services})
            stop.wait(0.1)

    try:
        stack.prepare()
        stack.start()
        report["project"] = stack.project
        report["limits"] = stack.limits
        report["metadata"] = container_metadata(engine, stack.project)
        # Reach the host through addresses the container routes, without exposing peers publicly.
        hosts = command(engine, "exec", stack.ids["qbittorrent"], "getent", "hosts", "host.docker.internal")
        routes = command(engine, "exec", stack.ids["qbittorrent"], "ip", "route", "show", "default")
        gateways = peer_addresses(routes, hosts)
        seeder, data, checksum = seed_payload(Path(temporary.name) / "seed", payload_mib * 1024**2)
        resolved = metadata_source(data)
        peers = "|".join(f"{address}:{seeder.listen_port()}" for address in gateways)
        source["magnet"] = resolved.magnet + f"&x.pe={gateways[0]}:{seeder.listen_port()}"
        stack.configure_indexers()
        sampler = threading.Thread(target=sample_loop)
        sampler.start()
        for trial in range(repeats):
            phase[0] = "idle"
            time.sleep(1)
            phase[0] = "search"
            search_started = time.monotonic()
            releases = []
            for number in range(5):
                response = requests.get(
                    stack.urls["prowlarr"] + "/api/v1/search",
                    headers={"X-Api-Key": stack.keys["prowlarr"]},
                    params={"query": f"Helm Benchmark {trial} {number}", "type": "search"},
                    timeout=30,
                )
                response.raise_for_status()
                releases = response.json()
                if not releases:
                    raise RuntimeError("Production Prowlarr→Jackett fixture search returned no results")
            search_seconds = time.monotonic() - search_started
            phase[0] = "download"
            download_started = time.monotonic()
            qb = stack.urls["qbittorrent"]
            response = requests.post(
                qb + "/api/v2/torrents/add",
                files={"torrents": ("generated.torrent", data, "application/x-bittorrent")},
                data={"savepath": "/downloads", "tags": "helm-benchmark"},
                timeout=15,
            )
            response.raise_for_status()
            # qBittorrent answers 200 with this body when it refuses a torrent.
            if response.text.strip().lower().startswith("fails"):
                raise RuntimeError("Production qBittorrent rejected the generated torrent: " + response.text[:200])
            # Generated torrents are hybrid, and qBittorrent keys those by the v2 hash.
            deadline = time.monotonic() + 45
            torrent_hash = None
            next_peer_add = 0.0
            while True:
                response = requests.get(qb + "/api/v2/torrents/info", params={"tag": "helm-benchmark"}, timeout=5)
                response.raise_for_status()
                entries = response.json()
                if entries:
                    torrent_hash = entries[0]["hash"]
                    if entries[0].get("progress") == 1:
                        break
                    if time.monotonic() >= next_peer_add:
                        next_peer_add = time.monotonic() + 5
                        requests.post(
                            qb + "/api/v2/torrents/addPeers",
                            data={"hashes": torrent_hash, "peers": peers},
                            timeout=5,
                        ).raise_for_status()
                if time.monotonic() >= deadline:
                    state = [{key: entry.get(key) for key in ("state", "progress", "num_seeds")} for entry in entries]
                    raise TimeoutError(f"Production qBittorrent could not complete the generated payload: {state}")
                time.sleep(0.2)
            target = Path(temporary.name) / "downloads" / "generated-payload.bin"
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != checksum:
                raise ValueError("Production download checksum mismatch")
            report["trials"].append(
                {
                    "search_requests": 5,
                    "search_seconds": search_seconds,
                    "download_seconds": time.monotonic() - download_started,
                    "payload_bytes": payload_mib * 1024**2,
                    "sha256": actual,
                }
            )
            requests.post(
                qb + "/api/v2/torrents/delete", data={"hashes": torrent_hash, "deleteFiles": "true"}, timeout=10
            ).raise_for_status()
        phase[0] = "teardown"
        stack.stop()
        time.sleep(0.5)
        report["all_containers_stopped"] = all(
            not json.loads(command(engine, "inspect", identifier))[0]["State"]["Running"]
            for identifier in stack.ids.values()
        )
        report["success"] = report["all_containers_stopped"]
    except Exception as error:
        report["success"] = False
        report["errors"].append(redact_text(error))
        for name, identifier in stack.ids.items():
            try:
                text = command(engine, "logs", "--tail", "25", identifier)
                # Keys are private to this run; remove them even from diagnostic logs.
                report.setdefault("failure_logs", {})[name] = scrub_logs(text[-4096:], stack.keys.values())
            except Exception:
                pass
    finally:
        stop.set()
        if sampler:
            sampler.join()
        if seeder:
            seeder.pause()
        report["cleanup_errors"] = stack.cleanup()
        fixture.shutdown()
        fixture.server_close()
        fixture_thread.join()
        try:
            temporary.cleanup()
        except OSError as error:
            report["cleanup_errors"].append(redact_text(error))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=["docker", "podman"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--payload-mib", type=int, default=16)
    parser.add_argument("--image-manifest", type=Path, help="JSON object mapping services to image references")
    parser.add_argument("--image", action="append", default=[], metavar="SERVICE=IMAGE", help="Override one image")
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.payload_mib < 1:
        parser.error("repeats and payload size must be positive")
    try:
        images = load_images(args.image_manifest, args.image)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    result = run_engine(args.engine, args.repeats, images, args.payload_mib)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "engine": args.engine,
                "success": result["success"],
                "errors": result["errors"],
                "cleanup_errors": result["cleanup_errors"],
            }
        )
    )
    return 0 if result["success"] and not result["cleanup_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
