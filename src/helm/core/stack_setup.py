"""Set up the container stack: compose file, .env.docker, API keys and launcher.

``setup.sh`` checks the host (engine, native installs, systemd) and then runs
``helm-torrent init`` inside the Helm image with the engine socket and the
user's directories mounted at the same paths, so every path written here is a
host path. ``helm-torrent init`` can also run directly on a host that has the
docker CLI.

Engine commands always go through the docker CLI (``HELM_DOCKER_CMD``), which
talks to Docker or to Podman's Docker-compatible socket.
"""

import getpass
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from helm.core import compose

IMAGE = "ghcr.io/piratebird/helm:latest"
SERVICES = ("prowlarr", "jackett", "qbittorrent", "flaresolverr", "gluetun")
JACKETT_SITES = (
    ("1337x", "https://1337x.to/"),
    ("yts", "https://yts.mx/"),
    ("nyaasi", "https://nyaa.si/"),
    ("thepiratebay", "https://thepiratebay.org/"),
)
QBITTORRENT_CONF = """[Preferences]
WebUI\\Password_PBKDF2="@ByteArray(ARQ77eY1NUZaQsuDHbIMCA==:0WMRkYTUWVT9wVvdDtHAjU9b3b7uB8NR1Gur2hmQCvCDpm39Q+PsIfSYvgkvpe7L5yL8YQv8EaV7t8mP308QWg==)"
WebUI\\Username=admin
WebUI\\AuthSubnetWhitelist=10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
WebUI\\AuthSubnetWhitelistEnabled=true
WebUI\\LocalHostAuth=false
"""
KEY_FILES = {"jackett": "/config/Jackett/ServerConfig.json", "prowlarr": "/config/config.xml"}
KEY_PATTERNS = {
    "jackett": re.compile(r'"APIKey"\s*:\s*"([^"]*)"'),
    "prowlarr": re.compile(r"<ApiKey>([^<]*)</ApiKey>"),
}
DEFAULT_PORTS = {"jackett": "9117", "prowlarr": "9696"}


class SetupError(RuntimeError):
    pass


@dataclass
class Vpn:
    provider: str
    kind: str
    extra: List[str] = field(default_factory=list)


@dataclass
class Plan:
    engine: str
    indexers: str
    run_mode: str
    state: str
    config: str
    downloads: str
    workdir: str
    socket: str
    project: str = "helm"
    source: bool = False
    skip_prompts: bool = False
    jackett_api: str = ""
    prowlarr_api: str = ""
    qb_user: str = "admin"
    vpn: Optional[Vpn] = None
    external: Dict[str, Tuple[str, str]] = field(default_factory=dict)

    def wants(self, name):
        return self.indexers in (name, "both")

    def local(self, name):
        return self.wants(name) and name not in self.external

    @property
    def compose_file(self):
        return os.path.join(self.workdir, "docker-compose.yml")

    @property
    def env_file(self):
        return os.path.join(self.state, ".env.docker")


# ---------------------------------------------------------------- output and prompts


def say(text=""):
    print(text, flush=True)


def info(text):
    say(f"[INFO] {text}")


def warn(text):
    print(f"[WARN] {text}", file=sys.stderr, flush=True)


def error(text):
    print(f"[ERROR] {text}", file=sys.stderr, flush=True)


def ask(plan, prompt, default=""):
    if plan.skip_prompts:
        return default
    try:
        reply = input(prompt).strip()
    except EOFError:
        reply = ""
    return reply or default


def ask_secret(plan, prompt):
    if plan.skip_prompts:
        return ""
    if not sys.stdin.isatty():
        return ask(plan, prompt)
    try:
        return getpass.getpass(prompt)
    except EOFError:
        return ""


def is_yes(text):
    return text in ("y", "Y")


# ---------------------------------------------------------------- engine


class Engine:
    def __init__(self, command=None, poll=None):
        self.command = shlex.split(command or os.environ.get("HELM_DOCKER_CMD", "docker"))
        self.poll = float(os.environ.get("HELM_SETUP_POLL", "5") if poll is None else poll)

    def run(self, args, check=False):
        return subprocess.run(self.command + list(args), capture_output=True, text=True, check=check)

    def output(self, args):
        try:
            result = self.run(args)
        except OSError:
            return ""
        return result.stdout if result.returncode == 0 else ""

    def compose(self, plan, args):
        """Run compose and let its output through, like a person would see it."""
        result = subprocess.run(self.command + ["compose", "-f", plan.compose_file] + list(args), check=False)
        return result.returncode

    def container_id(self, plan, name):
        lines = self.output(["ps", "-q", "-f", f"name={plan.project}-{name}"]).split()
        return lines[0] if lines else ""

    def api_key(self, name, container):
        match = KEY_PATTERNS[name].search(self.output(["exec", container, "cat", KEY_FILES[name]]))
        return match.group(1) if match else ""


