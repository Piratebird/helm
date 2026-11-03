"""Exercise the standalone raw-GitHub script using disposable data and engines."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "uninstall.sh"


def run_script(tmp_path, *arguments, stdin=False, env=None):
    environment = os.environ.copy()
    environment.update(env or {})
    return subprocess.run(
        ["bash", "-s", "--", *arguments] if stdin else ["bash", str(SCRIPT), *arguments],
        input=SCRIPT.read_text() if stdin else None,
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )


def make_config(tmp_path):
    config = tmp_path / "shared config"
    config.mkdir()
    for name in ("config.json", "secrets.env", "repositories.yaml", "registry.json"):
        (config / name).write_text("keep private")
    (config / "plugins").mkdir()
    return config


@pytest.mark.parametrize("arguments", [[], ["--config", "--dry-run"], ["--config", "--yes", "--dry-run"]])
def test_preview_does_not_delete_config(tmp_path, arguments):
    config = make_config(tmp_path)
    result = run_script(tmp_path, *arguments, "--config-dir", str(config))
    assert result.returncode == (1 if not arguments else 0), result.stderr
    assert (config / "config.json").exists()
    assert (config / "secrets.env").exists()


def test_raw_stdin_script_preserves_kubernetes_and_plugins(tmp_path):
    config = make_config(tmp_path)
    result = run_script(tmp_path, "--config", "--config-dir", str(config), "--yes", stdin=True)
    assert result.returncode == 0, result.stderr
    assert not (config / "config.json").exists()
    assert not (config / "secrets.env").exists()
    assert (config / "repositories.yaml").exists()
    assert (config / "registry.json").exists()
    assert (config / "plugins").is_dir()


def test_state_cleanup_preserves_downloads_and_unrelated_files(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "jackett").mkdir()
    (state / "jackett" / "data").write_text("service state")
    (state / "helm.log.1").write_text("log")
    (state / ".env.docker").write_text("secrets")
    (state / "other.txt").write_text("unrelated")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "keep.iso").write_text("download")
    result = run_script(tmp_path, "--state", "--state-dir", str(state), "--downloads-dir", str(downloads), "--yes")
    assert result.returncode == 0, result.stderr
    assert not (state / "jackett").exists()
    assert not (state / "helm.log.1").exists()
    assert not (state / ".env.docker").exists()
    assert (state / "other.txt").exists()
    assert (downloads / "keep.iso").exists()


@pytest.mark.parametrize("root", ["/", "."])
def test_rejects_unsafe_root_before_removal(tmp_path, root):
    result = run_script(tmp_path, "--state", "--state-dir", root, "--yes")
    assert result.returncode == 1
    assert "unsafe removal path" in result.stderr


def test_rejects_download_overlap_before_removal(tmp_path):
    state = tmp_path / "state"
    downloads = state / "qbittorrent" / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "keep.iso").write_text("download")
    result = run_script(tmp_path, "--state", "--state-dir", str(state), "--downloads-dir", str(downloads), "--yes")
    assert result.returncode == 1
    assert (downloads / "keep.iso").exists()


def test_symlink_artifact_is_unlinked_without_deleting_target(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "untouched"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    (state / "jackett").symlink_to(target, target_is_directory=True)
    result = run_script(tmp_path, "--state", "--state-dir", str(state), "--yes")
    assert result.returncode == 0, result.stderr
    assert not (state / "jackett").is_symlink()
    assert (target / "keep.txt").exists()


def test_symlink_root_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    result = run_script(tmp_path, "--state", "--state-dir", str(link), "--yes")
    assert result.returncode == 1
    assert "must not be a symlink" in result.stderr


def fake_engine(tmp_path, engine):
    tools = tmp_path / "bin"
    tools.mkdir()
    executable = tools / engine
    executable.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$ENGINE_LOG"
case "$1 $2" in
  'ps -aq') echo container-123 ;;
  'network ls') echo network-123 ;;
  'volume ls') echo volume-123 ;;
esac
""")
    executable.chmod(0o755)
    log = tmp_path / "engine.log"
    return {"PATH": f"{tools}{os.pathsep}{os.environ['PATH']}", "ENGINE_LOG": str(log)}, log


