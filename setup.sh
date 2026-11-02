#!/usr/bin/env bash
#
# Helm setup: installs the services Helm uses (Jackett and/or Prowlarr,
# qBittorrent, FlareSolverr) with Docker, Podman, or natively.
#
#   ./setup.sh [--skip-prompts] [--config-dir DIR] [--state-dir DIR] [--downloads-dir DIR]
#   bash <(curl -sL https://raw.githubusercontent.com/Piratebird/helm/main/setup.sh)
#
# The compose file comes from src/helm/core/compose.template.yml, the same
# template `helm-torrent init` uses. From a checkout the local copy is used;
# otherwise it is downloaded from the same branch.

set -eo pipefail

HELM_IMAGE="ghcr.io/piratebird/helm:latest"
HELM_RAW_BASE="${HELM_RAW_BASE:-https://raw.githubusercontent.com/Piratebird/helm/${HELM_REF:-main}}"
TEMPLATE_PATH_IN_REPO="src/helm/core/compose.template.yml"
SETUP_TIMEOUT="${HELM_SETUP_TIMEOUT:-45}"
SETUP_POLL="${HELM_SETUP_POLL:-5}"
KEY_TRIES="${HELM_SETUP_KEY_TRIES:-15}"
KEY_POLL="${HELM_SETUP_KEY_POLL:-3}"

# Answers, set by ask() through printf -v.
install_mode="" indexer_mode="" run_mode="" qb_user="" use_vpn=""
vpn_provider="" vpn_type="" vpn_extra=""

# ---------------------------------------------------------------- output

setup_colours() {
    C_RED="" C_GREEN="" C_YELLOW="" C_CYAN="" C_BOLD="" C_RST=""
    if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
        C_RED=$'\033[31m' C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m'
        C_CYAN=$'\033[36m' C_BOLD=$'\033[1m' C_RST=$'\033[0m'
    fi
}

say()   { printf '%s\n' "$*"; }
info()  { printf '%s[INFO]%s %s\n' "$C_CYAN" "$C_RST" "$*"; }
ok()    { printf '%s[OK]%s %s\n' "$C_GREEN" "$C_RST" "$*"; }
warn()  { printf '%s[WARN]%s %s\n' "$C_YELLOW" "$C_RST" "$*" >&2; }
error() { printf '%s[ERROR]%s %s\n' "$C_RED" "$C_RST" "$*" >&2; }
die()   { error "$@"; exit 1; }
rule()  { say "==========================================="; }

# ask VAR PROMPT DEFAULT [DEFAULT_WITH_SKIP_PROMPTS]
ask() {
    local __var=$1 __prompt=$2 __default=$3 __reply=""
    if [ -n "$SKIP_PROMPTS" ]; then
        __reply="${4-$__default}"
    else
        read -r -p "$__prompt" __reply || true
        __reply="${__reply:-$__default}"
    fi
    printf -v "$__var" '%s' "$__reply"
}

ask_secret() {
    local __var=$1 __prompt=$2 __reply=""
    if [ -z "$SKIP_PROMPTS" ]; then
        read -r -s -p "$__prompt" __reply || true
        say ""
    fi
    printf -v "$__var" '%s' "$__reply"
}

is_yes() { [[ "$1" =~ ^[Yy]$ ]]; }

# ---------------------------------------------------------------- arguments

default_paths() {
    case "$(uname -s)" in
        Darwin*)
            HELM_CONFIG="$HOME/Library/Application Support/helm"
            HELM_STATE="$HOME/Library/Application Support/helm/state"
            ;;
        MINGW*|CYGWIN*|MSYS*)
            HELM_CONFIG="${APPDATA:-$HOME/AppData/Roaming}/helm"
            HELM_STATE="${LOCALAPPDATA:-$HOME/AppData/Local}/helm"
            ;;
        *)
            HELM_CONFIG="$HOME/.config/helm"
            HELM_STATE="$HOME/.local/state/helm"
            ;;
    esac
    HELM_DL="$HOME/Downloads/helm"
}

usage() {
    cat <<'EOF'
Usage: ./setup.sh [OPTIONS]
Options:
  --config-dir <path>       Set configuration directory (default: OS native config path)
  --state-dir <path>        Set state directory (default: OS native state path)
  --downloads-dir <path>    Set downloads directory (default: ~/Downloads/helm)
  --skip-prompts            Run silently with default values
EOF
}