# ---------------------------------------------------------------- steps


def detect_existing(plan, engine):
    """Find a Jackett or Prowlarr someone else runs, so Helm uses it instead of a second copy."""
    names = engine.output(["ps", "--format", "{{.Names}}"]).split()
    for name in ("jackett", "prowlarr"):
        if not plan.wants(name):
            continue
        found = [n for n in names if name in n.lower() and n != f"{plan.project}-{name}"]
        if not found:
            continue
        container = found[0]
        say(f"[+] Found existing external {name} container: {container}")
        port_lines = engine.output(["port", container]).splitlines()
        port = port_lines[0].rsplit(":", 1)[-1].strip() if port_lines else ""
        key = engine.api_key(name, container)
        if key:
            say(f"[+] Successfully extracted API key from external {name}!")
            plan.external[name] = (port or DEFAULT_PORTS[name], key)


def choose(plan):
    if plan.local("jackett"):
        plan.jackett_api = ask(plan, "Enter Jackett API Key (press Enter to auto-extract later): ")
    if plan.local("prowlarr"):
        plan.prowlarr_api = ask(plan, "Enter Prowlarr API Key (press Enter to auto-extract later): ")
    plan.qb_user = ask(plan, "Enter qBittorrent Username (default: admin): ", "admin")
    if not plan.skip_prompts:
        say()
    if is_yes(ask(plan, "Do you want to route qBittorrent through a VPN using Gluetun? (y/N): ", "n")):
        plan.vpn = choose_vpn(plan)


def choose_vpn(plan):
    say()
    say("--- VPN Configuration ---")
    provider = ask(plan, "Enter VPN Provider (e.g. nordvpn, custom): ")
    if provider == "protonvpn":
        warn("ProtonVPN blocks P2P/torrent traffic on its Free tier, so qBittorrent would stay stalled.")
        warn("Make sure you are using a paid ProtonVPN account.")
    kind = ask(plan, "Enter VPN Type (wireguard/openvpn) (default: wireguard): ", "wireguard")
    if not provider:
        error("VPN Provider cannot be empty. Disabling VPN.")
        return None
    vpn = Vpn(provider, kind)
    if kind == "wireguard":
        key = ask(plan, "Enter WireGuard Private Key: ")
        if not key:
            error("WireGuard key cannot be empty. Disabling VPN.")
            return None
        vpn.extra = [f"WIREGUARD_PRIVATE_KEY={key}"]
    elif kind == "openvpn" and provider != "custom":
        user = ask(plan, "Enter OpenVPN Username (or token): ")
        password = ask_secret(plan, "Enter OpenVPN Password (leave blank if using token): ")
        if not user:
            error("OpenVPN user cannot be empty. Disabling VPN.")
            return None
        vpn.extra = [f"OPENVPN_USER={user}", f"OPENVPN_PASSWORD={password}"]
    elif kind == "openvpn":
        info(f"For 'custom' OpenVPN, place custom.conf (and auth.conf if needed) in {plan.state}/gluetun")
    return vpn


def write_private(path, text):
    """Write a file only the user can read; .env.docker holds passwords."""
    temporary = f"{path}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def compose_blocks(plan):
    blocks = {"cli", "vpn" if plan.vpn else "novpn"}
    blocks |= {name for name in ("jackett", "prowlarr") if plan.local(name)}
    if plan.source:
        blocks.add("source")
    return blocks


def write_compose(plan):
    say("Configuring with VPN (Gluetun)..." if plan.vpn else "Configuring WITHOUT VPN...")
    values = {
        "HELM_STATE": plan.state,
        "HELM_DL": plan.downloads,
        "HELM_CONFIG": plan.config,
        "ENGINE_SOCKET": plan.socket,
    }
    text = compose.render(compose_blocks(plan), values)
    temporary = plan.compose_file + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, plan.compose_file)


