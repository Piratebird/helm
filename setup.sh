#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e
# Catch errors in piped commands (e.g., command1 | command2)
set -o pipefail

echo "==========================================="
echo "        Helm Application Setup Script      "
echo "==========================================="
echo ""

echo "How would you like to install the required services (Jackett & qBittorrent)?"
echo "  [1] Docker (Recommended) - Uses standard docker-compose, isolates dependencies."
echo "  [2] Podman               - Daemonless, rootless containers for lower memory overhead."
echo "  [3] Native               - Installs directly to your OS (/opt/Jackett & /usr/bin/qbittorrent). Highest performance but clutters OS."
read -r -p "Choose your installation method (1/2/3, default 1): " install_mode
install_mode=${install_mode:-1}
echo ""

check_docker() {
    if ! command -v docker &> /dev/null; then
        echo "[-] Error: Docker is not installed or not in your PATH."
        echo ""
        if [ "$(uname)" == "Darwin" ]; then
            echo "To install Docker on macOS, run:"
            echo "  brew install --cask docker"
        elif [ -f /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            case "$ID" in
                ubuntu|debian|pop|linuxmint)
                    echo "To install Docker on $NAME, run:"
                    echo "  sudo apt update && sudo apt install -y docker.io docker-compose-v2"
                ;;
                fedora|centos|rhel|almalinux|rocky)
                    echo "To install Docker on $NAME, run:"
                    echo "  sudo dnf install -y docker docker-compose"
                ;;
                arch|manjaro)
                    echo "To install Docker on $NAME, run:"
                    echo "  sudo pacman -S docker docker-compose"
                ;;
                *)
                    echo "Please install Docker manually for your OS ($NAME)."
                ;;
            esac
        fi
        echo ""
        echo "--- Unix Shenanigans Reminder ---"
        echo "1. Start the Docker daemon:  sudo systemctl enable --now docker"
        echo "2. Add user to docker group: sudo usermod -aG docker \$USER"
        echo "3. Apply group changes:      newgrp docker (or just log out and log back in)"
        echo ""
        exit 1
    fi
    
    # Ensure docker compose is available
    if ! docker compose version &> /dev/null; then
        echo "[-] Error: 'docker compose' is not available."
        echo "Please ensure you have the docker-compose-plugin installed."
        exit 1
    fi
    
    if ! docker info > /dev/null 2>&1; then
        echo "[ERROR] Cannot connect to the Docker daemon."
        echo "Resolution: Ensure the Docker service is running and your user is in the 'docker' group."
        exit 1
    fi
    
    DOCKER_CMD="docker"
    COMPOSE_CMD="docker compose"
}

check_podman() {
    if ! command -v podman &> /dev/null; then
        echo "[-] Podman is not installed."
        read -r -p "Would you like this script to install Podman automatically? [Y/n]: " auto_install
        auto_install=${auto_install:-Y}
        
        if [[ ! "$auto_install" =~ ^[Yy]$ ]]; then
            echo "Please install Podman manually and re-run the script."
            exit 1
        fi
        
        echo "Installing Podman..."
        if [ "$(uname)" == "Darwin" ]; then
            if command -v brew &> /dev/null; then
                brew install podman podman-desktop podman-compose
            else
                echo "Please install Homebrew (https://brew.sh/) first, then re-run this script."
                exit 1
            fi
        elif [ -f /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            case "$ID" in
                ubuntu|debian|pop|linuxmint)
                    sudo apt update && sudo apt install -y podman podman-compose
                ;;
                fedora|centos|rhel|almalinux|rocky)
                    sudo dnf install -y podman podman-compose
                ;;
                arch|manjaro)
                    sudo pacman -S --noconfirm podman podman-compose
                ;;
                *)
                    echo "Automatic installation is not supported for your OS ($NAME)."
                    echo "Please install Podman manually from https://podman.io/docs/installation"
                    exit 1
                ;;
            esac
        else
            echo "Cannot determine OS. Please install Podman manually."
            exit 1
        fi
        
        if ! command -v podman &> /dev/null; then
            echo "[-] Error: Automatic installation failed. Please install Podman manually."
            exit 1
        fi
    fi
    
    # Check if podman compose is available
    if podman compose version &> /dev/null; then
        COMPOSE_CMD="podman compose"
        elif command -v podman-compose &> /dev/null; then
        COMPOSE_CMD="podman-compose"
    else
        echo "[-] Error: Neither 'podman-compose' nor 'podman compose' is available."
        echo "Please install podman-compose (e.g., sudo apt install podman-compose)."
        exit 1
    fi
    
    DOCKER_CMD="podman"
}

