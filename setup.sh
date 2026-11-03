#!/usr/bin/env bash
#
# Helm setup: checks the host, then sets up the services Helm uses (Jackett
# and/or Prowlarr, qBittorrent, FlareSolverr) with Docker or Podman, or
# installs them natively.
#
#   ./setup.sh [--skip-prompts] [--config-dir DIR] [--state-dir DIR] [--downloads-dir DIR]
#   bash <(curl -sL https://raw.githubusercontent.com/Piratebird/helm/main/setup.sh)
#
# This script only handles the host: arguments, the container engine, native
# installs and the optional systemd unit. Everything else (compose file,
# .env.docker, API keys, launcher) is `helm-torrent init`, which runs inside
# the Helm image with your directories mounted at the same paths.
# Set HELM_SETUP_RUNNER (for example "helm-torrent") to run init on the host instead.

set -eo pipefail

HELM_IMAGE="${HELM_IMAGE:-ghcr.io/piratebird/helm:latest}"

# Answers, set by ask() through printf -v.
install_mode="" indexer_mode="" run_mode=""

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

choose_run_mode() {
    if [ -z "$SKIP_PROMPTS" ]; then
        say "How would you like to run Helm?"
        say "  [1] Permanent (Always-On) - Containers run 24/7 in the background."
        say "  [2] Ephemeral (One-Shot)  - Containers spin up only when downloading, then tear down."
    fi
    ask run_mode "Choose your mode (1/2, default 2): " 2
    [ -n "$SKIP_PROMPTS" ] || say ""
}

# ---------------------------------------------------------------- engines

# Print the package manager command for this OS: apt, dnf, pacman, brew or nothing.
package_manager() {
    if [ "$(uname)" == "Darwin" ]; then echo brew; return; fi
    [ -f /etc/os-release ] || return 0
    # shellcheck disable=SC1091
    . /etc/os-release
    case "$ID" in
        ubuntu|debian|pop|linuxmint) echo apt ;;
        fedora|centos|rhel|almalinux|rocky) echo dnf ;;
        arch|manjaro) echo pacman ;;
    esac
}

# install_command MANAGER PACKAGE... prints the install command for MANAGER.
install_command() {
    local manager=$1; shift
    case "$manager" in
        apt) echo "sudo apt update && sudo apt install -y $*" ;;
        dnf) echo "sudo dnf install -y $*" ;;
        pacman) echo "sudo pacman -S --noconfirm $*" ;;
        brew) echo "brew install $*" ;;
    esac
}

check_docker() {
    if ! command -v docker &> /dev/null; then
        error "Docker is not installed or not in your PATH."
        local manager docker_pkg="docker"
        manager=$(package_manager)
        if [ "$manager" == "apt" ]; then docker_pkg="docker.io"; fi
        say ""
        case "$manager" in
            brew) say "To install Docker on macOS, run:"; say "  brew install --cask docker" ;;
            "") say "Please install Docker manually for your OS." ;;
            *) say "To install Docker, run:"; say "  $(install_command "$manager" "$docker_pkg" docker-compose)" ;;
        esac
        say ""
        say "--- Unix Shenanigans Reminder ---"
        say "1. Start the Docker daemon:  sudo systemctl enable --now docker"
        say "2. Add user to docker group: sudo usermod -aG docker \$USER"
        say "3. Apply group changes:      newgrp docker (or just log out and log back in)"
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

check_podman() {
    if ! command -v podman &> /dev/null; then
        warn "Podman is not installed."
        local auto_install manager
        ask auto_install "Would you like this script to install Podman automatically? [Y/n]: " Y
        is_yes "$auto_install" || die "Please install Podman manually and re-run the script."
        manager=$(package_manager)
        [ -n "$manager" ] || die "Automatic installation is not supported here. See https://podman.io/docs/installation"
        if [ "$manager" == "brew" ] && ! command -v brew &> /dev/null; then
            die "Please install Homebrew (https://brew.sh/) first, then re-run this script."
        fi
        say "Installing Podman..."
        eval "$(install_command "$manager" podman podman-compose)"
        command -v podman &> /dev/null || die "Automatic installation failed. Please install Podman manually."
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

    local manager
    manager=$(package_manager)
    say "Installing native dependencies..."
    case "$manager" in
        apt) eval "$(install_command apt qbittorrent-nox wget tar curl libicu-dev libssl-dev zlib1g)" ;;
        dnf|pacman) eval "$(install_command "$manager" qbittorrent-nox wget tar curl icu openssl zlib)" ;;
        *) say "Please ensure you have qbittorrent-nox and .NET dependencies (ICU, OpenSSL, zlib) installed manually." ;;
    esac
}