parse_args() {
    SKIP_PROMPTS=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --config-dir|--state-dir|--downloads-dir)
                [[ $# -ge 2 ]] || die "$1 needs a path"
                case $1 in
                    --config-dir) HELM_CONFIG="$2" ;;
                    --state-dir) HELM_STATE="$2" ;;
                    --downloads-dir) HELM_DL="$2" ;;
                esac
                shift 2
                ;;
            --skip-prompts) SKIP_PROMPTS=1; shift ;;
            --help|-h) usage; exit 0 ;;
            *) usage >&2; die "Unknown option: $1" ;;
        esac
    done
    local name value
    for name in HELM_CONFIG HELM_STATE HELM_DL; do
        value="${!name}"
        if [[ "$value" == *'"'* || "$value" == *$'\n'* ]]; then
            die "$name cannot contain quotes or line breaks: $value"
        fi
    done
}

# ---------------------------------------------------------------- choices

choose_install() {
    if [ -z "$SKIP_PROMPTS" ]; then
        say "How would you like to install the required services (Jackett/Prowlarr & qBittorrent)?"
        say "  [1] Docker (Recommended) - Uses standard docker-compose, isolates dependencies."
        say "  [2] Podman               - Daemonless, rootless containers for lower memory overhead."
        say "  [3] Native               - Installs directly to your OS (/opt/Jackett & /usr/bin/qbittorrent). Highest performance but clutters OS."
    fi
    ask install_mode "Choose your installation method (1/2/3, default 1): " 1
    [ -n "$SKIP_PROMPTS" ] || say ""

    if [ -z "$SKIP_PROMPTS" ]; then
        say "Which indexer manager would you like to use?"
        say "  [1] Jackett (Default) - Stable, battle-tested."
        say "  [2] Prowlarr (BETA)   - Modern UI, becoming the standard."
        say "  [3] Both              - Run both Jackett and Prowlarr simultaneously."
    fi
    ask indexer_mode "Choose your indexer manager (1/2/3, default 1): " 1 3
    [ -n "$SKIP_PROMPTS" ] || say ""

    case "$indexer_mode" in
        2) INDEXER_MANAGER="prowlarr" ;;
        3) INDEXER_MANAGER="both" ;;
        *) indexer_mode=1; INDEXER_MANAGER="jackett" ;;
    esac
}

wants_jackett()  { [ "$indexer_mode" == "1" ] || [ "$indexer_mode" == "3" ]; }
wants_prowlarr() { [ "$indexer_mode" == "2" ] || [ "$indexer_mode" == "3" ]; }
local_jackett()  { wants_jackett && [ -z "$EXTERNAL_JACKETT_API" ]; }
local_prowlarr() { wants_prowlarr && [ -z "$EXTERNAL_PROWLARR_API" ]; }

choose_stack() {
    if [ -z "$SKIP_PROMPTS" ]; then
        say "How would you like to run Helm?"
        say "  [1] Permanent (Always-On) - Containers run 24/7 in the background."
        say "  [2] Ephemeral (One-Shot)  - Containers spin up only when downloading, then tear down."
    fi
    ask run_mode "Choose your mode (1/2, default 2): " 2
    [ -n "$SKIP_PROMPTS" ] || say ""

    JACKETT_API="" PROWLARR_API=""
    if wants_jackett && [ -z "$EXTERNAL_JACKETT_API" ]; then
        ask JACKETT_API "Enter Jackett API Key (press Enter to auto-extract later): " ""
    fi
    if wants_prowlarr && [ -z "$EXTERNAL_PROWLARR_API" ]; then
        ask PROWLARR_API "Enter Prowlarr API Key (press Enter to auto-extract later): " ""
    fi
    ask qb_user "Enter qBittorrent Username (default: admin): " admin
    [ -n "$SKIP_PROMPTS" ] || say ""
    ask use_vpn "Do you want to route qBittorrent through a VPN using Gluetun? (y/N): " n
    if is_yes "$use_vpn"; then
        choose_vpn
    fi
}

choose_vpn() {
    vpn_provider="" vpn_type="" vpn_extra=""
    say ""
    say "--- VPN Configuration ---"
    ask vpn_provider "Enter VPN Provider (e.g. nordvpn, custom): " ""
    if [ "$vpn_provider" = "protonvpn" ]; then
        warn "ProtonVPN blocks P2P/torrent traffic on its Free tier, so qBittorrent would stay stalled."
        warn "Make sure you are using a paid ProtonVPN account."
    fi
    ask vpn_type "Enter VPN Type (wireguard/openvpn) (default: wireguard): " wireguard

    if [ -z "$vpn_provider" ]; then
        error "VPN Provider cannot be empty. Disabling VPN."
        use_vpn="n"
    elif [ "$vpn_type" = "wireguard" ]; then
        local wg_key
        ask wg_key "Enter WireGuard Private Key: " ""
        if [ -z "$wg_key" ]; then
            error "WireGuard key cannot be empty. Disabling VPN."
            use_vpn="n"
        else
            vpn_extra="WIREGUARD_PRIVATE_KEY=$wg_key"
        fi
    elif [ "$vpn_type" = "openvpn" ]; then
        if [ "$vpn_provider" != "custom" ]; then
            local ovpn_user ovpn_pass
            ask ovpn_user "Enter OpenVPN Username (or token): " ""
            ask_secret ovpn_pass "Enter OpenVPN Password (leave blank if using token): "
            if [ -z "$ovpn_user" ]; then
                error "OpenVPN user cannot be empty. Disabling VPN."
                use_vpn="n"
            else
                vpn_extra="OPENVPN_USER=$ovpn_user"$'\n'"OPENVPN_PASSWORD=$ovpn_pass"
            fi
        else
            info "For 'custom' OpenVPN, place custom.conf (and auth.conf if needed) in $HELM_STATE/gluetun"
        fi
    fi
}

