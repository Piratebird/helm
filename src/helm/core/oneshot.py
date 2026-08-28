import os
import subprocess
import sys
import time

import requests

# The CLI runs inside the mini-helm container, whose docker socket is mounted at
# /var/run/docker.sock. Both Docker and Podman provide that socket, so the docker
# CLI works for either engine; override via HELM_DOCKER_CMD if you ever run the
# CLI without the socket (e.g. native mode with a remote daemon).
DOCKER_CMD = os.environ.get("HELM_DOCKER_CMD", "docker")

CONTAINER_SERVICES = frozenset(["jackett", "qbittorrent", "flaresolverr", "gluetun"])


def _docker_run(args, **kwargs):
    return subprocess.run([DOCKER_CMD] + args, **kwargs)


def _container_name(service):
    project = os.environ.get("COMPOSE_PROJECT_NAME", "helm")
    return f"{project}-{service}"


def spin_up_oneshot():
    print("\n\033[1m\033[36mInitializing One-Shot Ephemeral Stack...\033[0m")
    print("\033[3mBringing up Jackett, Flaresolverr, and qBittorrent via docker compose...\033[0m")

    # Explicitly ensure all background containers are up just in case host depends_on fails
    try:
        _docker_run(
            ["compose", "up", "-d", "--remove-orphans", "jackett", "qbittorrent", "flaresolverr"],
            check=False,
        )
    except Exception:
        pass

    def check_container_status(service_name, timeout=45):
        print(f"\033[3mWaiting for {service_name} to stabilize...\033[0m")
        elapsed = 0
        while elapsed < timeout:
            try:
                # Get container ID
                cid = subprocess.check_output([DOCKER_CMD, "compose", "ps", "-q", service_name], text=True).strip()
                if cid:
                    # Check status
                    status = subprocess.check_output(
                        [DOCKER_CMD, "inspect", "-f", "{{.State.Status}}", cid], text=True
                    ).strip()
                    if status == "running":
                        return True
                    elif status == "exited":
                        print(
                            f"\033[31m[ERROR] Container {service_name} crashed immediately. Check 'docker compose logs {service_name}'\033[0m"
                        )
                        for svc in CONTAINER_SERVICES:
                            _docker_run(["stop", _container_name(svc)], capture_output=True)
                        sys.exit(1)
            except Exception:
                pass
            time.sleep(5)
            elapsed += 5
        print(f"\033[31m[WARNING] Timeout waiting for {service_name} to stabilize.\033[0m")
        return False

    check_container_status("jackett")
    check_container_status("qbittorrent")
    check_container_status("flaresolverr")
    print("\033[36mWaiting for internal HTTP servers to fully boot (this ensures 1337x doesn't fail)...\033[0m")

    # Wait for Jackett to respond to HTTP requests
    jackett_url = os.environ.get("JACKETT_URL", "http://jackett:9117")
    jackett_ready = False
    for _ in range(30):
        try:
            r = requests.get(f"{jackett_url}/UI/Dashboard", timeout=5)
            if r.status_code in [200, 401]:
                jackett_ready = True
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)

    if not jackett_ready:
        print("\033[31m[WARNING] Jackett HTTP API didn't respond in time.\033[0m")

    # Wait for Flaresolverr to respond to HTTP requests
    flaresolverr_url = os.environ.get("FLARESOLVERR_URL", "http://flaresolverr:8191")
    flaresolverr_ready = False
    for _ in range(30):
        try:
            r = requests.get(flaresolverr_url, timeout=5)
            if "ready" in r.text.lower() or r.status_code == 200:
                flaresolverr_ready = True
                break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)

    if not flaresolverr_ready:
        print("\033[31m[WARNING] Flaresolverr HTTP API didn't respond in time.\033[0m")

    print("\033[32mEnvironment variables loaded and APIs ready.\033[0m\n")


def teardown_oneshot():
    print("\n\033[1m\033[33mTearing down the ephemeral stack to save processing power...\033[0m")
    try:
        # Stop containers individually via 'docker stop' instead of 'docker compose stop'
        # to avoid network namespace destruction that triggers "rootless netns: permission denied"
        for svc in CONTAINER_SERVICES:
            try:
                _docker_run(["stop", _container_name(svc)], capture_output=True)
            except KeyboardInterrupt:
                pass
        print("\033[1m\033[32mAll containers stopped cleanly. Your RAM is free!\033[0m")
    except Exception as e:
        print(f"\033[31mCould not tear down docker-compose stack: {e}\033[0m")


def wait_for_download():
    print("\n\033[36mWaiting for download to complete before tearing down the stack...\033[0m")
    print("\033[3m(Press Ctrl+C to cancel, or Ctrl+P then Ctrl+Q to detach and run in background)\033[0m")

    import helm.core.qbittorrent_client as qbc

    # temporarily inject webui for the wait functionality
    qb_webui = os.environ.get("QB_WEBUI", "http://qbittorrent:18080")
    session = qbc.login_qbittorrent()
    time.sleep(5)  # give qbittorrent time to parse the magnet metadata

    try:
        while True:
            try:
                r = session.get(f"{qb_webui}/api/v2/torrents/info", timeout=15)
                if r.status_code == 200:
                    torrents = r.json()
                    if not torrents:
                        time.sleep(3)
                        continue

                    all_done = True
                    for t in torrents:
                        progress = t.get("progress", 0.0)
                        name = t.get("name", "Unknown")
                        if progress < 1.0:
                            all_done = False
                            print(f"\r\033[33mDownloading '{name[:40]}...' ({progress * 100:.1f}%) \033[0m", end="")
                            sys.stdout.flush()

                    if all_done:
                        print("\n\033[1m\033[32mDownload(s) 100% complete!\033[0m")
                        break
            except Exception:
                pass

            time.sleep(3)
    except KeyboardInterrupt:
        print("\n\033[31mDownload cancelled by user!\033[0m")
        print("\033[3mRemoving unfinished torrents from qBittorrent...\033[0m")
        try:
            r = session.get(f"{qb_webui}/api/v2/torrents/info", timeout=15)
            if r.status_code == 200:
                torrents = r.json()
                hashes_to_delete = [t.get("hash") for t in torrents if t.get("progress", 0.0) < 1.0]
                if hashes_to_delete:
                    # delete torrent and downloaded data
                    session.post(
                        f"{qb_webui}/api/v2/torrents/delete",
                        data={"hashes": "|".join(hashes_to_delete), "deleteFiles": "true"},
                        timeout=15,
                    )
        except Exception:
            pass
        raise
