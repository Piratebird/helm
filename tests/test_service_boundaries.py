"""Setup, wizard and one-shot behaviour against a fake engine binary and real HTTP."""

import builtins
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

import helm.config.config_wizard as wizard
import helm.stack.oneshot as oneshot
from helm.config.config_manager import get_state_dir
from helm.config.secret_manager import get_secret
from helm.config.settings import load_settings, remember


def install_engine(tmp_path, monkeypatch, status):
    """A fake docker CLI that records its arguments and reports one container state."""
    log = tmp_path / "engine.log"
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {log}\n'
        'case "$1 $2" in\n'
        f"  inspect*) echo {status} ;;\n"
        '  compose*) case "$*" in *" ps -q "*) echo cid ;; esac ;;\n'
        "esac\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(oneshot, "DOCKER_CMD", str(binary))
    monkeypatch.setattr(oneshot.time, "sleep", lambda _: None)
    return log


class Service(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"ready")

    do_POST = do_GET


@pytest.fixture
def service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def test_teardown_stops_only_named_stack_services(tmp_path, monkeypatch):
    log = install_engine(tmp_path, monkeypatch, "running")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "helm-test")
    oneshot.teardown_oneshot()
    lines = sorted(log.read_text().splitlines())
    assert lines == sorted("stop helm-test-" + service for service in oneshot.CONTAINER_SERVICES)


def test_startup_brings_up_stack_before_inspecting_it(tmp_path, monkeypatch, service):
    log = install_engine(tmp_path, monkeypatch, "running")
    monkeypatch.setenv("JACKETT_URL", service)
    monkeypatch.setenv("FLARESOLVERR_URL", service)
    oneshot.spin_up_oneshot()
    lines = log.read_text().splitlines()
    assert lines[0].startswith("compose") and " up -d " in lines[0]
    for name in ("jackett", "prowlarr", "qbittorrent", "flaresolverr"):
        assert name in lines[0]
    assert sum(line.startswith("inspect") for line in lines) == 4


def test_startup_uses_generated_compose_file(tmp_path, monkeypatch, service):
    log = install_engine(tmp_path, monkeypatch, "running")
    monkeypatch.setenv("JACKETT_URL", service)
    monkeypatch.setenv("FLARESOLVERR_URL", service)
    state = Path(get_state_dir())
    state.mkdir(parents=True, exist_ok=True)
    compose = state / "docker-compose.yml"
    compose.write_text("services: {}\n")
    try:
        oneshot.spin_up_oneshot()
        assert f"-f {compose}" in log.read_text().splitlines()[0]
    finally:
        compose.unlink()


def test_crashed_container_stops_the_stack_and_exits(tmp_path, monkeypatch):
    log = install_engine(tmp_path, monkeypatch, "exited")
    with pytest.raises(SystemExit) as error:
        oneshot.spin_up_oneshot()
    assert error.value.code == 1
    stopped = [line for line in log.read_text().splitlines() if line.startswith("stop ")]
    assert len(stopped) == len(oneshot.CONTAINER_SERVICES)


def answers(monkeypatch, values):
    remaining = iter(values)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(remaining))


def test_wizard_lite_choice_is_saved(monkeypatch):
    answers(monkeypatch, ["n"])
    wizard.run_wizard()
    assert load_settings().get("search.lite_only") is True
    remember({"search.lite_only": False})


def test_wizard_without_a_terminal_exits_with_guidance(monkeypatch, capsys):
    def closed(prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", closed)
    with pytest.raises(SystemExit) as error:
        wizard.run_wizard()
    assert error.value.code == 1
    assert "-it" in capsys.readouterr().out


def test_ensure_config_runs_wizard_only_when_settings_are_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(wizard, "run_wizard", lambda: calls.append(1))
    for key in ("JACKETT_URL", "QB_WEBUI", "QB_USERNAME", "JACKETT_API_KEY", "QB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    remember({"search.lite_only": False})
    wizard.ensure_config()  # Not a terminal: warn instead of blocking on a prompt.
    assert calls == []
    monkeypatch.setattr(wizard.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    wizard.ensure_config()
    assert calls == [1]
    remember({"search.lite_only": True})
    wizard.ensure_config()
    assert calls == [1]
    remember({"search.lite_only": False})
    assert get_secret("QB_PASSWORD") is None
