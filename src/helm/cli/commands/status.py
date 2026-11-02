"""helm-torrent status: running containers and Web UI URLs."""

import shutil
import subprocess

from helm.cli import deps


def run():
    """Print running helm containers and the configured Web UI URLs (never prompts)."""
    print("Checking container status and Web UIs...")
    cmd_runner = "docker" if shutil.which("docker") else "podman" if shutil.which("podman") else None
    res = None
    if cmd_runner:
        cmd = [cmd_runner, "ps", "--format", "{{.Names}}: {{.Status}}"]
        try:
            res = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except Exception:
            print(f"Failed to run {cmd_runner} ps.")
    else:
        print("Native Mode - No containers running.")

    if res is not None:
        print("-" * 40)
        helm_containers = [line for line in res.stdout.splitlines() if "helm" in line.lower()]
        print("\n".join(helm_containers) if helm_containers else "No helm containers found.")
        print("-" * 40)

    settings = deps.load_settings()
    container_text = (res.stdout or "").lower() if res else ""
    print("\n--- Web UIs ---")
    if container_text and "jackett" not in container_text:
        print("Jackett:     not running")
    else:
        print(f"Jackett:     {settings.get('services.jackett')}")
    if container_text and "prowlarr" not in container_text:
        print("Prowlarr:    not running")
    else:
        print(f"Prowlarr:    {settings.get('services.prowlarr')}")
    if container_text and "qbittorrent" not in container_text:
        print("qBittorrent: not running")
    else:
        print(f"qBittorrent: {settings.get('services.qbittorrent')}")
    print("---------------")