install_native() {
    echo "--- Native Installation ---"
    if [ "$(uname)" == "Darwin" ]; then
        echo -e "\n\033[31m[ERROR] Native mode on macOS is blocked.\033[0m"
        echo "i aint messing with yall system"
        echo "Please re-run the script and select Docker (Option 1) or Podman (Option 2)."
        exit 1
    fi
    
    needs_install=0
    if ! command -v qbittorrent-nox &> /dev/null || ! command -v wget &> /dev/null || ! command -v tar &> /dev/null; then
        needs_install=1
    fi
    
    if [ "$needs_install" -eq 1 ]; then
        echo "[-] Some native dependencies (qbittorrent-nox, wget, libicu, etc.) are missing."
        read -r -p "Would you like this script to install them automatically? [Y/n]: " auto_install
        auto_install=${auto_install:-Y}
        
        if [[ ! "$auto_install" =~ ^[Yy]$ ]]; then
            echo "Please install them manually and re-run the script."
            exit 1
        fi
        
        echo "Installing native dependencies..."
        if [ -f /etc/os-release ]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            if [[ "$ID" == "ubuntu" || "$ID" == "debian" || "$ID" == "pop" || "$ID" == "linuxmint" ]]; then
                sudo apt update && sudo apt install -y qbittorrent-nox wget tar curl libicu-dev libssl-dev zlib1g
                elif [[ "$ID" == "fedora" || "$ID" == "centos" || "$ID" == "rhel" || "$ID" == "almalinux" || "$ID" == "rocky" ]]; then
                sudo dnf install -y qbittorrent-nox wget tar curl icu openssl zlib
                elif [[ "$ID" == "arch" || "$ID" == "manjaro" ]]; then
                sudo pacman -S --noconfirm qbittorrent-nox wget tar curl icu openssl zlib
            else
                echo "Please ensure you have qbittorrent-nox and .NET dependencies (ICU, OpenSSL, zlib) installed manually."
            fi
        fi
    fi
    
    if ! command -v qbittorrent-nox &> /dev/null; then
        echo "[-] Error: qbittorrent-nox failed to install or is not in PATH."
    else
        echo "[OK] qBittorrent-nox is installed."
    fi
    
    if [ ! -d "/opt/Jackett" ]; then
        echo "Installing Jackett to /opt/Jackett..."
        cd /opt
        wget -O - -o /dev/null https://github.com/Jackett/Jackett/releases/latest/download/Jackett.Binaries.LinuxAMDx64.tar.gz | sudo tar -xz
        sudo chown "$(whoami)":"$(id -g)" -R "/opt/Jackett"
        cd Jackett
        sudo ./install_service_systemd.sh
        cd -
    else
        echo "Jackett already installed at /opt/Jackett"
    fi
    
    echo "Native installation completed! Ensure services are running using systemctl."
    exit 0
}

# Branch logic based on selection
if [ "$install_mode" == "3" ]; then
    install_native
    elif [ "$install_mode" == "2" ]; then
    check_podman
else
    check_docker
fi

echo "[OK] Container engine and compose are functional."

mkdir -p docker_data/jackett docker_data/qbittorrent docker_data/downloads
touch docker_data/config.json

echo ""
echo "How would you like to run Helm?"
echo "  [1] Permanent (Always-On) - Containers run 24/7 in the background."
echo "  [2] Ephemeral (One-Shot)  - Containers spin up only when downloading, then tear down."
read -r -p "Choose your mode (1/2, default 2): " run_mode
run_mode=${run_mode:-2}
echo ""

read -r -p "Enter Jackett API Key (press Enter to auto-extract later): " JACKETT_API
read -r -p "Enter qBittorrent Username (default: admin): " qb_user
qb_user=${qb_user:-admin}

echo ""
read -r -p "Do you want to route qBittorrent through a VPN using Gluetun? (y/N): " use_vpn

# Write docker-compose.yml
cat << 'EOF' > docker-compose.yml
services:
  jackett:
    container_name: "${COMPOSE_PROJECT_NAME:-helm}-jackett"
    image: lscr.io/linuxserver/jackett:latest
    labels:
      - "com.docker.compose.project=${COMPOSE_PROJECT_NAME:-helm}"
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
      - ${HOST_PWD:-.}/docker_data/jackett:/config
      - ${HOST_PWD:-.}/docker_data/downloads:/downloads
    ports:
      - 19117:9117
    restart: unless-stopped

  flaresolverr:
    container_name: "${COMPOSE_PROJECT_NAME:-helm}-flaresolverr"
    image: ghcr.io/flaresolverr/flaresolverr:latest
    labels:
      - "com.docker.compose.project=${COMPOSE_PROJECT_NAME:-helm}"
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

  mini-helm:
    container_name: "${COMPOSE_PROJECT_NAME:-helm}-mini-helm"
    image: ghcr.io/piratebird/helm:latest
    dns:
      - 8.8.8.8
      - 1.1.1.1
    stdin_open: true
    tty: true
    env_file:
      - ./docker_data/.env.docker
    security_opt:
      - label=disable
    volumes:
      - ${HOST_PWD:-.}/docker_data/config.json:/app/config.json