# native_download NAME URL extracts a release tarball to /opt/NAME; it fails when already there.
native_download() {
    if [ -d "/opt/$1" ]; then
        say "$1 already installed at /opt/$1"
        return 1
    fi
    say "Installing $1 to /opt/$1..."
    wget -O - -o /dev/null "$2" | sudo tar -xz -C /opt
    sudo chown "$(whoami)":"$(id -g)" -R "/opt/$1"
}

native_jackett() {
    native_download Jackett https://github.com/Jackett/Jackett/releases/latest/download/Jackett.Binaries.LinuxAMDx64.tar.gz \
        || return 0
    (cd /opt/Jackett && sudo ./install_service_systemd.sh)
}

native_prowlarr() {
    native_download Prowlarr https://github.com/Prowlarr/Prowlarr/releases/latest/download/Prowlarr.master.linux-core-x64.tar.gz \
        || return 0
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

quote_words() {
    local word out=""
    for word in "$@"; do out="$out $(printf '%q' "$word")"; done
    printf '%s' "${out# }"
}

install_systemd_unit() {
    local compose_bin
    compose_bin=$(command -v "${COMPOSE_CMD[0]}")
    compose_bin=$(quote_words "$compose_bin" "${COMPOSE_CMD[@]:1}" -f "$PWD/docker-compose.yml")
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

# ---------------------------------------------------------------- init

init_arguments() {
    INIT_ARGS=(init --config-dir "$HELM_CONFIG" --state-dir "$HELM_STATE" --dl-dir "$HELM_DL"
        --engine "$ENGINE" --indexers "$INDEXER_MANAGER" --workdir "$PWD"
        --socket "$ENGINE_SOCKET" --project "$PROJECT")
    if [ "$run_mode" == "1" ]; then INIT_ARGS+=(--run-mode permanent); else INIT_ARGS+=(--run-mode oneshot); fi
    if [ -n "$SKIP_PROMPTS" ]; then INIT_ARGS+=(--skip-prompts); fi
    if [ -d "$PWD/src/helm" ]; then INIT_ARGS+=(--mount-source); fi
}

# Arguments that let the init container use the engine socket and write
# files on the host as the user running setup.
container_user_args() {
    USER_ARGS=()
    if [ "$ENGINE" == "podman" ] || [ "$(uname)" == "Darwin" ]; then
        return 0  # rootless Podman and Docker Desktop already map root to you
    fi
    if "$ENGINE" info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
        return 0
    fi
    local socket_group
    socket_group=$(stat -c %g "$ENGINE_SOCKET" 2>/dev/null || echo 0)
    USER_ARGS=(--user "$(id -u):$(id -g)" --group-add "$socket_group")
}

run_init() {
    init_arguments
    if [ -n "${HELM_SETUP_RUNNER:-}" ]; then
        # shellcheck disable=SC2086  # the runner may be several words
        HELM_DOCKER_CMD="${HELM_DOCKER_CMD:-$ENGINE}" $HELM_SETUP_RUNNER "${INIT_ARGS[@]}"
        return
    fi
    say "Pulling the latest helm image..."
    if ! "$ENGINE" pull "$HELM_IMAGE"; then
        "$ENGINE" image inspect "$HELM_IMAGE" > /dev/null 2>&1 \
            || die "Could not pull $HELM_IMAGE, and no local copy exists."
        warn "Could not pull $HELM_IMAGE; using the local copy."
    fi
    container_user_args
    local tty=() dir
    if [ -t 0 ] && [ -t 1 ]; then tty=(-t); fi
    local mounts=(-v "$ENGINE_SOCKET:/var/run/docker.sock")
    for dir in "$PWD" "$HELM_STATE" "$HELM_CONFIG" "$HELM_DL"; do
        mounts+=(-v "$dir:$dir")
    done
    "$ENGINE" run --rm -i "${tty[@]}" --security-opt label=disable \
        "${mounts[@]}" "${USER_ARGS[@]}" -w "$PWD" -e HOME=/tmp -e HELM_DOCKER_CMD=docker \
        -e COMPOSE_PROJECT_NAME="$PROJECT" -e NO_COLOR="${NO_COLOR:-}" \
        "$HELM_IMAGE" "${INIT_ARGS[@]}"
}

# ---------------------------------------------------------------- main

main() {
    setup_colours
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
    mkdir -p "$HELM_STATE" "$HELM_CONFIG" "$HELM_DL"

    choose_run_mode
    # Ask everything bash needs before init starts: init reads the rest of stdin.
    local install_systemd="n"
    if [ "$run_mode" == "1" ]; then
        ask install_systemd "Do you want to create a systemd service to start Helm automatically on boot? (y/N): " n
    fi
    run_init
    if is_yes "$install_systemd"; then install_systemd_unit; fi
    rule
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
