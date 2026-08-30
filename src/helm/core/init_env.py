import os
import subprocess
import time

from helm.core.config_manager import get_config_dir, get_dl_dir, get_log_dir

DOCKER_COMPOSE_BASE = """
services:
  jackett:
    container_name: "{project_name}-jackett"
    image: lscr.io/linuxserver/jackett:latest
    deploy:
      resources:
        limits:
          cpus: "0.50"
          memory: 256M
    labels:
      - "com.docker.compose.project={project_name}"
      - "com.docker.compose.service=jackett"
      - "com.docker.compose.oneoff=False"
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
    security_opt:
      - label=disable
    volumes:
      - "{state_dir}/jackett:/config"
      - "{dl_dir}:/downloads"
    ports:
      - 19117:9117
    restart: unless-stopped

  flaresolverr:
    container_name: "{project_name}-flaresolverr"
    image: ghcr.io/flaresolverr/flaresolverr:latest
    deploy:
      resources:
        limits:
          cpus: "0.50"
          memory: 512M
    labels:
      - "com.docker.compose.project={project_name}"
      - "com.docker.compose.service=flaresolverr"
      - "com.docker.compose.oneoff=False"
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - LOG_LEVEL=info
      - TZ=Etc/UTC
    security_opt:
      - label=disable
    ports:
      - 18191:8191
    restart: unless-stopped
"""

QBITTORRENT_BASE = """
  qbittorrent:
    container_name: "{project_name}-qbittorrent"
    image: lscr.io/linuxserver/qbittorrent:latest
    deploy:
      resources:
        limits:
          cpus: "0.50"
          memory: 256M
    labels:
      - "com.docker.compose.project={project_name}"
      - "com.docker.compose.service=qbittorrent"
      - "com.docker.compose.oneoff=False"
    {network_settings}
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - WEBUI_PORT=18080
    security_opt:
      - label=disable
    volumes:
      - "{state_dir}/qbittorrent:/config"
      - "{dl_dir}:/downloads"
    {depends_on}
    {ports}
    restart: unless-stopped
"""

GLUETUN_BASE = """
  gluetun:
    container_name: "{project_name}-gluetun"
    image: qmcgaw/gluetun:latest
    deploy:
      resources:
        limits:
          cpus: "0.25"
          memory: 128M
    labels:
      - "com.docker.compose.project={project_name}"
      - "com.docker.compose.service=gluetun"
      - "com.docker.compose.oneoff=False"
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    env_file:
      - "{state_dir}/.env.docker"
    volumes:
      - "{state_dir}/gluetun:/gluetun"
    ports:
      - 18080:8080
      - 6881:6881
      - 6881:6881/udp
    restart: unless-stopped
"""


def bootstrap_env():
    print("===========================================")
    print("        Helm Zero-Touch Bootstrapping      ")
    print("===========================================")

    config_dir = get_config_dir()
    state_dir = os.path.dirname(get_log_dir())
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

    compose_yaml = DOCKER_COMPOSE_BASE.format(project_name=project_name, state_dir=state_dir, dl_dir=dl_dir)

    if use_vpn == "y":
        compose_yaml += GLUETUN_BASE.format(project_name=project_name, state_dir=state_dir)
        qb_network = 'network_mode: "service:gluetun"'
        qb_depends = "depends_on:\n      - gluetun"
        qb_ports = ""
    else:
        qb_network = ""
        qb_depends = ""
        qb_ports = "ports:\n      - 18080:18080\n      - 6881:6881\n      - 6881:6881/udp"

    compose_yaml += QBITTORRENT_BASE.format(
        project_name=project_name,
        state_dir=state_dir,
        dl_dir=dl_dir,
        network_settings=qb_network,
        depends_on=qb_depends,
        ports=qb_ports,
    )

    compose_path = os.path.join(state_dir, "docker-compose.yml")
    with open(compose_path, "w") as f:
        f.write(compose_yaml)

    env_docker_content = f"""# --- Helm Container Isolated Configuration ---
JACKETT_URL=http://jackett:9117
JACKETT_API_KEY=placeholder
QB_WEBUI=http://qbittorrent:18080
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
    with open(os.path.join(state_dir, ".env.docker"), "w") as f:
        f.write(env_docker_content)

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
        compose_cmd + ["-f", compose_path, "up", "-d", "--remove-orphans", "jackett", "qbittorrent", "flaresolverr"],
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
        with open(os.path.join(state_dir, ".env.docker"), "w") as f:
            f.write(env_docker_content)
        print("[+] Successfully grabbed Jackett API Key.")

    if run_mode != "1":
        print("\nTearing down containers for Ephemeral (One-Shot) mode...")
        subprocess.run(compose_cmd + ["-f", compose_path, "stop"], check=False)

    print("\nSetup is complete! You can now use helm.")