def env_values(plan):
    def url(name, service):
        if name in plan.external:
            return f"http://host.docker.internal:{plan.external[name][0]}"
        return f"http://{service}:{DEFAULT_PORTS[name]}"

    def key(name, typed):
        return plan.external[name][1] if name in plan.external else (typed or "placeholder")

    # With a VPN, qBittorrent shares gluetun's network, so only that name resolves.
    values = [
        ("JACKETT_URL", url("jackett", "jackett")),
        ("JACKETT_API_KEY", key("jackett", plan.jackett_api)),
        ("PROWLARR_URL", url("prowlarr", "prowlarr")),
        ("PROWLARR_API_KEY", key("prowlarr", plan.prowlarr_api)),
        ("QB_WEBUI", f"http://{'gluetun' if plan.vpn else 'qbittorrent'}:18080"),
        ("QB_USERNAME", plan.qb_user),
        ("QB_PASSWORD", "adminadmin"),
        ("INDEXER_MANAGER", plan.indexers),
        ("COMPOSE_PROJECT_NAME", plan.project),
        ("HOST_PWD", plan.workdir),
    ]
    return values


def write_env(plan):
    lines = ["# --- Helm Container Isolated Configuration ---"]
    lines += [f"{name}={value}" for name, value in env_values(plan)]
    if plan.vpn:
        lines += ["", "# --- VPN (Gluetun) Configuration ---"]
        lines += [f"VPN_SERVICE_PROVIDER={plan.vpn.provider}", f"VPN_TYPE={plan.vpn.kind}"] + plan.vpn.extra
    write_private(plan.env_file, "\n".join(lines) + "\n")


def set_env_value(plan, name, value):
    with open(plan.env_file, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    lines = [f"{name}={value}" if line.startswith(f"{name}=") else line for line in lines]
    write_private(plan.env_file, "\n".join(lines) + "\n")


def seed_jackett(plan):
    say("Seeding default Jackett indexers...")
    directory = os.path.join(plan.state, "jackett", "Jackett", "Indexers")
    os.makedirs(directory, exist_ok=True)
    for name, site in JACKETT_SITES:
        with open(os.path.join(directory, f"{name}.json"), "w", encoding="utf-8") as handle:
            handle.write(f'[{{"id": "sitelink","type": "inputstring","name": "Site Link","value": "{site}"}}]\n')


def seed_qbittorrent(plan):
    directory = os.path.join(plan.state, "qbittorrent", "qBittorrent")
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "qBittorrent.conf")
    if os.path.exists(path):
        info("Keeping the existing qBittorrent configuration.")
        return
    say("Pre-seeding qBittorrent configuration...")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(QBITTORRENT_CONF)


def start_services(plan, engine):
    services = ["qbittorrent", "flaresolverr"] + [name for name in ("jackett", "prowlarr") if plan.local(name)]
    say()
    say("Starting containers in the background to initialize configurations...")
    if engine.compose(plan, ["up", "-d", "--remove-orphans"] + services) != 0:
        raise SetupError("compose could not start the services. See the output above.")


def wait_for_services(plan, engine, timeout=None):
    timeout = float(os.environ.get("HELM_SETUP_TIMEOUT", "45") if timeout is None else timeout)
    name = next((n for n in ("jackett", "prowlarr") if plan.local(n)), "qbittorrent")
    info("Waiting for services to initialize...")
    deadline = time.monotonic() + timeout
    waited = 0.0
    while True:
        container = engine.container_id(plan, name)
        if container:
            state = engine.output(["inspect", "-f", "{{.State.Status}}", container]).strip()
            if state == "running":
                say("[OK] Services are initialized and running!")
                return True
            if state == "exited":
                raise SetupError(
                    f"Container {name} started but crashed immediately. "
                    f"Run 'docker logs {plan.project}-{name}' to see why."
                )
        if time.monotonic() >= deadline:
            warn("Timeout reached while waiting for configurations.")
            warn("Services may be experiencing slow startup times or are hanging.")
            return False
        time.sleep(engine.poll)
        waited += engine.poll
        say(f"Polling status... ({waited:.0f}/{timeout:.0f} seconds)")


def grab_api_key(plan, engine, name):
    tries = int(os.environ.get("HELM_SETUP_KEY_TRIES", "15"))
    poll = float(os.environ.get("HELM_SETUP_KEY_POLL", "3"))
    for _ in range(tries):
        container = engine.container_id(plan, name)
        key = engine.api_key(name, container) if container else ""
        if key:
            return key
        time.sleep(poll)
    return ""


