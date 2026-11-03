"""Run the real setup.sh and helm-torrent init against fake engine binaries.

``HELM_SETUP_RUNNER`` makes setup.sh run init on the host instead of inside
the image, so these tests see the files the containerised run would write.
"""

import itertools
import os
import shutil
import site
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from helm.stack import compose, stack_setup

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
REAL_DOCKER = shutil.which("docker")
BASH = shutil.which("bash")
RUNNER = f"{sys.executable} -m helm.cli"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

FAKE_ENGINE = r"""#!/usr/bin/env bash
name=$(basename "$0")
printf '%s %s\n' "$name" "$*" >> "$FAKE_LOG"
case "$1" in
    compose) [ "$2" == "version" ] && exit 0; exit "${FAKE_COMPOSE_STATUS:-0}" ;;
    info)
        if [ "$2" == "--format" ]; then
            case "$3" in
                *RemoteSocket*) echo "/run/fake/podman.sock" ;;
                *SecurityOptions*) echo "${FAKE_SECURITY:-[name=seccomp]}" ;;
            esac
        fi
        exit 0 ;;
    ps)
        if [ "$2" == "--format" ]; then
            [ -n "$FAKE_EXTERNAL" ] && echo "$FAKE_EXTERNAL"
            exit 0
        fi
        filter="$4"
        echo "id-${filter##*-}"
        exit 0 ;;
    inspect) echo "${FAKE_STATE:-running}"; exit 0 ;;
    port) echo "9117/tcp -> 0.0.0.0:29117"; exit 0 ;;
    exec)
        container="$2"
        if [ "$3" == "cat" ] && [ "$4" == "/config/Jackett/ServerConfig.json" ]; then
            printf '{\n  "APIKey": "jkey-%s",\n  "FlareSolverrUrl": ""\n}\n' "$container"
        elif [ "$3" == "cat" ] && [ "$4" == "/config/config.xml" ]; then
            printf '<Config>\n  <ApiKey>pkey-%s</ApiKey>\n</Config>\n' "$container"
        fi
        exit 0 ;;
    *) exit 0 ;;
esac
"""


def make_bin(directory, name, body):
    path = directory / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class Box:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name in ("docker", "podman"):
            make_bin(bin_dir, name, FAKE_ENGINE)
        make_bin(bin_dir, "systemctl", '#!/bin/sh\necho "systemctl $*" >> "$FAKE_LOG"\n')
        make_bin(bin_dir, "sudo", '#!/bin/sh\necho "sudo $*" >> "$FAKE_LOG"\ncat > /dev/null\n')
        self.work = tmp_path / "work dir"
        self.work.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.log = tmp_path / "calls.log"
        self.state = tmp_path / "state dir"
        self.config = tmp_path / "config"
        self.dl = tmp_path / "dl"
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "PYTHONPATH": str(ROOT / "src"),
            # HOME changes below; keep this interpreter's user site-packages.
            "PYTHONUSERBASE": site.getuserbase(),
            "FAKE_LOG": str(self.log),
            "FAKE_EXTERNAL": "",
            "NO_COLOR": "1",
            "HELM_SETUP_RUNNER": RUNNER,
            "HELM_SETUP_POLL": "0",
            "HELM_SETUP_KEY_POLL": "0",
            "HELM_SETUP_KEY_TRIES": "2",
        }

    def run(self, *args, script=SETUP, stdin="", extra=None, dirs=True):
        argv = [BASH, str(script)]
        if dirs:
            argv += ["--state-dir", str(self.state), "--config-dir", str(self.config), "--downloads-dir", str(self.dl)]
        env = {**self.env, **(extra or {})}
        env = {key: value for key, value in env.items() if value is not None}
        return subprocess.run(
            argv + list(args), cwd=self.work, env=env, input=stdin, capture_output=True, text=True, timeout=120
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def env_file(self):
        lines = (self.state / ".env.docker").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))

    def values(self, socket="/var/run/docker.sock"):
        return {
            "HELM_STATE": str(self.state),
            "HELM_DL": str(self.dl),
            "HELM_CONFIG": str(self.config),
            "ENGINE_SOCKET": socket,
        }

    def compose_text(self):
        return (self.work / "docker-compose.yml").read_text()


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def all_block_sets():
    for cli, jackett, prowlarr, source, vpn in itertools.product([0, 1], repeat=5):
        blocks = {"vpn" if vpn else "novpn"}
        for on, name in ((cli, "cli"), (jackett, "jackett"), (prowlarr, "prowlarr"), (source, "source")):
            if on:
                blocks.add(name)
        yield blocks