# ---------------------------------------------------------------- engines

# NAME and ID come from /etc/os-release.
# shellcheck disable=SC2153
print_docker_install_help() {
    if [ "$(uname)" == "Darwin" ]; then
        say "To install Docker on macOS, run:"
        say "  brew install --cask docker"
    elif [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|pop|linuxmint)
                say "To install Docker on $NAME, run:"
                say "  sudo apt update && sudo apt install -y docker.io docker-compose" ;;
            fedora|centos|rhel|almalinux|rocky)
                say "To install Docker on $NAME, run:"
                say "  sudo dnf install -y docker docker-compose" ;;
            arch|manjaro)
                say "To install Docker on $NAME, run:"
                say "  sudo pacman -S docker docker-compose" ;;
            *)
                say "Please install Docker manually for your OS ($NAME)." ;;
        esac
    fi
    say ""
    say "--- Unix Shenanigans Reminder ---"
    say "1. Start the Docker daemon:  sudo systemctl enable --now docker"
    say "2. Add user to docker group: sudo usermod -aG docker \$USER"
    say "3. Apply group changes:      newgrp docker (or just log out and log back in)"
    say ""
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        error "Docker is not installed or not in your PATH."
        say ""
        print_docker_install_help
        exit 1
    fi
    docker compose version &> /dev/null \
        || die "'docker compose' is not available. Please install the docker-compose-plugin."
    docker info > /dev/null 2>&1 \
        || die "Cannot connect to the Docker daemon. Make sure it is running and your user is in the 'docker' group."
    ENGINE="docker"
    COMPOSE_CMD=(docker compose)
    ENGINE_SOCKET="/var/run/docker.sock"
}

# shellcheck disable=SC2153
install_podman() {
    say "Installing Podman..."
    if [ "$(uname)" == "Darwin" ]; then
        command -v brew &> /dev/null || die "Please install Homebrew (https://brew.sh/) first, then re-run this script."
        brew install podman podman-desktop podman-compose
    elif [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "$ID" in
            ubuntu|debian|pop|linuxmint) sudo apt update && sudo apt install -y podman podman-compose ;;
            fedora|centos|rhel|almalinux|rocky) sudo dnf install -y podman podman-compose ;;
            arch|manjaro) sudo pacman -S --noconfirm podman podman-compose ;;
            *) die "Automatic installation is not supported for your OS ($NAME). See https://podman.io/docs/installation" ;;
        esac
    else
        die "Cannot determine OS. Please install Podman manually."
    fi
    command -v podman &> /dev/null || die "Automatic installation failed. Please install Podman manually."
}

check_podman() {
    if ! command -v podman &> /dev/null; then
        warn "Podman is not installed."
        local auto_install
        ask auto_install "Would you like this script to install Podman automatically? [Y/n]: " Y
        is_yes "$auto_install" || die "Please install Podman manually and re-run the script."
        install_podman
    fi
    if podman compose version &> /dev/null; then
        COMPOSE_CMD=(podman compose)
    elif command -v podman-compose &> /dev/null; then
        COMPOSE_CMD=(podman-compose)
    else
        die "Neither 'podman-compose' nor 'podman compose' is available. Please install podman-compose."
    fi
    ENGINE="podman"
    info "Enabling the Podman rootless socket for container orchestration..."
    systemctl --user enable --now podman.socket 2>/dev/null || true
    ENGINE_SOCKET=$(podman info --format '{{.Host.RemoteSocket.Path}}' 2>/dev/null || true)
    ENGINE_SOCKET="${ENGINE_SOCKET:-/run/user/$(id -u)/podman/podman.sock}"
}

# ---------------------------------------------------------------- native