def configure_api_keys(plan, engine):
    if plan.wants("jackett"):
        key = plan.external["jackett"][1] if "jackett" in plan.external else plan.jackett_api
        if not key:
            key = grab_api_key(plan, engine, "jackett")
            if key:
                say("[+] Successfully grabbed Jackett API Key.")
                engine.run(
                    [
                        "exec",
                        engine.container_id(plan, "jackett"),
                        "sed",
                        "-i",
                        's|"FlareSolverrUrl":.*|"FlareSolverrUrl": "http://flaresolverr:8191",|',
                        KEY_FILES["jackett"],
                    ]
                )
                engine.compose(plan, ["restart", "jackett"])
                say("[+] Jackett configured with FlareSolverr.")
            else:
                warn("Could not find APIKey in Jackett configuration. Using placeholder.")
                key = "your_jackett_api_key_here"
        plan.jackett_api = key
        set_env_value(plan, "JACKETT_API_KEY", key)
    if plan.wants("prowlarr"):
        key = plan.external["prowlarr"][1] if "prowlarr" in plan.external else plan.prowlarr_api
        if not key:
            key = grab_api_key(plan, engine, "prowlarr")
            if key:
                say("[+] Successfully grabbed Prowlarr API Key.")
            else:
                warn("Could not find APIKey in Prowlarr configuration. Using placeholder.")
                key = "your_prowlarr_api_key_here"
        plan.prowlarr_api = key
        set_env_value(plan, "PROWLARR_API_KEY", key)


def launcher_text(plan):
    if plan.engine == "podman":
        words = [
            "podman", "run", "-it", "--rm", "--entrypoint=", "--security-opt", "label=disable",
            "--network", f"{plan.project}_default", "-v", f"{plan.workdir}:/app",
            "-v", f"{plan.config}:/config/helm", "-e", "HELM_CONFIG_DIR=/config/helm",
            "-v", f"{plan.socket}:/var/run/docker.sock", "--env-file", plan.env_file,
            IMAGE, "python", "-m", "helm.cli",
        ]  # fmt: skip
        run = " ".join(shlex.quote(w) for w in words) + ' "$@" 2> >(grep -v "rootless netns" >&2)'
    else:
        words = ["docker", "compose", "-f", plan.compose_file, "--profile", "cli", "run", "--rm", "cli"]
        run = "exec " + " ".join(shlex.quote(w) for w in words) + ' "$@"'
    return f"""#!/usr/bin/env bash
# Generated by helm-torrent init for {plan.engine}. Re-run setup to change it.
set -e
export COMPOSE_PROJECT_NAME={shlex.quote(plan.project)}
oneshot=0
for arg in "$@"; do
    case "$arg" in
        -o|--oneshot) oneshot=1 ;;
    esac
done
if [ "$oneshot" -eq 0 ]; then
    set -- --oneshot "$@"
fi
{run}
"""


def write_launcher(plan):
    say("Generating helm-torrent launcher script...")
    path = os.path.join(plan.workdir, "helm-torrent.sh")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(launcher_text(plan))
    os.chmod(path, 0o755)


def stop_stack(plan, engine):
    say("Tearing down containers for Ephemeral (One-Shot) mode...")
    if plan.engine == "podman":
        # podman stop avoids compose tearing down the network namespace, which hits
        # "rootless netns: kill network process: permission denied".
        engine.run(["stop"] + [f"{plan.project}-{name}" for name in SERVICES])
    else:
        engine.compose(plan, ["stop"])


def print_summary(plan):
    say()
    say("Setup is 100% complete! Everything is configured.")
    say("Your config files and secrets.env were not changed.")
    say("---")
    say("Security Summary & Data Locations:")
    say(f" - Settings:   {plan.config}/")
    say(f" - Databases:  {plan.state}/")
    say(f" - Secrets:    {plan.env_file} (mode 600)")
    say(f" - Downloads:  {plan.downloads}/")
    say("---")
    if any(os.path.exists(os.path.join(plan.config, name)) for name in ("config.toml", "config.jsonc")):
        info(f"The containers use INDEXER_MANAGER={plan.indexers} from {plan.env_file}.")
        info(f'For a helm-torrent on this machine, set search.indexer_manager = "{plan.indexers}" in your config file.')
    say()


def finish(plan, engine):
    if plan.run_mode == "oneshot":
        stop_stack(plan, engine)
        write_launcher(plan)
        say("Containers stopped. Helm will spin them up automatically when you trigger a download.")
        say()
        say("To start using the app in its isolated CLI container, run:")
        say("    ./helm-torrent.sh")
    else:
        say("Containers are left running 24/7 in the background.")
        say()
        say("To start using the app in its isolated CLI container, run:")
        say(f"    {plan.engine} compose --profile cli run --rm cli")