@pytest.mark.parametrize("engine", ["docker", "podman"])
@pytest.mark.parametrize("apply", [False, True])
def test_removes_only_selected_project_resources(tmp_path, engine, apply):
    environment, log = fake_engine(tmp_path, engine)
    arguments = ["--containers", "--volumes", "--engine", engine, "--project", "helm-test"]
    if apply:
        arguments.append("--yes")
    result = run_script(tmp_path, *arguments, env=environment)
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert calls[:3] == [
        "ps -aq --filter label=com.docker.compose.project=helm-test",
        "network ls -q --filter label=com.docker.compose.project=helm-test",
        "volume ls -q --filter label=com.docker.compose.project=helm-test",
    ]
    assert calls[3:] == (
        ["container rm -f container-123", "network rm network-123", "volume rm volume-123"] if apply else []
    )


def test_volumes_are_never_selected_implicitly(tmp_path):
    environment, log = fake_engine(tmp_path, "docker")
    result = run_script(tmp_path, "--containers", "--engine", "docker", "--yes", env=environment)
    assert result.returncode == 0, result.stderr
    assert "volume" not in log.read_text()


def test_engine_failure_stops_before_deleting_state(tmp_path):
    environment, log = fake_engine(tmp_path, "docker")
    engine = tmp_path / "bin" / "docker"
    engine.write_text("#!/usr/bin/env bash\nexit 1\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / ".env.docker").write_text("preserve")
    result = run_script(
        tmp_path, "--containers", "--engine", "docker", "--state", "--state-dir", str(state), "--yes", env=environment
    )
    assert result.returncode != 0
    assert (state / ".env.docker").exists()


def test_package_removal_targets_only_torrent_distribution(tmp_path):
    fake_python = tmp_path / "python"
    log = tmp_path / "pip.log"
    fake_python.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$PIP_LOG"\n')
    fake_python.chmod(0o755)
    result = run_script(tmp_path, "--package", "--yes", env={"HELM_PYTHON": str(fake_python), "PIP_LOG": str(log)})
    assert result.returncode == 0, result.stderr
    assert log.read_text().strip() == "-m pip uninstall -y helm-torrent"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--volumes"],
        ["--config-dir"],
        ["--config", "--engine", "bad"],
        ["--config", "--project", "../bad"],
        ["--unknown"],
    ],
)
def test_invalid_arguments_do_not_remove_anything(tmp_path, arguments):
    result = run_script(tmp_path, *arguments)
    assert result.returncode == 1
    assert "Error:" in result.stderr


def test_help_runs_from_raw_stdin_without_python_or_checkout(tmp_path):
    result = run_script(tmp_path, "--help", stdin=True, env={"HELM_PYTHON": "/not/installed"})
    assert result.returncode == 0, result.stderr
    assert "Preview by default" in result.stdout


def test_command_metadata_and_help_agree_on_new_name():
    import configparser

    root = SCRIPT.parent
    project = (root / "pyproject.toml").read_text()
    scripts = project.split("[project.scripts]\n", 1)[1].split("\n[", 1)[0]
    parser = configparser.ConfigParser()
    parser.read_string("[scripts]\n" + scripts)
    assert dict(parser["scripts"]) == {"helm-torrent": '"helm.cli:main"'}
    # The CLI help is tied to the same exported name, even under python -m.
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, "-m", "helm.cli", "--help"], env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "usage: helm-torrent" in result.stdout


def test_local_handout_documents_command_and_uninstall():
    handout_path = SCRIPT.parent / "personal_notes" / "handout.md"
    if not handout_path.exists():
        pytest.skip("personal_notes is intentionally private and ignored")
    handout = handout_path.read_text()
    assert "`helm-torrent`" in handout
    assert "uninstall.sh" in handout
    assert "--dry-run" in handout


def test_local_agent_handoff_records_pending_work():
    path = SCRIPT.parent / "personal_notes" / "hadnout.md"
    if not path.exists():
        pytest.skip("personal_notes is intentionally private and ignored")
    contents = path.read_text()
    for marker in (
        "## Last known verification state",
        "3 failures, 31 passes",
        "## Recommended continuation order",
        "### Temporary access cleanup promise",
    ):
        assert marker in contents