# ---------------------------------------------------------------- template


def test_rendered_files_have_the_expected_services():
    vals = {"HELM_STATE": "/s", "HELM_DL": "/d", "HELM_CONFIG": "/c", "ENGINE_SOCKET": "/sock"}
    for blocks in all_block_sets():
        text = compose.render(blocks, vals)
        assert "#@" not in text and "@HELM" not in text
        data = yaml.safe_load(text)
        expected = {"flaresolverr", "qbittorrent"} | (blocks & {"jackett", "prowlarr", "cli"})
        if "vpn" in blocks:
            expected.add("gluetun")
        assert set(data["services"]) == expected
        qb = data["services"]["qbittorrent"]
        if "vpn" in blocks:
            assert qb["network_mode"] == "service:gluetun" and "ports" not in qb
            assert "18080:18080" in data["services"]["gluetun"]["ports"]
        else:
            assert "network_mode" not in qb and "18080:18080" in qb["ports"]
        if "cli" in blocks:
            cli = data["services"]["cli"]
            assert "/c:/config/helm" in cli["volumes"]
            assert "HELM_CONFIG_DIR=/config/helm" in cli["environment"]
            assert "/sock:/var/run/docker.sock" in cli["volumes"]
            assert any(v.endswith("/src:/app/src") for v in cli["volumes"]) == ("source" in blocks)


def test_placeholders_are_replaced_literally():
    vals = {"HELM_STATE": "/s & \\1 $x", "HELM_DL": "/@HELM_STATE@", "HELM_CONFIG": "/c", "ENGINE_SOCKET": "/k"}
    text = compose.render({"jackett", "novpn"}, vals)
    assert '"/s & \\1 $x/jackett:/config"' in text
    assert '"/@HELM_STATE@:/downloads"' in text


@pytest.mark.parametrize("template", ["a\n#@ end\n", "a\n#@ if cli\nb\n"])
def test_unbalanced_templates_fail(template):
    with pytest.raises(compose.TemplateError):
        compose.render({"novpn"}, {name: "/x" for name in compose.PLACEHOLDERS}, template=template)


def test_nested_blocks_need_every_parent_on():
    template = "#@ if cli\nA\n#@ if source\nB\n#@ end\nC\n#@ end\n#@ comment\nD\n"
    vals = {name: "" for name in compose.PLACEHOLDERS}
    assert compose.render({"novpn", "source"}, vals, template=template) == "D\n"
    assert compose.render({"novpn", "cli"}, vals, template=template) == "A\nC\nD\n"
    assert compose.render({"novpn", "cli", "source"}, vals, template=template) == "A\nB\nC\nD\n"


@pytest.mark.parametrize(
    "blocks, vals, message",
    [
        ({"novpn", "bogus"}, {}, "Unknown"),
        ({"vpn", "novpn"}, {}, "exactly one"),
        (set(), {}, "exactly one"),
        ({"novpn"}, {"HELM_STATE": "/s"}, "Missing"),
        ({"novpn"}, {"HELM_STATE": 'a"b', "HELM_DL": "", "HELM_CONFIG": "", "ENGINE_SOCKET": ""}, "quotes"),
    ],
)
def test_render_rejects_bad_input(blocks, vals, message):
    with pytest.raises(compose.TemplateError, match=message):
        compose.render(blocks, vals)