def run_setup(plan, engine=None):
    engine = engine or Engine()
    for name in (plan.state, plan.downloads, plan.config):
        compose.check_value("path", name)
    for name in ("jackett", "prowlarr", "qbittorrent", "gluetun"):
        os.makedirs(os.path.join(plan.state, name), exist_ok=True)
    os.makedirs(plan.downloads, exist_ok=True)
    os.makedirs(plan.config, exist_ok=True)

    detect_existing(plan, engine)
    choose(plan)
    write_compose(plan)
    write_env(plan)
    if plan.local("jackett"):
        seed_jackett(plan)
    seed_qbittorrent(plan)
    start_services(plan, engine)
    wait_for_services(plan, engine)
    configure_api_keys(plan, engine)
    print_summary(plan)
    finish(plan, engine)


# ---------------------------------------------------------------- command line


def add_arguments(parser):
    parser.add_argument("--engine", choices=("docker", "podman"), help="Container engine (asked when missing)")
    parser.add_argument("--indexers", choices=("jackett", "prowlarr", "both"), help="Indexer manager to run")
    parser.add_argument("--run-mode", choices=("permanent", "oneshot"), help="Keep containers running, or not")
    parser.add_argument("--workdir", help="Where docker-compose.yml and helm-torrent.sh go (default: state dir)")
    parser.add_argument("--socket", help="Engine socket path on the host")
    parser.add_argument("--project", help="Container name prefix (default: COMPOSE_PROJECT_NAME or helm)")
    parser.add_argument("--mount-source", action="store_true", help="Mount <workdir>/src into the CLI container")
    parser.add_argument("--skip-prompts", action="store_true", help="Use defaults instead of asking")


def default_socket(engine):
    if engine == "podman":
        found = Engine("podman").output(["info", "--format", "{{.Host.RemoteSocket.Path}}"]).strip()
        return found or f"/run/user/{os.getuid()}/podman/podman.sock"
    return "/var/run/docker.sock"


def engine_command(engine):
    """The docker CLI talks to both engines; fall back to podman's own CLI without it."""
    if os.environ.get("HELM_DOCKER_CMD"):
        return os.environ["HELM_DOCKER_CMD"]
    return "docker" if shutil.which("docker") or engine == "docker" else "podman"


def plan_from_args(args, settings_paths):
    """Build a Plan from ``helm-torrent init`` arguments, asking for anything missing."""
    state, config, downloads = settings_paths
    skip = bool(args.skip_prompts)
    probe = Plan("docker", "jackett", "oneshot", state, config, downloads, "", "", skip_prompts=skip)

    engine = args.engine
    if engine is None:
        reply = ask(probe, "Container engine: [1] Docker (default) [2] Podman: ", "1")
        engine = "podman" if reply == "2" else "docker"
    indexers = args.indexers
    if indexers is None:
        reply = ask(probe, "Indexer manager: [1] Jackett (default) [2] Prowlarr [3] Both: ", "3" if skip else "1")
        indexers = {"2": "prowlarr", "3": "both"}.get(reply, "jackett")
    run_mode = args.run_mode
    if run_mode is None:
        reply = ask(probe, "Run mode: [1] Permanent [2] Ephemeral one-shot (default): ", "2")
        run_mode = "permanent" if reply == "1" else "oneshot"

    return Plan(
        engine=engine,
        indexers=indexers,
        run_mode=run_mode,
        state=state,
        config=config,
        downloads=downloads,
        workdir=os.path.abspath(args.workdir or state),
        socket=args.socket or default_socket(engine),
        project=args.project or os.environ.get("COMPOSE_PROJECT_NAME") or "helm",
        source=bool(args.mount_source),
        skip_prompts=skip,
    )


def main(args):
    from helm.core.config_manager import get_config_dir, get_dl_dir, get_state_dir

    paths = (
        os.path.abspath(get_state_dir()),
        os.path.abspath(get_config_dir()),
        os.path.abspath(get_dl_dir()),
    )
    try:
        plan = plan_from_args(args, paths)
        os.environ["COMPOSE_PROJECT_NAME"] = plan.project
        run_setup(plan, Engine(engine_command(plan.engine)))
    except (SetupError, compose.TemplateError) as problem:
        error(str(problem))
        return 1
    return 0