native_confirm() {
    if [ "$(uname)" == "Darwin" ]; then
        error "Native mode on macOS is blocked."
        say "i aint messing with yall system"
        die "Please re-run the script and select Docker (Option 1) or Podman (Option 2)."
    fi
    if wants_prowlarr; then
        say ""
        warn "Prowlarr integration is currently in BETA."
        say "Installing Prowlarr natively involves complex .NET dependencies that might clutter your system."
        say "It is ${C_BOLD}HIGHLY RECOMMENDED${C_RST} to use Docker (Option 1) or Podman (Option 2) for Prowlarr instead."
        say ""
        local confirm_native
        ask confirm_native "Are you sure you want to continue with Native Installation? [y/N]: " N
        is_yes "$confirm_native" || die "Aborting native installation. Please run the script again and select Docker."
    fi
}

native_dependencies() {
    if command -v qbittorrent-nox &> /dev/null && command -v wget &> /dev/null && command -v tar &> /dev/null; then
        return 0
    fi
    warn "Some native dependencies (qbittorrent-nox, wget, libicu, etc.) are missing."
    local auto_install
    ask auto_install "Would you like this script to install them automatically? [Y/n]: " Y
    is_yes "$auto_install" || die "Please install them manually and re-run the script."

    say "Installing native dependencies..."
    [ -f /etc/os-release ] || return 0
    # shellcheck disable=SC1091
    . /etc/os-release
    case "$ID" in
        ubuntu|debian|pop|linuxmint)
            sudo apt update && sudo apt install -y qbittorrent-nox wget tar curl libicu-dev libssl-dev zlib1g ;;
        fedora|centos|rhel|almalinux|rocky)
            sudo dnf install -y qbittorrent-nox wget tar curl icu openssl zlib ;;
        arch|manjaro)
            sudo pacman -S --noconfirm qbittorrent-nox wget tar curl icu openssl zlib ;;
        *)
            say "Please ensure you have qbittorrent-nox and .NET dependencies (ICU, OpenSSL, zlib) installed manually." ;;
    esac
}

native_jackett() {
    if [ -d "/opt/Jackett" ]; then
        say "Jackett already installed at /opt/Jackett"
        return 0
    fi
    say "Installing Jackett to /opt/Jackett..."
    wget -O - -o /dev/null https://github.com/Jackett/Jackett/releases/latest/download/Jackett.Binaries.LinuxAMDx64.tar.gz \
        | sudo tar -xz -C /opt
    sudo chown "$(whoami)":"$(id -g)" -R "/opt/Jackett"
    (cd /opt/Jackett && sudo ./install_service_systemd.sh)
}

native_prowlarr() {
    if [ -d "/opt/Prowlarr" ]; then
        say "Prowlarr already installed at /opt/Prowlarr"
        return 0
    fi
    say "Installing Prowlarr to /opt/Prowlarr..."
    wget -O - -o /dev/null https://github.com/Prowlarr/Prowlarr/releases/latest/download/Prowlarr.master.linux-core-x64.tar.gz \
        | sudo tar -xz -C /opt
    sudo chown "$(whoami)":"$(id -g)" -R "/opt/Prowlarr"
    sudo tee /etc/systemd/system/prowlarr.service > /dev/null <<SVC
[Unit]
Description=Prowlarr Daemon
After=network.target

[Service]
User=$(whoami)
Group=$(id -g)
Type=simple
ExecStart=/opt/Prowlarr/Prowlarr -nobrowser
TimeoutStopSec=20
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
SVC
    sudo systemctl daemon-reload
    sudo systemctl enable --now prowlarr
}

install_native() {
    say "--- Native Installation ---"
    native_confirm
    native_dependencies
    if command -v qbittorrent-nox &> /dev/null; then
        ok "qBittorrent-nox is installed."
    else
        error "qbittorrent-nox failed to install or is not in PATH."
    fi
    if wants_jackett; then native_jackett; fi
    if wants_prowlarr; then native_prowlarr; fi
    say "Native installation completed! Ensure services are running using systemctl."
}

# ---------------------------------------------------------------- existing indexers

# Print the API key found in a Jackett ServerConfig.json or Prowlarr config.xml on stdin.
parse_api_key() {
    if [ "$1" == "prowlarr" ]; then
        sed -n 's:.*<ApiKey>\([^<]*\)</ApiKey>.*:\1:p' | head -n 1
    else
        sed -n 's/.*"APIKey"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
    fi
}

read_api_key() {
    local name=$1 container=$2 file="/config/Jackett/ServerConfig.json"
    if [ "$name" == "prowlarr" ]; then file="/config/config.xml"; fi
    "$ENGINE" exec "$container" cat "$file" 2>/dev/null | parse_api_key "$name" || true
}

