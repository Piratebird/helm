#!/usr/bin/env bash
# Standalone: bash <(curl -fsSL https://raw.githubusercontent.com/Piratebird/helm/main/uninstall.sh) --help
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: uninstall.sh [OPTIONS]
Preview by default. Select components and pass --yes to remove them.
  --containers             Remove this Compose project's containers and networks
  --volumes                Also delete labeled named volumes and ALL their contents
  --config                 Remove config.json and secrets.env; preserve other files
  --state                  Remove known service data, generated Compose files and logs
  --package                Uninstall helm-torrent using HELM_PYTHON (default: python3)
  --engine docker|podman   Container engine (otherwise detected)
  --project NAME           Compose project (default: COMPOSE_PROJECT_NAME or helm)
  --config-dir PATH        Override Helm torrent configuration directory
  --state-dir PATH         Override Helm torrent service data directory
  --downloads-dir PATH     Protect a custom host download directory from deletion
  --yes                    Apply the removal plan
  --dry-run                Always preview, even with --yes
  --help                   Show this help
Host downloads, user plugins, source files, images and system services are preserved.
Disable any helm-app.service separately before removing a managed stack.
Kubernetes Helm is never invoked or uninstalled.
HELP
}

die() { echo "Error: $*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "$1 requires a value"; }

case "$(uname -s)" in
    Darwin*)
        config_dir="${HELM_CONFIG_DIR:-$HOME/Library/Application Support/helm}"
        state_dir="${HELM_STATE_DIR:-$HOME/Library/Application Support/helm/state}"
        ;;
    MINGW*|CYGWIN*|MSYS*)
        config_dir="${HELM_CONFIG_DIR:-${APPDATA:-$HOME/AppData/Roaming}/helm}"
        state_dir="${HELM_STATE_DIR:-${LOCALAPPDATA:-$HOME/AppData/Local}/helm}"
        ;;
    *)
        config_dir="${HELM_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/helm}"
        state_dir="${HELM_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/helm}"
        ;;
esac
downloads_dir="${HELM_DL_DIR:-$HOME/Downloads/helm}"
engine=""
project="${COMPOSE_PROJECT_NAME:-helm}"
remove_containers=false
remove_volumes=false
remove_config=false
remove_state=false
remove_package=false
apply=false
dry_run=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --containers) remove_containers=true; shift ;;
        --volumes) remove_volumes=true; shift ;;
        --config) remove_config=true; shift ;;
        --state) remove_state=true; shift ;;
        --package) remove_package=true; shift ;;
        --yes) apply=true; shift ;;
        --dry-run) dry_run=true; shift ;;
        --engine) need_value "$@"; engine="$2"; shift 2 ;;
        --project) need_value "$@"; project="$2"; shift 2 ;;
        --config-dir) need_value "$@"; config_dir="$2"; shift 2 ;;
        --state-dir) need_value "$@"; state_dir="$2"; shift 2 ;;
        --downloads-dir) need_value "$@"; downloads_dir="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done
$remove_containers || $remove_config || $remove_state || $remove_package || die "Select at least one component; see --help"
! $remove_volumes || $remove_containers || die "--volumes requires --containers"
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "Invalid Compose project name"
[[ -z "$engine" || "$engine" == docker || "$engine" == podman ]] || die "--engine must be docker or podman"

