#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e
# Catch errors in piped commands (e.g., command1 | command2)
set -o pipefail

echo "==========================================="
echo "        Helm Docker Setup Script           "
echo "==========================================="
echo ""

# Ensure Docker is installed before proceeding
if ! command -v docker &> /dev/null; then
    echo "[-] Error: Docker is not installed or not in your PATH."
    echo ""
    
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|pop|linuxmint)
                echo "To install Docker on $NAME, run:"
                echo "  sudo apt update && sudo apt install -y docker.io docker-compose-v2"
                ;;
            fedora)
                echo "To install Docker on $NAME, run:"
                echo "  sudo dnf install -y docker docker-compose"
                ;;
            arch|manjaro)
                echo "To install Docker on $NAME, run:"
                echo "  sudo pacman -S docker docker-compose"
                ;;
            centos|rhel|almalinux|rocky)
                echo "To install Docker on $NAME, run:"
                echo "  sudo dnf install -y docker docker-compose"
                ;;
            *)
                echo "Please install Docker manually for your OS ($NAME)."
                ;;
        esac
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "To install Docker on macOS, run:"
        echo "  brew install --cask docker"
    else
        echo "Please install Docker manually from https://docs.docker.com/get-docker/"
    fi
    
    echo ""
    echo "--- Unix Shenanigans Reminder ---"
    echo "1. Start the Docker daemon:  sudo systemctl enable --now docker"
    echo "2. Add user to docker group: sudo usermod -aG docker \$USER"
    echo "3. Apply group changes:      newgrp docker (or just log out and log back in)"
    echo ""
    echo "After completing these steps, run ./setup.sh again."
    exit 1
fi

# Ensure docker compose is available
if ! docker compose version &> /dev/null; then
    echo "[-] Error: 'docker compose' is not available."
    echo "Please ensure you have the docker-compose-plugin installed."
    exit 1
fi

echo "[INFO] Verifying Docker daemon access..."
if ! docker info > /dev/null 2>&1; then
    echo "[ERROR] Cannot connect to the Docker daemon."
    echo "Resolution: Ensure the Docker service is running and your user is in the 'docker' group."
    echo "Run: 'sudo usermod -aG docker \$USER' and 'newgrp docker'."
    exit 1
fi
echo "[OK] Docker daemon is responsive."

# Removed flaky ping tests. Docker will naturally fail if offline.
echo "[OK] Network and DNS resolution functional."

# Ensure we don't fuck with the local native environment.
# All docker-related data will go into a dedicated directory.
mkdir -p docker_data/jackett docker_data/qbittorrent docker_data/downloads
touch docker_data/config.json

# Prompt for common configurations
echo ""
echo "How would you like to run Helm?"
echo "  [1] Permanent (Always-On) - Containers run 24/7 in the background."
echo "  [2] Ephemeral (One-Shot)  - Containers spin up only when downloading, then tear down."
read -p "Choose your mode (1/2, default 2): " run_mode
run_mode=${run_mode:-2}
echo ""

read -p "Enter Jackett API Key (press Enter to auto-extract later): " JACKETT_API
read -p "Enter qBittorrent Username (default: admin): " qb_user
qb_user=${qb_user:-admin}
# Password is automatically managed by Helm

echo ""
read -p "Do you want to route qBittorrent through a VPN using Gluetun? (y/N): " use_vpn

# Write docker-compose.yml
cat << 'EOF' > docker-compose.yml
services:
  jackett:
    image: lscr.io/linuxserver/jackett:latest
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
    volumes:
      - ${PWD}/docker_data/jackett:/config:z
      - ${PWD}/docker_data/downloads:/downloads:z
    ports:
      - 19117:9117
    restart: unless-stopped

  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - LOG_LEVEL=info
      - TZ=Etc/UTC
    ports:
      - 18191:8191
    restart: unless-stopped

  mini-helm:
    build:
      context: .
      network: host
    image: mini-helm
    dns:
      - 8.8.8.8
      - 1.1.1.1
    stdin_open: true
    tty: true
    env_file:
      - ${PWD}/docker_data/.env.docker
    security_opt:
      - label=disable
    volumes:
      - ${PWD}/docker_data/config.json:/app/config.json:z
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - jackett
      - qbittorrent
    profiles:
      - cli
EOF

if [[ "$use_vpn" =~ ^[Yy]$ ]]; then
    echo ""
    echo "--- VPN Configuration ---"
    read -p "Enter VPN Provider (e.g. nordvpn, custom): " vpn_provider
    read -p "Enter VPN Type (wireguard/openvpn) (default: wireguard): " vpn_type
    vpn_type=${vpn_type:-wireguard}
    
    vpn_extra=""
    if [ "$vpn_type" = "wireguard" ]; then
        read -p "Enter WireGuard Private Key: " wg_key
        vpn_extra="WIREGUARD_PRIVATE_KEY=$wg_key"
    fi
    
    echo "Configuring with VPN (Gluetun)..."
    cat << 'EOF' >> docker-compose.yml

  gluetun:
    image: qmcgaw/gluetun:latest
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
    image: lscr.io/linuxserver/qbittorrent:latest
    network_mode: "service:gluetun"
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - WEBUI_PORT=18080
    volumes:
      - ${PWD}/docker_data/qbittorrent:/config:z
      - ${PWD}/docker_data/downloads:/downloads:z
    depends_on:
      - gluetun
    restart: unless-stopped