detect_existing_indexer() {
    local name=$1 container port key
    container=$("$ENGINE" ps --format '{{.Names}}' 2>/dev/null | grep -i "$name" | grep -vx "$PROJECT-$name" | head -n 1 || true)
    [ -n "$container" ] || return 0
    say "[+] Found existing external $name container: $container"
    port=$("$ENGINE" port "$container" 2>/dev/null | head -n 1 | awk -F':' '{print $NF}' || true)
    if [ -z "$port" ]; then
        if [ "$name" == "prowlarr" ]; then port="9696"; else port="9117"; fi
    fi
    key=$(read_api_key "$name" "$container")
    [ -n "$key" ] || return 0
    say "[+] Successfully extracted API key from external $name!"
    if [ "$name" == "prowlarr" ]; then
        EXTERNAL_PROWLARR_PORT="$port" EXTERNAL_PROWLARR_API="$key"
    else
        EXTERNAL_JACKETT_PORT="$port" EXTERNAL_JACKETT_API="$key"
    fi
}

detect_existing_indexers() {
    EXTERNAL_JACKETT_PORT="" EXTERNAL_JACKETT_API=""
    EXTERNAL_PROWLARR_PORT="" EXTERNAL_PROWLARR_API=""
    if wants_jackett; then detect_existing_indexer jackett; fi
    if wants_prowlarr; then detect_existing_indexer prowlarr; fi
}

# ---------------------------------------------------------------- files

# render_compose TEMPLATE "BLOCK ..." prints the compose file.
# The rules match helm.core.compose.render; tests compare the two.
render_compose() {
    R_STATE="$HELM_STATE" R_DL="$HELM_DL" R_CONFIG="$HELM_CONFIG" R_SOCKET="$ENGINE_SOCKET" \
    awk -v blocks=" $2 " '
        function put(s, key, val,   i, out) {
            out = ""
            while ((i = index(s, key)) > 0) {
                out = out substr(s, 1, i - 1) val
                s = substr(s, i + length(key))
            }
            return out s
        }
        { line = $0; marker = line; sub(/^[ \t]+/, "", marker) }
        marker ~ /^#@ if / { split(marker, f, " "); depth++; on[depth] = index(blocks, " " f[3] " ") > 0; next }
        marker ~ /^#@ end/ { if (depth == 0) { bad = 1; exit } depth--; next }
        marker ~ /^#@/ { next }
        {
            for (i = 1; i <= depth; i++) if (!on[i]) next
            line = put(line, "@HELM_STATE@", ENVIRON["R_STATE"])
            line = put(line, "@HELM_DL@", ENVIRON["R_DL"])
            line = put(line, "@HELM_CONFIG@", ENVIRON["R_CONFIG"])
            line = put(line, "@ENGINE_SOCKET@", ENVIRON["R_SOCKET"])
            print line
        }
        END { if (bad || depth != 0) { print "unbalanced #@ blocks in compose template" > "/dev/stderr"; exit 1 } }
    ' "$1"
}

compose_blocks() {
    local blocks="cli"
    if local_jackett; then blocks="$blocks jackett"; fi
    if local_prowlarr; then blocks="$blocks prowlarr"; fi
    if [ -d "$PWD/src/helm" ]; then blocks="$blocks source"; fi
    if is_yes "$use_vpn"; then blocks="$blocks vpn"; else blocks="$blocks novpn"; fi
    printf '%s\n' "$blocks"
}

fetch_template() {
    local local_file="$SCRIPT_DIR/$TEMPLATE_PATH_IN_REPO" url="$HELM_RAW_BASE/$TEMPLATE_PATH_IN_REPO"
    if [ -n "${HELM_COMPOSE_TEMPLATE:-}" ]; then
        TEMPLATE_FILE="$HELM_COMPOSE_TEMPLATE"
    elif [ -f "$local_file" ]; then
        TEMPLATE_FILE="$local_file"
    else
        TEMPLATE_FILE=$(mktemp)
        TEMPLATE_TEMP=1
        info "Downloading the compose template from $url"
        if command -v curl &> /dev/null; then
            curl -fsSL "$url" -o "$TEMPLATE_FILE" || die "Could not download $url"
        else
            wget -q -O "$TEMPLATE_FILE" "$url" || die "Could not download $url"
        fi
    fi
    [ -s "$TEMPLATE_FILE" ] || die "Compose template is missing or empty: $TEMPLATE_FILE"
}