@pytest.mark.skipif(REAL_DOCKER is None, reason="docker compose is not installed")
def test_docker_compose_accepts_every_rendered_file(tmp_path):
    (tmp_path / ".env.docker").write_text("A=1\n")
    vals = {"HELM_STATE": str(tmp_path), "HELM_DL": "/d", "HELM_CONFIG": "/c", "ENGINE_SOCKET": "/sock"}
    for index, blocks in enumerate(all_block_sets()):
        path = tmp_path / f"compose-{index}.yml"
        path.write_text(compose.render(blocks, vals))
        result = subprocess.run(
            [REAL_DOCKER, "compose", "-f", str(path), "--profile", "cli", "config", "-q"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode and "is not a docker command" in result.stderr:
            pytest.skip("the compose plugin is not installed")
        assert result.returncode == 0, (sorted(blocks), result.stderr)


def test_template_ships_as_package_data():
    assert '"helm.stack" = ["compose.template.yml"]' in (ROOT / "pyproject.toml").read_text()
    assert compose.TEMPLATE.is_file()


# ---------------------------------------------------------------- setup.sh


def test_help_and_unknown_option(box):
    result = box.run("--help", dirs=False)
    assert result.returncode == 0 and "--skip-prompts" in result.stdout
    result = box.run("--bogus", dirs=False)
    assert result.returncode == 1 and "Unknown option: --bogus" in result.stderr
    result = box.run("--state-dir", dirs=False)
    assert result.returncode == 1 and "needs a path" in result.stderr
    result = box.run("--skip-prompts", "--state-dir", '/tmp/a"b', dirs=False)
    assert result.returncode == 1 and "cannot contain quotes" in result.stderr
    assert box.calls() == []


def test_skip_prompts_with_docker(box):
    result = box.run("--skip-prompts")
    assert result.returncode == 0, result.stderr

    compose_file = box.work / "docker-compose.yml"
    assert box.compose_text() == compose.render({"cli", "jackett", "prowlarr", "novpn"}, box.values())

    env = box.env_file()
    assert env["JACKETT_API_KEY"] == "jkey-id-jackett"
    assert env["PROWLARR_API_KEY"] == "pkey-id-prowlarr"
    assert env["JACKETT_URL"] == "http://jackett:9117"
    assert env["PROWLARR_URL"] == "http://prowlarr:9696"
    assert env["QB_WEBUI"] == "http://qbittorrent:18080"
    assert env["QB_USERNAME"] == "admin"
    assert env["INDEXER_MANAGER"] == "both"
    assert env["COMPOSE_PROJECT_NAME"] == "helm"
    assert env["HOST_PWD"] == str(box.work)
    assert stat.S_IMODE((box.state / ".env.docker").stat().st_mode) == 0o600

    calls = box.calls()
    file_arg = f"-f {compose_file}"
    assert f"docker compose {file_arg} up -d --remove-orphans qbittorrent flaresolverr jackett prowlarr" in calls
    assert f"docker compose {file_arg} restart jackett" in calls
    assert f"docker compose {file_arg} stop" in calls
    assert not any(call.startswith(("sudo", "docker pull", "docker run")) for call in calls)

    assert (box.state / "jackett/Jackett/Indexers/yts.json").is_file()
    assert "WebUI\\Username=admin" in (box.state / "qbittorrent/qBittorrent/qBittorrent.conf").read_text()
    assert not (box.config / "config.json").exists()
    for path in (box.config, box.state, box.dl):
        assert f"{path}/" in result.stdout
    assert "./helm-torrent.sh" in result.stdout


def test_docker_launcher_adds_oneshot_once(box):
    assert box.run("--skip-prompts").returncode == 0
    launcher = box.work / "helm-torrent.sh"
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755
    assert subprocess.run([BASH, "-n", str(launcher)], check=False).returncode == 0
    if shutil.which("shellcheck"):
        result = subprocess.run(["shellcheck", str(launcher)], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout

    base = f"docker compose -f {box.work}/docker-compose.yml --profile cli run --rm cli"
    for args, expected in (
        (["search", "two words"], "--oneshot search two words"),
        (["-o", "x"], "-o x"),
        (["--oneshot"], "--oneshot"),
        (["--output", "x"], "--oneshot --output x"),
    ):
        box.log.write_text("")
        result = subprocess.run([str(launcher), *args], env=box.env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert box.calls() == [f"{base} {expected}"]


def test_existing_qbittorrent_config_is_kept(box):
    conf = box.state / "qbittorrent/qBittorrent/qBittorrent.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[Preferences]\nmine=1\n")
    assert box.run("--skip-prompts").returncode == 0
    assert conf.read_text() == "[Preferences]\nmine=1\n"


def test_external_jackett_is_detected_before_files_are_written(box):
    result = box.run(stdin="1\n1\n2\n\n\n", extra={"FAKE_EXTERNAL": "my-jackett"})
    assert result.returncode == 0, result.stderr
    assert "Found existing external jackett container: my-jackett" in result.stdout

    assert "jackett" not in yaml.safe_load(box.compose_text())["services"]
    env = box.env_file()
    assert env["JACKETT_URL"] == "http://host.docker.internal:29117"
    assert env["JACKETT_API_KEY"] == "jkey-my-jackett"
    assert env["INDEXER_MANAGER"] == "jackett"
    assert not any("restart jackett" in call for call in box.calls())
    assert not (box.state / "jackett/Jackett/Indexers").exists()


def test_interactive_vpn_and_systemd(box):
    # install, indexer, run mode, systemd (bash); then Jackett key, user, VPN answers (init)
    answers = "1\n1\n1\ny\nmy-key\nbob\ny\nmullvad\nwireguard\nwg-secret\n"
    result = box.run(stdin=answers)
    assert result.returncode == 0, result.stderr

    assert box.compose_text() == compose.render({"cli", "jackett", "vpn"}, box.values())
    env = box.env_file()
    assert env["QB_WEBUI"] == "http://gluetun:18080"
    assert env["QB_USERNAME"] == "bob"
    assert env["JACKETT_API_KEY"] == "my-key"
    assert env["VPN_SERVICE_PROVIDER"] == "mullvad"
    assert env["VPN_TYPE"] == "wireguard"
    assert env["WIREGUARD_PRIVATE_KEY"] == "wg-secret"
    assert env["PROWLARR_API_KEY"] == "placeholder"
    assert "left running 24/7" in result.stdout
    assert not (box.work / "helm-torrent.sh").exists()

    calls = box.calls()
    assert not any(call.endswith(" stop") for call in calls)
    assert "sudo tee /etc/systemd/system/helm-app.service" in calls
    assert "sudo systemctl enable helm-app.service" in calls


def test_empty_vpn_key_disables_the_vpn(box):
    result = box.run(stdin="1\n1\n2\n\n\ny\nmullvad\nwireguard\n\n")
    assert result.returncode == 0, result.stderr
    assert "WireGuard key cannot be empty" in result.stderr
    assert "gluetun" not in yaml.safe_load(box.compose_text())["services"]


def test_crashed_container_stops_setup(box):
    result = box.run("--skip-prompts", extra={"FAKE_STATE": "exited"})
    assert result.returncode == 1
    assert "crashed immediately" in result.stderr and "docker logs helm-jackett" in result.stderr


def test_compose_failure_stops_setup(box):
    result = box.run("--skip-prompts", extra={"FAKE_COMPOSE_STATUS": "3"})
    assert result.returncode == 1
    assert "compose could not start the services" in result.stderr


def test_podman_uses_its_socket_and_the_real_image(box):
    result = box.run(stdin="2\n2\n2\n\n\n\n")
    assert result.returncode == 0, result.stderr

    sock = "/run/fake/podman.sock"
    compose_file = box.work / "docker-compose.yml"
    assert box.compose_text() == compose.render({"cli", "prowlarr", "novpn"}, box.values(sock))
    env = box.env_file()
    assert env["PROWLARR_API_KEY"] == "pkey-id-prowlarr" and env["INDEXER_MANAGER"] == "prowlarr"

    calls = box.calls()
    assert "systemctl --user enable --now podman.socket" in calls
    assert f"podman compose -f {compose_file} up -d --remove-orphans qbittorrent flaresolverr prowlarr" in calls
    assert "podman stop helm-prowlarr helm-jackett helm-qbittorrent helm-flaresolverr helm-gluetun" in calls
    assert "mini-helm" not in " ".join(calls)

    launcher = box.work / "helm-torrent.sh"
    box.log.write_text("")
    result = subprocess.run([str(launcher), "search", "x"], env=box.env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    (call,) = box.calls()
    assert call.startswith("podman run -it --rm --entrypoint= --security-opt label=disable --network helm_default")
    assert f"-v {box.config}:/config/helm -e HELM_CONFIG_DIR=/config/helm" in call
    assert f"{sock}:/var/run/docker.sock" in call
    assert call.endswith("ghcr.io/piratebird/helm:latest python -m helm.cli --oneshot search x")


def test_project_name_comes_from_the_environment(box):
    result = box.run("--skip-prompts", extra={"COMPOSE_PROJECT_NAME": "media"})
    assert result.returncode == 0, result.stderr
    assert box.env_file()["COMPOSE_PROJECT_NAME"] == "media"
    assert box.env_file()["JACKETT_API_KEY"] == "jkey-id-jackett"
    assert "export COMPOSE_PROJECT_NAME=media" in (box.work / "helm-torrent.sh").read_text()


def test_checkout_mounts_local_source(box):
    (box.work / "src" / "helm").mkdir(parents=True)
    assert box.run("--skip-prompts").returncode == 0
    assert box.compose_text() == compose.render({"cli", "jackett", "prowlarr", "source", "novpn"}, box.values())


def test_script_works_away_from_a_checkout(box):
    script = box.tmp / "fd" / "setup.sh"
    script.parent.mkdir()
    shutil.copy(SETUP, script)
    result = box.run("--skip-prompts", script=script)
    assert result.returncode == 0, result.stderr
    assert box.compose_text() == compose.render({"cli", "jackett", "prowlarr", "novpn"}, box.values())


def test_missing_docker_prints_install_help(box, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    for tool in ("uname", "dirname", "awk", "sed", "grep", "head", "mkdir", "cat"):
        found = shutil.which(tool)
        if found:
            os.symlink(found, empty / tool)
    result = box.run("--skip-prompts", extra={"PATH": str(empty)})
    assert result.returncode == 1
    assert "Docker is not installed" in result.stderr
    assert "Unix Shenanigans Reminder" in result.stdout


# ---------------------------------------------------------------- container handoff


def init_call(calls):
    (call,) = [call for call in calls if call.startswith(("docker run", "podman run"))]
    return call


def test_init_runs_in_the_image_as_the_user(box):
    result = box.run("--skip-prompts", extra={"HELM_SETUP_RUNNER": None})
    assert result.returncode == 0, result.stderr
    calls = box.calls()
    assert "docker pull ghcr.io/piratebird/helm:latest" in calls
    call = init_call(calls)
    assert call.startswith(
        "docker run --rm -i --security-opt label=disable -v /var/run/docker.sock:/var/run/docker.sock"
    )
    for path in (box.work, box.state, box.config, box.dl):
        assert f"-v {path}:{path}" in call
    assert f"--user {os.getuid()}:{os.getgid()} --group-add " in call
    assert f"-w {box.work} -e HOME=/tmp -e HELM_DOCKER_CMD=docker -e COMPOSE_PROJECT_NAME=helm" in call
    assert call.endswith(
        f"ghcr.io/piratebird/helm:latest init --config-dir {box.config} --state-dir {box.state} "
        f"--dl-dir {box.dl} --engine docker --indexers both --workdir {box.work} "
        "--socket /var/run/docker.sock --project helm --run-mode oneshot --skip-prompts"
    )


def test_rootless_engines_keep_root_in_the_container(box):
    result = box.run("--skip-prompts", extra={"HELM_SETUP_RUNNER": None, "FAKE_SECURITY": "[name=rootless]"})
    assert result.returncode == 0, result.stderr
    assert "--user" not in init_call(box.calls())

    box.log.write_text("")
    result = box.run(stdin="2\n1\n2\n", extra={"HELM_SETUP_RUNNER": None})
    assert result.returncode == 0, result.stderr
    call = init_call(box.calls())
    assert call.startswith("podman run --rm -i --security-opt label=disable -v /run/fake/podman.sock:")
    assert "--user" not in call and "--engine podman" in call


def test_image_can_be_overridden(box):
    result = box.run("--skip-prompts", extra={"HELM_SETUP_RUNNER": None, "HELM_IMAGE": "helm:dev"})
    assert result.returncode == 0, result.stderr
    assert "docker pull helm:dev" in box.calls()
    assert " helm:dev init " in init_call(box.calls())


# ---------------------------------------------------------------- helm-torrent init directly


def run_init(box, *args, stdin=""):
    argv = [sys.executable, "-m", "helm.cli", "init", "--config-dir", str(box.config), "--state-dir", str(box.state)]
    argv += ["--dl-dir", str(box.dl), *args]
    env = {**box.env, "HELM_DOCKER_CMD": "docker"}
    return subprocess.run(argv, cwd=box.work, env=env, input=stdin, capture_output=True, text=True, timeout=120)


def test_init_asks_for_missing_choices_and_defaults_to_the_state_dir(box):
    result = run_init(box, stdin="1\n2\n1\n\n\nn\n")
    assert result.returncode == 0, result.stderr
    expected = compose.render({"cli", "prowlarr", "novpn"}, box.values())
    assert (box.state / "docker-compose.yml").read_text() == expected
    assert box.env_file()["INDEXER_MANAGER"] == "prowlarr"
    assert box.env_file()["HOST_PWD"] == str(box.state)
    assert "left running 24/7" in result.stdout


def test_init_runs_on_a_broken_config_file(box):
    box.config.mkdir()
    (box.config / "config.toml").write_text("this is [not toml\n")
    result = run_init(box, "--engine", "docker", "--indexers", "jackett", "--run-mode", "oneshot", "--skip-prompts")
    assert result.returncode == 0, result.stderr
    assert "search.indexer_manager" in result.stdout


def test_init_rejects_bad_choices(box):
    result = run_init(box, "--engine", "lxc")
    assert result.returncode == 2 and "invalid choice" in result.stderr


def test_init_without_docker_cli_uses_podman(monkeypatch):
    monkeypatch.delenv("HELM_DOCKER_CMD", raising=False)
    monkeypatch.setattr(stack_setup.shutil, "which", lambda name: None)
    assert stack_setup.engine_command("podman") == "podman"
    assert stack_setup.engine_command("docker") == "docker"
    monkeypatch.setenv("HELM_DOCKER_CMD", "nerdctl")
    assert stack_setup.engine_command("podman") == "nerdctl"


def make_plan(box, indexers="jackett"):
    return stack_setup.Plan(
        "docker", indexers, "oneshot", str(box.state), str(box.config), str(box.dl), str(box.work), "/s"
    )


def test_openvpn_answers_reach_the_env_file(box, monkeypatch):
    answers = iter(["", "", "y", "acme", "openvpn", "user1", "pw1"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    plan = make_plan(box)
    stack_setup.choose(plan)
    assert plan.vpn.provider == "acme" and plan.vpn.kind == "openvpn"
    assert plan.vpn.extra == ["OPENVPN_USER=user1", "OPENVPN_PASSWORD=pw1"]

    box.state.mkdir()
    stack_setup.write_env(plan)
    text = (box.state / ".env.docker").read_text()
    assert "VPN_TYPE=openvpn\nOPENVPN_USER=user1\nOPENVPN_PASSWORD=pw1\n" in text


def test_custom_openvpn_and_protonvpn_warning(box, monkeypatch, capsys):
    answers = iter(["", "", "y", "custom", "openvpn"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    plan = make_plan(box)
    stack_setup.choose(plan)
    assert plan.vpn.extra == [] and "custom.conf" in capsys.readouterr().out

    answers = iter(["", "", "y", "protonvpn", "openvpn", "", ""])
    stack_setup.choose(plan)
    captured = capsys.readouterr()
    assert "ProtonVPN blocks P2P" in captured.err and "OpenVPN user cannot be empty" in captured.err
    assert plan.vpn is None


def test_set_env_value_keeps_other_lines_and_mode(box):
    box.state.mkdir()
    plan = make_plan(box, "both")
    stack_setup.write_private(plan.env_file, "# head\nA=1\nAB=2\n")
    stack_setup.set_env_value(plan, "A", "x=y")
    assert (box.state / ".env.docker").read_text() == "# head\nA=x=y\nAB=2\n"
    assert stat.S_IMODE((box.state / ".env.docker").stat().st_mode) == 0o600


def test_wait_times_out_with_a_warning(box, capsys):
    class Quiet(stack_setup.Engine):
        def output(self, args):
            return ""

    assert stack_setup.wait_for_services(make_plan(box), Quiet(poll=0), timeout=0) is False
    assert "Timeout reached" in capsys.readouterr().err


def test_missing_api_keys_fall_back_to_placeholders(box):
    class Empty(stack_setup.Engine):
        def output(self, args):
            return ""

    box.state.mkdir()
    plan = make_plan(box, "both")
    stack_setup.write_env(plan)
    os.environ["HELM_SETUP_KEY_TRIES"] = "1"
    os.environ["HELM_SETUP_KEY_POLL"] = "0"
    try:
        stack_setup.configure_api_keys(plan, Empty(command="true", poll=0))
    finally:
        del os.environ["HELM_SETUP_KEY_TRIES"], os.environ["HELM_SETUP_KEY_POLL"]
    env = box.env_file()
    assert env["JACKETT_API_KEY"] == "your_jackett_api_key_here"
    assert env["PROWLARR_API_KEY"] == "your_prowlarr_api_key_here"


# ---------------------------------------------------------------- whole flow in process


@pytest.mark.parametrize(
    "engine, indexers, run_mode, vpn, external",
    [
        ("docker", "both", "oneshot", False, ""),
        ("podman", "prowlarr", "oneshot", False, ""),
        ("docker", "jackett", "permanent", True, ""),
        ("docker", "both", "permanent", False, "other-prowlarr"),
    ],
)
def test_run_setup_in_process(box, monkeypatch, capsys, engine, indexers, run_mode, vpn, external):
    for name, value in box.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("FAKE_EXTERNAL", external)
    replies = {"VPN using Gluetun": "y" if vpn else "n", "VPN Provider": "nordvpn", "WireGuard Private Key": "k"}
    monkeypatch.setattr("builtins.input", lambda prompt="": next((v for k, v in replies.items() if k in prompt), ""))
    plan = stack_setup.Plan(
        engine, indexers, run_mode, str(box.state), str(box.config), str(box.dl), str(box.work), "/sock"
    )
    fake = str(box.tmp / "bin" / engine)
    stack_setup.run_setup(plan, stack_setup.Engine(fake, poll=0))
    out = capsys.readouterr().out

    blocks = {"cli", "vpn" if vpn else "novpn"} | {n for n in ("jackett", "prowlarr") if plan.local(n)}
    assert box.compose_text() == compose.render(blocks, box.values("/sock"))
    env = box.env_file()
    assert env["INDEXER_MANAGER"] == indexers
    if external:
        assert "prowlarr" not in yaml.safe_load(box.compose_text())["services"]
        assert env["PROWLARR_API_KEY"] == f"pkey-{external}"
    elif plan.wants("prowlarr"):
        assert env["PROWLARR_API_KEY"] == "pkey-id-prowlarr"
    if plan.wants("jackett"):
        assert env["JACKETT_API_KEY"] == "jkey-id-jackett"
    assert ("WIREGUARD_PRIVATE_KEY=k" in (box.state / ".env.docker").read_text()) == vpn
    assert (box.work / "helm-torrent.sh").exists() == (run_mode == "oneshot")
    assert "Setup is 100% complete" in out


def test_main_reports_setup_errors(box, monkeypatch, capsys):
    import argparse

    for name, value in box.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HELM_CONFIG_DIR", str(box.config))
    monkeypatch.setenv("HELM_STATE_DIR", str(box.state))
    monkeypatch.setenv("HELM_DL_DIR", str(box.dl))
    monkeypatch.setenv("FAKE_STATE", "exited")
    monkeypatch.setenv("HELM_DOCKER_CMD", str(box.tmp / "bin" / "docker"))
    parser = argparse.ArgumentParser()
    stack_setup.add_arguments(parser)
    args = parser.parse_args(["--engine", "docker", "--indexers", "jackett", "--run-mode", "oneshot", "--skip-prompts"])
    assert stack_setup.main(args) == 1
    assert "crashed immediately" in capsys.readouterr().err
    assert (box.state / "docker-compose.yml").exists()