# Resolve existing symlinks in parents, including for a missing final directory.
canonical_path() {
    local path="$1" parent leaf
    [[ "$path" == /* ]] || path="$PWD/$path"
    if [[ -d "$path" ]]; then
        (cd -- "$path" && pwd -P)
    else
        parent="$(dirname -- "$path")"
        leaf="$(basename -- "$path")"
        [[ "$parent" != "$path" ]] || die "Cannot resolve path: $path"
        parent="$(canonical_path "$parent")"
        case "$leaf" in
            .) echo "$parent" ;;
            ..) dirname -- "$parent" ;;
            *) echo "${parent%/}/$leaf" ;;
        esac
    fi
}

protected_home="$(canonical_path "$HOME")"
protected_cwd="$(pwd -P)"
protected_downloads="$(canonical_path "$downloads_dir")"
check_path() {
    local path="$1" resolved protected
    resolved="$(canonical_path "$path")"
    [[ "$resolved" != / ]] || die "Refusing unsafe removal path: $path"
    for protected in "$protected_home" "$protected_cwd" "$protected_downloads"; do
        [[ "$protected" != "$resolved" && "$protected" != "$resolved/"* ]] || die "Refusing unsafe removal path: $path"
    done
}

paths=()
if $remove_config; then
    [[ ! -L "$config_dir" ]] || die "Configuration directory must not be a symlink"
    config_dir="$(canonical_path "$config_dir")"
    check_path "$config_dir"
    paths+=("$config_dir/config.json" "$config_dir/secrets.env")
fi
if $remove_state; then
    [[ ! -L "$state_dir" ]] || die "State directory must not be a symlink"
    state_dir="$(canonical_path "$state_dir")"
    check_path "$state_dir"
    for name in docker-compose.yml .env.docker jackett prowlarr qbittorrent flaresolverr gluetun logs; do
        paths+=("$state_dir/$name")
    done
    for path in "$state_dir"/helm.log*; do
        [[ ! -e "$path" && ! -L "$path" ]] || paths+=("$path")
    done
fi
# Check every directory before performing ANY mutations. Artifact symlinks are
# unlinked rather than followed; unrelated files in the roots are left alone.
for path in "${paths[@]}"; do
    [[ -L "$path" ]] || check_path "$path"
done

container_ids=()
network_ids=()
volume_ids=()
if $remove_containers; then
    if [[ -z "$engine" ]]; then
        if command -v docker >/dev/null 2>&1; then engine=docker
        elif command -v podman >/dev/null 2>&1; then engine=podman
        else die "No Docker/Podman found; omit --containers or install an engine"
        fi
    fi
    label="label=com.docker.compose.project=$project"
    output="$("$engine" ps -aq --filter "$label")"
    while IFS= read -r id; do [[ -z "$id" ]] || container_ids+=("$id"); done <<< "$output"
    output="$("$engine" network ls -q --filter "$label")"
    while IFS= read -r id; do [[ -z "$id" ]] || network_ids+=("$id"); done <<< "$output"
    if $remove_volumes; then
        output="$("$engine" volume ls -q --filter "$label")"
        while IFS= read -r id; do [[ -z "$id" ]] || volume_ids+=("$id"); done <<< "$output"
    fi
fi
if $remove_package; then
    python_cmd="${HELM_PYTHON:-python3}"
    command -v "$python_cmd" >/dev/null 2>&1 || die "Python not found: $python_cmd"
fi

run() {
    printf '  Run:'
    printf ' %q' "$@"
    printf '\n'
    if $apply && ! $dry_run; then "$@"; fi
}
echo "Removal plan (host download directory is preserved):"
if $remove_volumes; then echo "  Selected named volumes and all their contents will be deleted."; fi
if [[ ${#container_ids[@]} -gt 0 ]]; then run "$engine" container rm -f "${container_ids[@]}"; fi
if [[ ${#network_ids[@]} -gt 0 ]]; then run "$engine" network rm "${network_ids[@]}"; fi
if [[ ${#volume_ids[@]} -gt 0 ]]; then run "$engine" volume rm "${volume_ids[@]}"; fi
for path in "${paths[@]}"; do
    if [[ -L "$path" || -f "$path" ]]; then run rm -f -- "$path"
    elif [[ -d "$path" ]]; then run rm -rf -- "$path"
    fi
done
if $remove_package; then run "$python_cmd" -m pip uninstall -y helm-torrent; fi
if $apply && ! $dry_run; then
    echo "Selected components removed. Downloads, user plugins, images and system services were not selected."
else
    echo "Preview only. Pass --yes without --dry-run to apply."
fi