write_compose() {
    fetch_template
    local blocks
    blocks=$(compose_blocks)
    if is_yes "$use_vpn"; then say "Configuring with VPN (Gluetun)..."; else say "Configuring WITHOUT VPN..."; fi
    render_compose "$TEMPLATE_FILE" "$blocks" > "$COMPOSE_FILE.tmp"
    mv "$COMPOSE_FILE.tmp" "$COMPOSE_FILE"
    if [ -n "${TEMPLATE_TEMP:-}" ]; then rm -f "$TEMPLATE_FILE"; fi
}

# set_env_value KEY VALUE replaces KEY's line in .env.docker.
set_env_value() {
    local file="$ENV_FILE"
    (
        umask 077
        K="$1" V="$2" awk 'index($0, ENVIRON["K"] "=") == 1 { print ENVIRON["K"] "=" ENVIRON["V"]; next } { print }' \
            "$file" > "$file.tmp"
    )
    mv "$file.tmp" "$file"
}

write_env_file() {
    local jackett_url="http://jackett:9117" prowlarr_url="http://prowlarr:9696" qb_host="qbittorrent"
    if [ -n "$EXTERNAL_JACKETT_PORT" ]; then jackett_url="http://host.docker.internal:$EXTERNAL_JACKETT_PORT"; fi
    if [ -n "$EXTERNAL_PROWLARR_PORT" ]; then prowlarr_url="http://host.docker.internal:$EXTERNAL_PROWLARR_PORT"; fi
    # With a VPN, qBittorrent shares gluetun's network, so only that name resolves.
    if is_yes "$use_vpn"; then qb_host="gluetun"; fi
    (
        umask 077
        {
            say "# --- Helm Container Isolated Configuration ---"
            say "JACKETT_URL=$jackett_url"
            say "JACKETT_API_KEY=${EXTERNAL_JACKETT_API:-${JACKETT_API:-placeholder}}"
            say "PROWLARR_URL=$prowlarr_url"
            say "PROWLARR_API_KEY=${EXTERNAL_PROWLARR_API:-${PROWLARR_API:-placeholder}}"
            say "QB_WEBUI=http://$qb_host:18080"
            say "QB_USERNAME=$qb_user"
            say "QB_PASSWORD=adminadmin"
            say "INDEXER_MANAGER=$INDEXER_MANAGER"
            say "COMPOSE_PROJECT_NAME=$PROJECT"
            say "HOST_PWD=$PWD"
            if is_yes "$use_vpn"; then
                say ""
                say "# --- VPN (Gluetun) Configuration ---"
                say "VPN_SERVICE_PROVIDER=$vpn_provider"
                say "VPN_TYPE=$vpn_type"
                if [ -n "$vpn_extra" ]; then say "$vpn_extra"; fi
            fi
        } > "$ENV_FILE"
    )
    chmod 600 "$ENV_FILE"
}

seed_jackett() {
    local dir="$HELM_STATE/jackett/Jackett/Indexers" name site
    say "Seeding default Jackett indexers..."
    mkdir -p "$dir"
    for name in 1337x yts nyaasi thepiratebay; do
        case $name in
            1337x) site="https://1337x.to/" ;;
            yts) site="https://yts.mx/" ;;
            nyaasi) site="https://nyaa.si/" ;;
            thepiratebay) site="https://thepiratebay.org/" ;;
        esac
        printf '[{"id": "sitelink","type": "inputstring","name": "Site Link","value": "%s"}]\n' "$site" > "$dir/$name.json"
    done
}

seed_qbittorrent() {
    local dir="$HELM_STATE/qbittorrent/qBittorrent"
    mkdir -p "$dir"
    if [ -f "$dir/qBittorrent.conf" ]; then
        info "Keeping the existing qBittorrent configuration."
        return 0
    fi
    say "Pre-seeding qBittorrent configuration..."
    cat > "$dir/qBittorrent.conf" <<'EOF'
[Preferences]
WebUI\Password_PBKDF2="@ByteArray(ARQ77eY1NUZaQsuDHbIMCA==:0WMRkYTUWVT9wVvdDtHAjU9b3b7uB8NR1Gur2hmQCvCDpm39Q+PsIfSYvgkvpe7L5yL8YQv8EaV7t8mP308QWg==)"
WebUI\Username=admin
WebUI\AuthSubnetWhitelist=10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
WebUI\AuthSubnetWhitelistEnabled=true
WebUI\LocalHostAuth=false
EOF
}

# ---------------------------------------------------------------- stack

compose() { "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" "$@"; }

# Podman runs compose from inside the Helm image, against the Podman socket.
run_compose() {
    if [ "$ENGINE" == "podman" ]; then
        podman run --rm --entrypoint="" --security-opt label=disable \
            -v "$PWD:/app" -v "$ENGINE_SOCKET:/var/run/docker.sock" --env-file "$ENV_FILE" \
            "$HELM_IMAGE" docker compose -f /app/docker-compose.yml "$@" 2> >(grep -v "rootless netns" >&2)
    else
        compose "$@"
    fi
}