EOF

if [ "$DOCKER_CMD" == "docker" ]; then
    cat << 'EOF' >> docker-compose.yml
      - /var/run/docker.sock:/var/run/docker.sock
EOF
    elif [ "$DOCKER_CMD" == "podman" ]; then
    echo "[INFO] Enabling Podman rootless socket for container orchestration..."
    systemctl --user enable --now podman.socket 2>/dev/null || true
    # Get the podman socket path (usually /run/user/1000/podman/podman.sock)
    PODMAN_SOCK=$(podman info --format '{{.Host.RemoteSocket.Path}}' 2>/dev/null || echo "/run/user/$(id -u)/podman/podman.sock")
    cat << EOF >> docker-compose.yml
      - $PODMAN_SOCK:/var/run/docker.sock
EOF
fi

cat << 'EOF' >> docker-compose.yml
    profiles:
      - cli
EOF

if [[ "$use_vpn" =~ ^[Yy]$ ]]; then
    echo ""
    echo "--- VPN Configuration ---"
    read -r -p "Enter VPN Provider (e.g. nordvpn, custom): " vpn_provider
    read -r -p "Enter VPN Type (wireguard/openvpn) (default: wireguard): " vpn_type
    vpn_type=${vpn_type:-wireguard}
    
    vpn_extra=""
    if [ "$vpn_type" = "wireguard" ]; then
        read -r -p "Enter WireGuard Private Key: " wg_key
        vpn_extra="WIREGUARD_PRIVATE_KEY=$wg_key"
    fi
    
    echo "Configuring with VPN (Gluetun)..."
    cat << 'EOF' >> docker-compose.yml

  gluetun:
    container_name: "${COMPOSE_PROJECT_NAME:-helm}-gluetun"
    image: qmcgaw/gluetun:latest
    labels:
      - "com.docker.compose.project=${COMPOSE_PROJECT_NAME:-helm}"
      - "com.docker.compose.service=gluetun"
      - "com.docker.compose.oneoff=False"
    cap_add:
      - NET_ADMIN
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      - VPN_SERVICE_PROVIDER=${VPN_SERVICE_PROVIDER}
      - VPN_TYPE=${VPN_TYPE}
      - WIREGUARD_PRIVATE_KEY=${WIREGUARD_PRIVATE_KEY}
    ports:
      - 18080:8080
      - 6881:6881
      - 6881:6881/udp
    restart: unless-stopped

  qbittorrent:
    container_name: "${COMPOSE_PROJECT_NAME:-helm}-qbittorrent"
    image: lscr.io/linuxserver/qbittorrent:latest
    labels:
      - "com.docker.compose.project=${COMPOSE_PROJECT_NAME:-helm}"
      - "com.docker.compose.service=qbittorrent"
      - "com.docker.compose.oneoff=False"
    network_mode: "service:gluetun"
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - WEBUI_PORT=18080
    security_opt:
      - label=disable
    volumes:
      - ${HOST_PWD:-.}/docker_data/qbittorrent:/config
      - ${HOST_PWD:-.}/docker_data/downloads:/downloads
    depends_on:
      - gluetun
    restart: unless-stopped
EOF
else
    echo "Configuring WITHOUT VPN..."
    cat << 'EOF' >> docker-compose.yml

  qbittorrent:
    container_name: "${COMPOSE_PROJECT_NAME:-helm}-qbittorrent"
    image: lscr.io/linuxserver/qbittorrent:latest
    labels:
      - "com.docker.compose.project=${COMPOSE_PROJECT_NAME:-helm}"
      - "com.docker.compose.service=qbittorrent"
      - "com.docker.compose.oneoff=False"
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - WEBUI_PORT=18080
    security_opt:
      - label=disable
    volumes:
      - ${HOST_PWD:-.}/docker_data/qbittorrent:/config
      - ${HOST_PWD:-.}/docker_data/downloads:/downloads
    ports:
      - 18080:18080
      - 6881:6881
      - 6881:6881/udp
    restart: unless-stopped
EOF
fi

