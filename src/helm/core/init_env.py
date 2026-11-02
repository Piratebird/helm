import os
import subprocess
import time

from helm.core import compose
from helm.core.config_manager import get_config_dir, get_dl_dir, get_state_dir


def build_compose(state_dir, dl_dir, config_dir, use_vpn):
    """Render the shared compose template without the setup.sh-only cli service."""
    blocks = {"jackett", "prowlarr", "vpn" if use_vpn else "novpn"}
    values = {"HELM_STATE": state_dir, "HELM_DL": dl_dir, "HELM_CONFIG": config_dir, "ENGINE_SOCKET": ""}
    return compose.render(blocks, values)


def write_private(path, text):
    """Write a file only the user can read; .env.docker holds passwords."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def bootstrap_env():
    print("===========================================")
    print("        Helm Zero-Touch Bootstrapping      ")
    print("===========================================")

    config_dir = get_config_dir()
    state_dir = get_state_dir()
    dl_dir = get_dl_dir()
    project_name = "helm"

    print(f"Using paths:\n  Config:    {config_dir}\n  State:     {state_dir}\n  Downloads: {dl_dir}\n")

    os.makedirs(os.path.join(state_dir, "jackett", "Jackett", "Indexers"), exist_ok=True)
    os.makedirs(os.path.join(state_dir, "qbittorrent", "qBittorrent"), exist_ok=True)
    os.makedirs(os.path.join(state_dir, "gluetun"), exist_ok=True)
    os.makedirs(dl_dir, exist_ok=True)
    os.makedirs(config_dir, exist_ok=True)

    config_file = os.path.join(config_dir, "config.json")
    if not os.path.exists(config_file):
        with open(config_file, "w") as f:
            f.write("{}")

    install_mode = input(
        "How would you like to install the required services?\n  [1] Docker (Recommended)\n  [2] Podman\nChoose (1/2, default 1): "
    ).strip()
    docker_cmd = "podman" if install_mode == "2" else "docker"
    compose_cmd = [docker_cmd, "compose"]

    run_mode = input(
        "\nHow would you like to run Helm?\n  [1] Permanent (Always-On)\n  [2] Ephemeral (One-Shot)\nChoose (1/2, default 2): "
    ).strip()

    use_vpn = input("\nDo you want to route qBittorrent through a VPN using Gluetun? (y/N): ").strip().lower()

    vpn_provider = ""
    vpn_type = ""
    vpn_extra = ""
    if use_vpn == "y":
        vpn_provider = input("Enter VPN Provider (e.g. nordvpn, custom): ").strip()
        vpn_type = input("Enter VPN Type (wireguard/openvpn) (default: wireguard): ").strip() or "wireguard"
        if vpn_type == "wireguard":
            wg_key = input("Enter WireGuard Private Key: ").strip()
            vpn_extra = f"WIREGUARD_PRIVATE_KEY={wg_key}"
        elif vpn_type == "openvpn" and vpn_provider != "custom":
            ovpn_user = input("Enter OpenVPN Username (or token): ").strip()
            ovpn_pass = input("Enter OpenVPN Password (leave blank if using token): ").strip()
            vpn_extra = f"OPENVPN_USER={ovpn_user}\nOPENVPN_PASSWORD={ovpn_pass}"

    compose_yaml = build_compose(state_dir, dl_dir, config_dir, use_vpn == "y")

    compose_path = os.path.join(state_dir, "docker-compose.yml")
    with open(compose_path, "w") as f:
        f.write(compose_yaml)

    env_docker_content = f"""# --- Helm Container Isolated Configuration ---
JACKETT_URL=http://jackett:9117
JACKETT_API_KEY=placeholder
PROWLARR_URL=http://prowlarr:9696
PROWLARR_API_KEY=placeholder
QB_WEBUI=http://{"gluetun" if use_vpn == "y" else "qbittorrent"}:18080
QB_USERNAME=admin
QB_PASSWORD=adminadmin
COMPOSE_PROJECT_NAME={project_name}
HOST_PWD={os.getcwd()}
"""
    if use_vpn == "y":
        env_docker_content += f"""
# --- VPN (Gluetun) Configuration ---
VPN_SERVICE_PROVIDER={vpn_provider}
VPN_TYPE={vpn_type}
{vpn_extra}
"""
    write_private(os.path.join(state_dir, ".env.docker"), env_docker_content)

    print("\nPre-seeding qBittorrent configuration...")
    qb_conf = """[Preferences]