container_id() {
    "$ENGINE" ps -q -f "name=$PROJECT-$1" 2>/dev/null | head -n 1 || true
}

start_services() {
    local services=(qbittorrent flaresolverr)
    if local_jackett; then services+=(jackett); fi
    if local_prowlarr; then services+=(prowlarr); fi
    say ""
    say "Starting containers in the background to initialize configurations..."
    run_compose up -d --remove-orphans "${services[@]}"
}

wait_for_services() {
    local name="qbittorrent" elapsed=0 container state
    if local_jackett; then name="jackett"; elif local_prowlarr; then name="prowlarr"; fi
    info "Waiting for services to initialize..."
    while [ "$elapsed" -lt "$SETUP_TIMEOUT" ]; do
        container=$(container_id "$name")
        if [ -n "$container" ]; then
            state=$("$ENGINE" inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)
            if [ "$state" == "running" ]; then
                ok "Services are initialized and running!"
                return 0
            fi
            if [ "$state" == "exited" ]; then
                die "Container $name started but crashed immediately. Run '$ENGINE logs $PROJECT-$name' to see why."
            fi
        fi
        sleep "$SETUP_POLL"
        elapsed=$((elapsed + SETUP_POLL))
        if [ "$SETUP_POLL" -eq 0 ]; then elapsed=$((elapsed + 1)); fi
        say "Polling status... ($elapsed/$SETUP_TIMEOUT seconds)"
    done
    warn "Timeout reached while waiting for configurations."
    warn "Services may be experiencing slow startup times or are hanging."
}

# grab_api_key NAME prints the key read from Helm's own container, or nothing.
grab_api_key() {
    local name=$1 container key try
    for ((try = 0; try < KEY_TRIES; try++)); do
        container=$(container_id "$name")
        if [ -n "$container" ]; then
            key=$(read_api_key "$name" "$container")
            if [ -n "$key" ]; then
                printf '%s\n' "$key"
                return 0
            fi
        fi
        sleep "$KEY_POLL"
    done
}

configure_api_keys() {
    if wants_jackett; then
        JACKETT_API="${EXTERNAL_JACKETT_API:-$JACKETT_API}"
        if [ -z "$JACKETT_API" ]; then
            JACKETT_API=$(grab_api_key jackett)
            if [ -n "$JACKETT_API" ]; then
                say "[+] Successfully grabbed Jackett API Key."
                "$ENGINE" exec "$(container_id jackett)" \
                    sed -i 's|"FlareSolverrUrl":.*|"FlareSolverrUrl": "http://flaresolverr:8191",|' \
                    /config/Jackett/ServerConfig.json 2>/dev/null || true
                run_compose restart jackett
                say "[+] Jackett configured with FlareSolverr."
            else
                warn "Could not find APIKey in Jackett configuration. Using placeholder."
                JACKETT_API="your_jackett_api_key_here"
            fi
        fi
        set_env_value JACKETT_API_KEY "$JACKETT_API"
    fi
    if wants_prowlarr; then
        PROWLARR_API="${EXTERNAL_PROWLARR_API:-$PROWLARR_API}"
        if [ -z "$PROWLARR_API" ]; then
            PROWLARR_API=$(grab_api_key prowlarr)
            if [ -n "$PROWLARR_API" ]; then
                say "[+] Successfully grabbed Prowlarr API Key."
            else
                warn "Could not find APIKey in Prowlarr configuration. Using placeholder."
                PROWLARR_API="your_prowlarr_api_key_here"
            fi
        fi
        set_env_value PROWLARR_API_KEY "$PROWLARR_API"
    fi
}

# ---------------------------------------------------------------- finish

quote_words() {
    local word out=""
    for word in "$@"; do out="$out $(printf '%q' "$word")"; done
    printf '%s' "${out# }"
}

write_launcher() {
    local file="$PWD/helm-torrent.sh" run
    if [ "$ENGINE" == "podman" ]; then
        run=$(quote_words podman run -it --rm --entrypoint= --security-opt label=disable \
            --network "${PROJECT}_default" -v "$PWD:/app" -v "$HELM_CONFIG:/config/helm" \
            -e HELM_CONFIG_DIR=/config/helm -v "$ENGINE_SOCKET:/var/run/docker.sock" \
            --env-file "$ENV_FILE" "$HELM_IMAGE" python -m helm.cli)
        run="$run \"\$@\" 2> >(grep -v \"rootless netns\" >&2)"
    else
        run="exec $(quote_words "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --profile cli run --rm cli) \"\$@\""
    fi
    say "Generating helm-torrent launcher script..."
    cat > "$file" <<EOF