cat << EOF > ./docker_data/.env.docker
# --- Helm Container Isolated Configuration ---
JACKETT_URL=http://jackett:9117
JACKETT_API_KEY=\${JACKETT_API:-placeholder}
QB_WEBUI=http://qbittorrent:18080
QB_USERNAME=$qb_user
QB_PASSWORD=adminadmin
COMPOSE_PROJECT_NAME=${PWD##*/}
HOST_PWD=$PWD
EOF

if [[ "$use_vpn" =~ ^[Yy]$ ]]; then
    export VPN_SERVICE_PROVIDER="$vpn_provider"
    export VPN_TYPE="$vpn_type"
    export WIREGUARD_PRIVATE_KEY="$wg_key"
    
    cat << EOF >> ./docker_data/.env.docker

# --- VPN (Gluetun) Configuration ---
VPN_SERVICE_PROVIDER=$vpn_provider
VPN_TYPE=$vpn_type
$vpn_extra
EOF
fi

echo "Pulling the latest helm image..."
$COMPOSE_CMD --profile cli pull mini-helm

run_compose() {
    if [ "$DOCKER_CMD" == "podman" ]; then
        podman run --rm --entrypoint="" --security-opt label=disable -v "$PWD:/app" -v "$PODMAN_SOCK:/var/run/docker.sock" --env-file ./docker_data/.env.docker mini-helm docker compose "$@" 2> >(grep -v "rootless netns" >&2)
    else
        $COMPOSE_CMD "$@"
    fi
}

echo "Seeding default Jackett indexers..."
mkdir -p ./docker_data/jackett/Jackett/Indexers

cat << 'EOF' > ./docker_data/jackett/Jackett/Indexers/1337x.json
[{"id": "sitelink","type": "inputstring","name": "Site Link","value": "https://1337x.to/"}]
EOF
cat << 'EOF' > ./docker_data/jackett/Jackett/Indexers/yts.json
[{"id": "sitelink","type": "inputstring","name": "Site Link","value": "https://yts.mx/"}]
EOF
cat << 'EOF' > ./docker_data/jackett/Jackett/Indexers/nyaasi.json
[{"id": "sitelink","type": "inputstring","name": "Site Link","value": "https://nyaa.si/"}]
EOF
cat << 'EOF' > ./docker_data/jackett/Jackett/Indexers/thepiratebay.json
[{"id": "sitelink","type": "inputstring","name": "Site Link","value": "https://thepiratebay.org/"}]
EOF

echo "Pre-seeding qBittorrent configuration..."
mkdir -p ./docker_data/qbittorrent/qBittorrent/
cat << 'EOF' > ./docker_data/qbittorrent/qBittorrent/qBittorrent.conf
[Preferences]
WebUI\Password_PBKDF2="@ByteArray(ARQ77eY1NUZaQsuDHbIMCA==:0WMRkYTUWVT9wVvdDtHAjU9b3b7uB8NR1Gur2hmQCvCDpm39Q+PsIfSYvgkvpe7L5yL8YQv8EaV7t8mP308QWg==)"
WebUI\Username=admin
WebUI\AuthSubnetWhitelist=10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
WebUI\AuthSubnetWhitelistEnabled=true
WebUI\LocalHostAuth=false
EOF

echo ""
echo "Starting containers in the background to initialize configurations..."
run_compose up -d jackett qbittorrent flaresolverr

echo "[INFO] Waiting for services to initialize..."

TIMEOUT=45
ELAPSED=0

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    CRITICAL_CONTAINER=$($DOCKER_CMD ps -q -f "name=${COMPOSE_PROJECT_NAME:-helm}-jackett" | head -n 1 || true)
    
    if [ -n "$CRITICAL_CONTAINER" ]; then
        IS_RUNNING=$($DOCKER_CMD inspect -f '{{.State.Running}}' "$CRITICAL_CONTAINER" 2>/dev/null || true)
        
        if [ "$IS_RUNNING" == "true" ]; then
            echo "[OK] Services are initialized and running!"
            break
        fi
        
        IS_EXITED=$($DOCKER_CMD inspect -f '{{.State.Status}}' "$CRITICAL_CONTAINER" 2>/dev/null || true)
        if [ "$IS_EXITED" == "exited" ]; then
            echo "[ERROR] Container jackett started but crashed immediately."
            echo "Resolution: Run 'run_compose logs jackett' to inspect the failure."
            exit 1
        fi
    fi
    
    sleep 5
    ELAPSED=$((ELAPSED + 5))
    echo "Polling status... ($ELAPSED/$TIMEOUT seconds)"
done

if [ "$ELAPSED" -ge "$TIMEOUT" ]; then
    echo "[WARNING] Timeout reached while waiting for configurations."
    echo "Services may be experiencing slow startup times or are hanging."
fi

JACKETT_CONTAINER=""

if [ -z "$JACKETT_API" ]; then
    for _ in {1..15}; do
        JACKETT_CONTAINER=$($DOCKER_CMD ps -q -f "name=${COMPOSE_PROJECT_NAME:-helm}-jackett" | head -n 1 || true)
        if [ -n "$JACKETT_CONTAINER" ]; then
            extracted_api=$($DOCKER_CMD exec "$JACKETT_CONTAINER" cat /config/Jackett/ServerConfig.json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('APIKey',''))" 2>/dev/null || true)
            if [ ! -z "$extracted_api" ]; then
                JACKETT_API="$extracted_api"
                echo "[+] Successfully grabbed Jackett API Key."
                
                $DOCKER_CMD exec "$JACKETT_CONTAINER" sed -i 's|"FlareSolverrUrl":.*|"FlareSolverrUrl": "http://flaresolverr:8191",|' /config/Jackett/ServerConfig.json 2>/dev/null || true
                run_compose restart jackett
                echo "[+] Jackett configured with FlareSolverr."
                break
            fi
        fi
        sleep 3
    done
    
    if [ -z "$JACKETT_API" ]; then
        echo "[-] Could not find APIKey in Jackett configuration. Using placeholder."
        JACKETT_API="your_jackett_api_key_here"
    fi
fi

# Re-write .env.docker with the true JACKETT_API_KEY
sed -i "s|JACKETT_API_KEY=.*|JACKETT_API_KEY=$JACKETT_API|" ./docker_data/.env.docker

echo ""
echo "Setup is 100% complete! Everything is configured."
echo "Your native .env and config.json were left completely untouched."
echo "Container configs are stored safely inside the ./docker_data directory."
echo ""

if [ "$run_mode" == "2" ]; then
    echo "Tearing down containers for Ephemeral (One-Shot) mode..."
    if [ "$DOCKER_CMD" == "podman" ]; then
        # Use podman stop directly - avoids docker compose's network namespace destruction
        # which triggers the "rootless netns: kill network process: permission denied" bug
        podman stop "${COMPOSE_PROJECT_NAME:-helm}"-jackett "${COMPOSE_PROJECT_NAME:-helm}"-qbittorrent "${COMPOSE_PROJECT_NAME:-helm}"-flaresolverr "${COMPOSE_PROJECT_NAME:-helm}"-gluetun 2>/dev/null || true
    else
        run_compose stop || true
    fi
    echo "Generating helm launcher script..."
    cat << EOF > helm.sh
#!/usr/bin/env bash
if [ "$DOCKER_CMD" == "podman" ]; then
    NETWORK="${COMPOSE_PROJECT_NAME:-helm}_default"
    podman run -it --rm --entrypoint="" --security-opt label=disable --network "\$NETWORK" -v "\$PWD:/app" -v "$PODMAN_SOCK:/var/run/docker.sock" --env-file ./docker_data/.env.docker mini-helm python src/__main__.py "\$@" 2> >(grep -v "rootless netns" >&2)
else
    $COMPOSE_CMD --profile cli run --rm mini-helm "\$@"
fi
EOF
    chmod +x helm.sh
    
    echo "Containers stopped. Helm will spin them up automatically when you trigger a download."
    echo ""
    echo "To start using the app in its isolated mini container, run:"
    echo "    ./helm.sh --oneshot"
    echo "==========================================="
else
    echo "Containers are left running 24/7 in the background."
    echo ""
    read -r -p "Do you want to create a systemd service to start Helm automatically on boot? (y/N): " install_systemd
    if [[ "$install_systemd" =~ ^[Yy]$ ]]; then
        echo "Creating systemd service..."
        cat << EOF | sudo tee /etc/systemd/system/helm-app.service > /dev/null
[Unit]
Description=Helm Application Service
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$PWD
ExecStart=$(which "$COMPOSE_CMD") up -d
ExecStop=/bin/bash -c '$DOCKER_CMD stop "${COMPOSE_PROJECT_NAME:-helm}"-jackett "${COMPOSE_PROJECT_NAME:-helm}"-qbittorrent "${COMPOSE_PROJECT_NAME:-helm}"-flaresolverr 2>/dev/null || true'
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable helm-app.service
        echo "Systemd service 'helm-app.service' created and enabled."
    fi
    echo ""
    echo "To start using the app in its isolated mini container, run:"
    echo "    $COMPOSE_CMD --profile cli run --rm mini-helm"
    echo "==========================================="
fi
