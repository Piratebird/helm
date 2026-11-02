"""Run the real setup.sh against fake docker, podman and systemctl binaries."""

import itertools
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from helm.core import compose, init_env

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.sh"
REAL_DOCKER = shutil.which("docker")
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

FAKE_ENGINE = r"""#!/usr/bin/env bash
name=$(basename "$0")
printf '%s %s\n' "$name" "$*" >> "$FAKE_LOG"
case "$1" in
    compose) exit 0 ;;
    info)
        if [ "$2" == "--format" ]; then echo "/run/fake/podman.sock"; fi
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


@pytest.fixture
def box(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "podman"):
        make_bin(bin_dir, name, FAKE_ENGINE)
    make_bin(bin_dir, "systemctl", '#!/bin/sh\necho "systemctl $*" >> "$FAKE_LOG"\n')
    make_bin(bin_dir, "sudo", '#!/bin/sh\necho "sudo $*" >> "$FAKE_LOG"\nexit 97\n')
    work = tmp_path / "work dir"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "FAKE_LOG": str(tmp_path / "calls.log"),
        "FAKE_EXTERNAL": "",
        "NO_COLOR": "1",
        "HELM_SETUP_POLL": "0",
        "HELM_SETUP_KEY_POLL": "0",
        "HELM_SETUP_KEY_TRIES": "2",
    }

    class Box:
        pass

    b = Box()
    b.tmp, b.work, b.home, b.env, b.log = tmp_path, work, home, env, Path(env["FAKE_LOG"])
    b.state = tmp_path / "state dir"
    b.config = tmp_path / "config"
    b.dl = tmp_path / "dl"

    def run(*args, script=SETUP, stdin="", extra=None, dirs=True):
        argv = [BASH, str(script)]
        if dirs:
            argv += ["--state-dir", str(b.state), "--config-dir", str(b.config), "--downloads-dir", str(b.dl)]
        argv += list(args)
        return subprocess.run(
            argv,
            cwd=b.work,
            env={**b.env, **(extra or {})},
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def calls():
        return b.log.read_text().splitlines() if b.log.exists() else []

    def env_file():
        lines = (b.state / ".env.docker").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))

    b.run, b.calls, b.env_file = run, calls, env_file
    return b


def values(box, socket="/var/run/docker.sock"):
    return {
        "HELM_STATE": str(box.state),
        "HELM_DL": str(box.dl),
        "HELM_CONFIG": str(box.config),
        "ENGINE_SOCKET": socket,
    }


def bash_render(blocks, vals, template=compose.TEMPLATE):
    return subprocess.run(
        [
            BASH,
            "-c",
            'source "$1"; render_compose "$2" "$3"',
            "render",
            str(SETUP),
            str(template),
            " ".join(sorted(blocks)),
        ],
        env={**os.environ, **vals},
        capture_output=True,
        text=True,
        check=False,
    )


def all_block_sets():
    for cli, jackett, prowlarr, source, vpn in itertools.product([0, 1], repeat=5):
        blocks = {"vpn" if vpn else "novpn"}
        for on, name in ((cli, "cli"), (jackett, "jackett"), (prowlarr, "prowlarr"), (source, "source")):
            if on:
                blocks.add(name)
        yield blocks


# ---------------------------------------------------------------- template


def test_bash_and_python_render_identically_for_every_block_set():
    vals = {
        "HELM_STATE": "/tmp/state & co/x\\y",
        "HELM_DL": "/dl with space",
        "HELM_CONFIG": "/cfg/@HELM_DL@",
        "ENGINE_SOCKET": "/run/user/1000/podman/podman.sock",
    }
    for blocks in all_block_sets():
        result = bash_render(blocks, vals)
        assert result.returncode == 0, result.stderr
        assert result.stdout == compose.render(blocks, vals), sorted(blocks)


def test_rendered_files_have_the_expected_services():
    vals = {"HELM_STATE": "/s", "HELM_DL": "/d", "HELM_CONFIG": "/c", "ENGINE_SOCKET": "/sock"}
    for blocks in all_block_sets():
        data = yaml.safe_load(compose.render(blocks, vals))
        services = set(data["services"])
        expected = {"flaresolverr", "qbittorrent"} | (blocks & {"jackett", "prowlarr", "cli"})
        if "vpn" in blocks:
            expected.add("gluetun")
        assert services == expected
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
        assert "#@" not in compose.render(blocks, vals)


@pytest.mark.parametrize(
    "template",
    ["a\n#@ end\n", "a\n#@ if cli\nb\n"],
)
def test_unbalanced_templates_fail_in_both_renderers(tmp_path, template):
    path = tmp_path / "bad.yml"
    path.write_text(template)
    vals = {name: "/x" for name in compose.PLACEHOLDERS}
    with pytest.raises(compose.TemplateError):
        compose.render({"novpn"}, vals, template=template)
    assert bash_render({"novpn"}, vals, template=path).returncode != 0


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
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert '"helm.core" = ["compose.template.yml"]' in pyproject
    assert compose.TEMPLATE.is_file()


def test_init_env_uses_the_template_without_the_cli_service(tmp_path):
    text = init_env.build_compose("/s", "/d", "/c", use_vpn=True)
    assert text == compose.render(
        {"jackett", "prowlarr", "vpn"}, {"HELM_STATE": "/s", "HELM_DL": "/d", "HELM_CONFIG": "/c", "ENGINE_SOCKET": ""}
    )
    assert "cli" not in yaml.safe_load(text)["services"]

    path = tmp_path / ".env.docker"
    path.write_text("old")
    path.chmod(0o644)
    init_env.write_private(str(path), "QB_PASSWORD=x\n")
    assert path.read_text() == "QB_PASSWORD=x\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ---------------------------------------------------------------- setup.sh runs


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
    assert compose_file.read_text() == compose.render({"cli", "jackett", "prowlarr", "novpn"}, values(box))

    env = box.env_file()
    assert env["JACKETT_API_KEY"] == "jkey-id-jackett"
    assert env["PROWLARR_API_KEY"] == "pkey-id-prowlarr"
    assert env["JACKETT_URL"] == "http://jackett:9117"
    assert env["QB_WEBUI"] == "http://qbittorrent:18080"
    assert env["INDEXER_MANAGER"] == "both"
    assert env["COMPOSE_PROJECT_NAME"] == "helm"
    assert env["HOST_PWD"] == str(box.work)
    assert stat.S_IMODE((box.state / ".env.docker").stat().st_mode) == 0o600

    calls = box.calls()
    file_arg = f"-f {compose_file}"
    assert f"docker compose {file_arg} --profile cli pull cli" in calls
    assert f"docker compose {file_arg} up -d --remove-orphans qbittorrent flaresolverr jackett prowlarr" in calls
    assert f"docker compose {file_arg} restart jackett" in calls
    assert f"docker compose {file_arg} stop" in calls
    assert not any(call.startswith("sudo") for call in calls)

    assert (box.state / "jackett/Jackett/Indexers/yts.json").is_file()
    assert "WebUI\\Username=admin" in (box.state / "qbittorrent/qBittorrent/qBittorrent.conf").read_text()
    assert not (box.config / "config.json").exists()

    for path in (box.config, box.state, box.dl):
        assert f"{path}/" in result.stdout
    assert "./helm-torrent.sh" in result.stdout


def test_docker_launcher_adds_oneshot_once(box):
    assert box.run("--skip-prompts").returncode == 0
    launcher = box.work / "helm-torrent.sh"
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

    data = yaml.safe_load((box.work / "docker-compose.yml").read_text())
    assert "jackett" not in data["services"]
    env = box.env_file()
    assert env["JACKETT_URL"] == "http://host.docker.internal:29117"
    assert env["JACKETT_API_KEY"] == "jkey-my-jackett"
    assert env["INDEXER_MANAGER"] == "jackett"
    assert not any("restart jackett" in call for call in box.calls())
    assert not (box.state / "jackett/Jackett/Indexers").exists()


def test_interactive_vpn_and_systemd_prompt(box):
    answers = "1\n1\n1\nmy-key\nbob\ny\nmullvad\nwireguard\nwg-secret\nn\n"
    result = box.run(stdin=answers)
    assert result.returncode == 0, result.stderr

    expected = compose.render({"cli", "jackett", "vpn"}, values(box))
    assert (box.work / "docker-compose.yml").read_text() == expected
    env = box.env_file()
    assert env["QB_WEBUI"] == "http://gluetun:18080"
    assert env["QB_USERNAME"] == "bob"
    assert env["JACKETT_API_KEY"] == "my-key"
    assert env["VPN_SERVICE_PROVIDER"] == "mullvad"
    assert env["VPN_TYPE"] == "wireguard"
    assert env["WIREGUARD_PRIVATE_KEY"] == "wg-secret"
    assert "PROWLARR_URL" in env and env["PROWLARR_API_KEY"] == "placeholder"
    assert "left running 24/7" in result.stdout
    assert not (box.work / "helm-torrent.sh").exists()
    assert not any(call.endswith(" stop") for call in box.calls())


def test_empty_vpn_key_disables_the_vpn(box):
    result = box.run(stdin="1\n1\n2\n\n\ny\nmullvad\nwireguard\n\n")
    assert result.returncode == 0, result.stderr
    assert "WireGuard key cannot be empty" in result.stderr
    assert "gluetun" not in yaml.safe_load((box.work / "docker-compose.yml").read_text())["services"]


def test_crashed_container_stops_setup(box):
    result = box.run("--skip-prompts", extra={"FAKE_STATE": "exited"})
    assert result.returncode == 1
    assert "crashed immediately" in result.stderr and "docker logs helm-jackett" in result.stderr


def test_podman_uses_the_real_image_and_socket(box):
    result = box.run(stdin="2\n2\n2\n\n\n\n")
    assert result.returncode == 0, result.stderr

    sock = "/run/fake/podman.sock"
    compose_file = box.work / "docker-compose.yml"
    assert compose_file.read_text() == compose.render({"cli", "prowlarr", "novpn"}, values(box, sock))
    env = box.env_file()
    assert env["PROWLARR_API_KEY"] == "pkey-id-prowlarr" and env["INDEXER_MANAGER"] == "prowlarr"

    calls = box.calls()
    assert "systemctl --user enable --now podman.socket" in calls
    assert f"podman compose -f {compose_file} --profile cli pull cli" in calls
    up = [call for call in calls if call.startswith("podman run") and " up -d " in call]
    assert len(up) == 1 and compose.TEMPLATE.name not in up[0]
    assert "ghcr.io/piratebird/helm:latest docker compose -f /app/docker-compose.yml up -d" in up[0]
    assert "mini-helm" not in " ".join(calls)
    assert any(call.startswith("podman stop helm-prowlarr helm-jackett") for call in calls)

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


def test_curl_one_liner_downloads_the_template(box):
    script = box.tmp / "fd" / "setup.sh"
    script.parent.mkdir()
    shutil.copy(SETUP, script)
    if shutil.which("curl") is None:
        pytest.skip("curl is not installed")
    result = box.run("--skip-prompts", script=script, extra={"HELM_RAW_BASE": f"file://{ROOT}"})
    assert result.returncode == 0, result.stderr
    assert "Downloading the compose template" in result.stdout
    expected = compose.render({"cli", "jackett", "prowlarr", "novpn"}, values(box))
    assert (box.work / "docker-compose.yml").read_text() == expected

    result = box.run("--skip-prompts", script=script, extra={"HELM_RAW_BASE": f"file://{box.tmp}/missing"})
    assert result.returncode == 1 and "Could not download" in result.stderr


def test_checkout_mounts_local_source(box):
    (box.work / "src" / "helm").mkdir(parents=True)
    assert box.run("--skip-prompts").returncode == 0
    expected = compose.render({"cli", "jackett", "prowlarr", "source", "novpn"}, values(box))
    assert (box.work / "docker-compose.yml").read_text() == expected


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