#!/usr/bin/env bash
# Generated by setup.sh for $ENGINE. Re-run setup.sh to change it.
set -e
export COMPOSE_PROJECT_NAME=$(printf '%q' "$PROJECT")
oneshot=0
for arg in "\$@"; do
    case "\$arg" in
        -o|--oneshot) oneshot=1 ;;
    esac
done
if [ "\$oneshot" -eq 0 ]; then
    set -- --oneshot "\$@"
fi
$run
EOF
    chmod +x "$file"
}

install_systemd_unit() {
    local compose_bin
    compose_bin=$(command -v "${COMPOSE_CMD[0]}")
    compose_bin=$(quote_words "$compose_bin" "${COMPOSE_CMD[@]:1}" -f "$COMPOSE_FILE")
    say "Creating systemd service..."
    sudo tee /etc/systemd/system/helm-app.service > /dev/null <<EOF
[Unit]
Description=Helm Application Service
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$PWD
Environment=COMPOSE_PROJECT_NAME=$PROJECT
ExecStart=$compose_bin up -d
ExecStop=$compose_bin stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable helm-app.service
    say "Systemd service 'helm-app.service' created and enabled."
}

print_summary() {
    say ""
    say "Setup is 100% complete! Everything is configured."
    say "Your config files and secrets.env were not changed."
    say "---"
    say "Security Summary & Data Locations:"
    say " - Settings:   $HELM_CONFIG/"
    say " - Databases:  $HELM_STATE/"
    say " - Secrets:    $ENV_FILE (mode 600)"
    say " - Downloads:  $HELM_DL/"
    say "---"
    if [ -f "$HELM_CONFIG/config.toml" ] || [ -f "$HELM_CONFIG/config.jsonc" ]; then
        info "The containers use INDEXER_MANAGER=$INDEXER_MANAGER from $ENV_FILE."
        info "For a helm-torrent on this machine, set search.indexer_manager = \"$INDEXER_MANAGER\" in your config file."
    fi
    say ""
}

stop_stack() {
    say "Tearing down containers for Ephemeral (One-Shot) mode..."
    if [ "$ENGINE" == "podman" ]; then
        # podman stop avoids compose tearing down the network namespace, which hits
        # "rootless netns: kill network process: permission denied".
        local service names=()
        for service in prowlarr jackett qbittorrent flaresolverr gluetun; do names+=("$PROJECT-$service"); done
        podman stop "${names[@]}" 2>/dev/null || true
    else
        run_compose stop || true
    fi
}

finish() {
    if [ "$run_mode" == "2" ]; then
        stop_stack
        write_launcher
        say "Containers stopped. Helm will spin them up automatically when you trigger a download."
        say ""
        say "To start using the app in its isolated CLI container, run:"
        say "    ./helm-torrent.sh"
    else
        say "Containers are left running 24/7 in the background."
        say ""
        local install_systemd
        ask install_systemd "Do you want to create a systemd service to start Helm automatically on boot? (y/N): " n
        if is_yes "$install_systemd"; then install_systemd_unit; fi
        say ""
        say "To start using the app in its isolated CLI container, run:"
        say "    $(quote_words "${COMPOSE_CMD[@]}" --profile cli run --rm cli)"
    fi
    rule
}

main() {
    setup_colours
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
    PROJECT="${COMPOSE_PROJECT_NAME:-helm}"
    export COMPOSE_PROJECT_NAME="$PROJECT"
    default_paths
    parse_args "$@"

    say "Using paths:"
    say "  Config:    $HELM_CONFIG"
    say "  State:     $HELM_STATE"
    say "  Downloads: $HELM_DL"
    say ""
    rule
    say "        Helm Application Setup Script      "
    rule
    say ""

    choose_install
    case "$install_mode" in
        3) install_native; exit 0 ;;
        2) check_podman ;;
        *) check_docker ;;
    esac
    ok "Container engine and compose are functional."

    COMPOSE_FILE="$PWD/docker-compose.yml"
    ENV_FILE="$HELM_STATE/.env.docker"
    mkdir -p "$HELM_STATE/jackett" "$HELM_STATE/prowlarr" "$HELM_STATE/qbittorrent" "$HELM_STATE/gluetun" "$HELM_DL" "$HELM_CONFIG"

    detect_existing_indexers
    say ""
    choose_stack
    write_compose
    write_env_file

    say "Pulling the latest helm image..."
    compose --profile cli pull cli

    if local_jackett; then seed_jackett; fi
    seed_qbittorrent
    start_services
    wait_for_services
    configure_api_keys
    print_summary
    finish
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