EOF
else
    echo "Configuring WITHOUT VPN..."
    cat << 'EOF' >> docker-compose.yml

  qbittorrent:
    image: lscr.io/linuxserver/qbittorrent:latest
    dns:
      - 8.8.8.8
      - 1.1.1.1
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - WEBUI_PORT=18080
    volumes:
      - ${PWD}/docker_data/qbittorrent:/config:z
      - ${PWD}/docker_data/downloads:/downloads:z
    ports:
      - 18080:18080
      - 6881:6881
      - 6881:6881/udp
    restart: unless-stopped
EOF
fi

# To prevent docker compose from complaining about missing files during initial load
touch ./docker_data/.env.docker

# If using VPN, export variables so Gluetun can start successfully right now
if [[ "$use_vpn" =~ ^[Yy]$ ]]; then
    export VPN_SERVICE_PROVIDER="$vpn_provider"
    export VPN_TYPE="$vpn_type"
    export WIREGUARD_PRIVATE_KEY="$wg_key"
fi

# Seed Jackett indexers with defaults so Jackett has trackers out of the box!
echo "Seeding default Jackett indexers..."
mkdir -p ./docker_data/jackett/Jackett/Indexers

# Generate basic indexer configs to make the app fully portable
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
docker compose up -d

echo "[INFO] Waiting for services to initialize..."

TIMEOUT=45
ELAPSED=0

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    CRITICAL_CONTAINER=$(docker compose ps -q jackett || true)
    
    if [ -n "$CRITICAL_CONTAINER" ]; then
        # 1. Check if the container successfully reached the 'running' state
        IS_RUNNING=$(docker inspect -f '{{.State.Running}}' "$CRITICAL_CONTAINER" 2>/dev/null || true)
        
        if [ "$IS_RUNNING" == "true" ]; then
            echo "[OK] Services are initialized and running!"
            break
        fi
        
        # 2. Check if the container crashed immediately (e.g., bad config)
        IS_EXITED=$(docker inspect -f '{{.State.Status}}' "$CRITICAL_CONTAINER" 2>/dev/null || true)
        if [ "$IS_EXITED" == "exited" ]; then
            echo "[ERROR] Container jackett started but crashed immediately."
            echo "Resolution: Run 'docker compose logs jackett' to inspect the failure."
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

# Extract Jackett API Key with a retry loop
JACKETT_CONFIG="./docker_data/jackett/Jackett/ServerConfig.json"

if [ -z "$JACKETT_API" ]; then
    for i in {1..15}; do
        if [ -f "$JACKETT_CONFIG" ]; then
            extracted_api=$(python3 -c "import json; print(json.load(open('$JACKETT_CONFIG')).get('APIKey', ''))" 2>/dev/null || true)
            if [ ! -z "$extracted_api" ]; then
                JACKETT_API="$extracted_api"
                echo "[+] Successfully grabbed Jackett API Key."
                
                # Inject FlareSolverr URL safely to avoid race conditions with Jackett saving config
                if grep -q '"FlareSolverrUrl"' "$JACKETT_CONFIG"; then
                    docker compose stop jackett
                    sed -i 's|"FlareSolverrUrl":.*|"FlareSolverrUrl": "http://flaresolverr:8191",|' "$JACKETT_CONFIG"
                    docker compose start jackett
                    echo "[+] Jackett configured with FlareSolverr."
                fi
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

# qBittorrent password is fixed by our pre-seeded config
QB_PASS="adminadmin"

# Write final isolated .env.docker file
cat << EOF > ./docker_data/.env.docker
# --- Helm Docker Isolated Configuration ---
JACKETT_URL=http://jackett:9117
JACKETT_API_KEY=$JACKETT_API
QB_WEBUI=http://qbittorrent:18080
QB_USERNAME=$qb_user
QB_PASSWORD=$QB_PASS
COMPOSE_PROJECT_NAME=${PWD##*/}
EOF

if [[ "$use_vpn" =~ ^[Yy]$ ]]; then
    cat << EOF >> ./docker_data/.env.docker

# --- VPN (Gluetun) Configuration ---
VPN_SERVICE_PROVIDER=$vpn_provider
VPN_TYPE=$vpn_type
$vpn_extra
EOF
fi

echo ""
echo "Building the mini-helm container..."
sed -i "s|\${PWD}|$(pwd)|g" docker-compose.yml
# Use legacy builder to bypass Fedora/Tailscale Buildkit DNS issues
DOCKER_BUILDKIT=0 docker compose build mini-helm

echo ""
echo "Setup is 100% complete! Everything is configured."
echo "Your native .env and config.json were left completely untouched."
echo "Docker configs are stored safely inside the ./docker_data directory."
echo ""

if [ "$run_mode" == "2" ]; then
    echo "Tearing down containers for Ephemeral (One-Shot) mode..."
    docker compose down
    echo "Containers stopped. Helm will spin them up automatically when you trigger a download."
    echo ""
    echo "To start using the app in its isolated mini container, run:"
    echo "    docker compose run --rm mini-helm --oneshot"
    echo "==========================================="
else
    echo "Containers are left running 24/7 in the background."
    echo ""
    echo "To start using the app in its isolated mini container, run:"
    echo "    docker compose run --rm mini-helm"
    echo "==========================================="
fi