WebUI\\Password_PBKDF2="@ByteArray(ARQ77eY1NUZaQsuDHbIMCA==:0WMRkYTUWVT9wVvdDtHAjU9b3b7uB8NR1Gur2hmQCvCDpm39Q+PsIfSYvgkvpe7L5yL8YQv8EaV7t8mP308QWg==)"
WebUI\\Username=admin
WebUI\\AuthSubnetWhitelist=10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
WebUI\\AuthSubnetWhitelistEnabled=true
WebUI\\LocalHostAuth=false
"""
    with open(os.path.join(state_dir, "qbittorrent", "qBittorrent", "qBittorrent.conf"), "w") as f:
        f.write(qb_conf)

    print("\nStarting containers to initialize Jackett...")

    subprocess.run(
        compose_cmd
        + ["-f", compose_path, "up", "-d", "--remove-orphans", "jackett", "prowlarr", "qbittorrent", "flaresolverr"],
        check=False,
    )

    print("[INFO] Waiting for Jackett to initialize...")
    time.sleep(10)

    jackett_api = ""
    try:
        j_config = os.path.join(state_dir, "jackett", "Jackett", "ServerConfig.json")
        if os.path.exists(j_config):
            import json

            with open(j_config, "r") as f:
                j_data = json.load(f)
                jackett_api = j_data.get("APIKey", "")
    except Exception:
        pass

    if jackett_api:
        env_docker_content = env_docker_content.replace("JACKETT_API_KEY=placeholder", f"JACKETT_API_KEY={jackett_api}")
        write_private(os.path.join(state_dir, ".env.docker"), env_docker_content)
        print("[+] Successfully grabbed Jackett API Key.")

    prowlarr_api = ""
    try:
        p_config = os.path.join(state_dir, "prowlarr", "config.xml")
        if os.path.exists(p_config):
            with open(p_config, "r") as f:
                for line in f:
                    if "<ApiKey>" in line:
                        prowlarr_api = line.split("<ApiKey>")[1].split("</ApiKey>")[0].strip()
                        break
    except Exception:
        pass

    if prowlarr_api:
        env_docker_content = env_docker_content.replace(
            "PROWLARR_API_KEY=placeholder", f"PROWLARR_API_KEY={prowlarr_api}"
        )
        write_private(os.path.join(state_dir, ".env.docker"), env_docker_content)
        print("[+] Successfully grabbed Prowlarr API Key.")

    print("\n[INFO] Auto-configuring default Prowlarr indexers...")
    os.environ["PROWLARR_URL"] = "http://localhost:19696"
    os.environ["PROWLARR_API_KEY"] = prowlarr_api
    if prowlarr_api:
        try:
            from helm.core.indexer_manager import ProwlarrManager

            manager = ProwlarrManager()
            enabled = manager.seed_default_indexers()
            print(f"  [+] Activated {enabled} default Prowlarr indexer(s).")
        except Exception as e:
            print(f"[-] Could not auto-configure Prowlarr indexers: {e}")

    print("\n[INFO] Auto-configuring default Jackett indexers...")
    # Override env vars temporarily so JackettManager targets the fresh container
    os.environ["JACKETT_URL"] = "http://localhost:19117"
    os.environ["JACKETT_API_KEY"] = jackett_api
    os.environ["JACKETT_PASSWORD"] = ""

    try:
        from helm.core.indexer_manager import JackettManager

        manager = JackettManager()

        # The most reliable and widely used public trackers
        DEFAULT_INDEXERS = ["1337x", "yts", "torrentgalaxy", "nyaasi", "eztv"]
        all_indexers = manager.get_all_indexers()

        configured_count = 0
        for idx in all_indexers:
            if idx["id"] in DEFAULT_INDEXERS and not idx["configured"]:
                try:
                    manager.add_indexer(idx["id"])
                    print(f"  [+] Activated tracker: {idx['title']}")
                    configured_count += 1
                except Exception as e:
                    print(f"  [-] Failed to activate {idx['title']}: {e}")
        if configured_count == 0:
            print("  [+] Default trackers are already active.")
    except Exception as e:
        print(f"[-] Could not auto-configure indexers: {e}")

    if run_mode != "1":
        print("\nTearing down containers for Ephemeral (One-Shot) mode...")
        subprocess.run(compose_cmd + ["-f", compose_path, "stop"], check=False)

    print("\nSetup is complete! You can now use helm-torrent.")
