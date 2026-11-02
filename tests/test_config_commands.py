import http.server
import os
import shlex
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from helm.core.config_commands import DESCRIPTIONS, redact_url, render_template
from helm.core.settings import FIELDS, flatten, load_settings, parse_jsonc, read_file

SRC = str(Path(__file__).resolve().parents[1] / "src")
SCRUBBED = {field.env for field in FIELDS if field.env} | {
    "VISUAL",
    "EDITOR",
    "QB_PASSWORD",
    "JACKETT_API_KEY",
    "PROWLARR_API_KEY",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Private config, state and download directories, with no inherited settings."""
    paths = {
        "HELM_CONFIG_DIR": tmp_path / "config",
        "HELM_STATE_DIR": tmp_path / "state",
        "HELM_DL_DIR": tmp_path / "downloads",
    }
    paths["HELM_CONFIG_DIR"].mkdir()
    for key, value in paths.items():
        monkeypatch.setenv(key, str(value))
    for key in SCRUBBED:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def helm(*arguments, **extra_env):
    environment = {key: value for key, value in os.environ.items() if key not in SCRUBBED}
    environment["PYTHONPATH"] = SRC
    environment.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "helm.cli", "config", *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def config_file(home, name="config.toml"):
    return home / "config" / name


def lines_with(output, level):
    return [line for line in output.splitlines() if line.startswith(f"[{level}]")]


@pytest.fixture
def http_server():
    """A real local HTTP server that answers every request with 401."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


# Template


@pytest.mark.parametrize("file_format", ["toml", "jsonc"])
def test_template_parses_and_yields_only_defaults(home, file_format):
    name = "config." + file_format
    path = config_file(home, name)
    path.write_text(render_template(file_format))
    assert flatten(read_file(str(path))) == {}
    settings = load_settings()
    assert settings.path == str(path)
    for field in FIELDS:
        assert settings.get(field.path) == field.default
        assert settings.origin(field.path) == "default"


@pytest.mark.parametrize("file_format", ["toml", "jsonc"])
def test_template_documents_every_field(file_format):
    text = render_template(file_format)
    for field in FIELDS:
        assert DESCRIPTIONS[field.path] in text
        assert field.path.split(".", 1)[1] in text


def test_uncommented_jsonc_template_line_takes_effect():
    text = render_template("jsonc").replace('// "min_seeds": 3,', '"min_seeds": 9,')
    assert parse_jsonc(text)["search"]["min_seeds"] == 9


# init


@pytest.mark.parametrize("file_format", ["toml", "jsonc"])
def test_init_creates_private_template(home, file_format):
    result = helm("init", "--format", file_format)
    assert result.returncode == 0, result.stderr
    path = config_file(home, "config." + file_format)
    assert str(path) in result.stdout
    assert path.read_text() == render_template(file_format)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_init_refuses_to_overwrite_any_config_file(home):
    path = config_file(home, "config.jsonc")
    path.write_text('{"search": {"min_seeds": 5}}')
    result = helm("init")
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert path.read_text() == '{"search": {"min_seeds": 5}}'
    assert not config_file(home).exists()


def test_init_migrates_legacy_config_first(home):
    (home / "config" / "config.json").write_text('{"min_seeds": 8}')
    result = helm("init")
    assert result.returncode == 1
    assert "min_seeds = 8" in config_file(home).read_text()


def test_config_requires_an_action(home):
    result = helm()
    assert result.returncode == 2
    assert "<action>" in result.stderr


# edit


def fake_editor(home, body, exit_code=0):
    script = home / "fake editor.py"
    script.write_text(
        "import sys\n"
        "flag, path = sys.argv[1], sys.argv[2]\n"
        "assert flag == '--wait'\n"
        f"open(path, 'w').write({body!r})\n"
        f"sys.exit({exit_code})\n"
    )
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} --wait"


def test_edit_creates_file_runs_editor_without_shell_and_validates(home):
    editor = fake_editor(home, "[search]\nmin_seeds = 4\n")
    result = helm("edit", EDITOR=editor)
    assert result.returncode == 0, result.stderr
    assert config_file(home).read_text() == "[search]\nmin_seeds = 4\n"
    assert lines_with(result.stdout, "ok")
    assert not lines_with(result.stdout, "error")


def test_edit_prefers_visual_over_editor(home):
    visual = fake_editor(home, "[search]\nmin_seeds = 6\n")
    result = helm("edit", VISUAL=visual, EDITOR="/nonexistent-editor")
    assert result.returncode == 0, result.stderr
    assert "min_seeds = 6" in config_file(home).read_text()


def test_edit_reports_invalid_values(home):
    editor = fake_editor(home, "[search]\nlite_workers = 99\nmystery = 1\n")
    result = helm("edit", EDITOR=editor)
    assert result.returncode == 1
    assert any("search.lite_workers" in line for line in lines_with(result.stdout, "error"))
    assert any("search.mystery" in line for line in lines_with(result.stdout, "warn"))


def test_edit_opens_a_broken_file(home):
    config_file(home).write_text("[search\n")
    editor = fake_editor(home, "[search]\n")
    result = helm("edit", EDITOR=editor)
    assert result.returncode == 0, result.stderr
    assert config_file(home).read_text() == "[search]\n"


def test_edit_passes_on_editor_failure(home):
    editor = fake_editor(home, "", exit_code=3)
    result = helm("edit", EDITOR=editor)
    assert result.returncode == 3
    assert "status 3" in result.stderr


def test_edit_without_editor_explains(home):
    result = helm("edit")
    assert result.returncode == 1
    assert "$EDITOR" in result.stderr
    assert not config_file(home).exists()


def test_edit_reports_missing_editor_program(home):
    result = helm("edit", EDITOR="/nonexistent/editor")
    assert result.returncode == 1
    assert "Cannot start" in result.stderr


# doctor


def test_doctor_healthy_setup_with_reachable_services(home, http_server):
    config_file(home).write_text(
        f'[search]\nindexer_manager = "both"\n[services]\n'
        f'jackett = "{http_server}"\nprowlarr = "{http_server}"\nqbittorrent = "{http_server}"\n'
    )
    secrets = home / "config" / "secrets.env"
    secrets.write_text("QB_PASSWORD=hunter2-very-secret\n")
    secrets.chmod(0o600)
    result = helm("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines_with(result.stdout, "warn") == []
    assert len([line for line in result.stdout.splitlines() if "answers at" in line]) == 3
    assert "hunter2" not in result.stdout + result.stderr


def test_doctor_warns_but_passes_for_unreachable_service(home):
    config_file(home).write_text(f'[services]\nqbittorrent = "{closed_port_url()}"\n')
    result = helm("doctor")
    assert result.returncode == 0
    assert any("qbittorrent is not reachable" in line for line in lines_with(result.stdout, "warn"))


def test_doctor_skips_services_in_lite_only_mode(home):
    config_file(home).write_text(f'[search]\nlite_only = true\n[services]\njackett = "{closed_port_url()}"\n')
    result = helm("doctor")
    assert result.returncode == 0
    assert "skipped" in result.stdout
    assert "not reachable" not in result.stdout


def test_doctor_reports_syntax_error_without_traceback(home):
    config_file(home).write_text("[search\n")
    result = helm("doctor")
    assert result.returncode == 1
    assert any("Invalid syntax" in line for line in lines_with(result.stdout, "error"))
    assert "Traceback" not in result.stderr
    assert "Download directory" in result.stdout


def test_doctor_reports_bad_values_and_environment(home):
    config_file(home).write_text("[search]\nmin_seeds = -1\n")
    result = helm("doctor", HELM_LITE_WORKERS="50")
    assert result.returncode == 1
    errors = "\n".join(lines_with(result.stdout, "error"))
    assert "search.min_seeds" in errors
    assert "HELM_LITE_WORKERS" in errors


def test_doctor_warns_about_unknown_shadowed_and_ignored_files(home):
    config_file(home).write_text("[search]\nmin_seeds = 2\nextra = true\n")
    config_file(home, "config.jsonc").write_text("{}")
    (home / "config" / "config.json").write_text('{"min_seeds": 9}')
    result = helm("doctor", HELM_LITE_ONLY="1")
    assert result.returncode == 0
    warnings = "\n".join(lines_with(result.stdout, "warn"))
    assert "search.extra" in warnings
    assert "config.jsonc is ignored" in warnings
    assert "config.json is ignored for settings; set search.min_seeds" in warnings


def test_doctor_warns_about_open_secrets_file(home):
    secrets = home / "config" / "secrets.env"
    secrets.write_text("QB_PASSWORD=do-not-print-me\n")
    secrets.chmod(0o644)
    result = helm("doctor", HELM_LITE_ONLY="1")
    assert result.returncode == 0
    assert any("chmod 600" in line for line in lines_with(result.stdout, "warn"))
    assert "do-not-print-me" not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions without root")
def test_doctor_fails_for_unwritable_download_directory(home):
    locked = home / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = helm("doctor", HELM_DL_DIR=str(locked / "downloads"), HELM_LITE_ONLY="1")
    finally:
        locked.chmod(0o700)
    assert result.returncode == 1
    assert any("Download directory" in line for line in lines_with(result.stdout, "error"))


def test_doctor_redacts_credentials_in_urls(home):
    config_file(home).write_text(f'[services]\nqbittorrent = "http://user:pa55word@{closed_port_url()[7:]}"\n')
    result = helm("doctor")
    assert "pa55word" not in result.stdout + result.stderr
    assert "***@127.0.0.1" in result.stdout


# show


def test_show_lists_values_and_origins(home):
    config_file(home).write_text("[search]\nmin_seeds = 4\n")
    state = home / "state"
    state.mkdir()
    (state / "settings.json").write_text('{"search.default_type": "music"}')
    result = helm("show", "--origin", HELM_LITE_WORKERS="2")
    assert result.returncode == 0, result.stderr
    lines = {line.split("=")[0].strip(): line for line in result.stdout.splitlines() if "=" in line}
    assert len(lines) == len(FIELDS)
    assert lines["search.min_seeds"].endswith("= 4  (file)")
    assert lines["search.lite_workers"].endswith("= 2  (env)")
    assert lines["search.default_type"].endswith('= "music"  (state)')
    assert lines["search.lite_only"].endswith("= false  (default)")


def test_show_without_origin_and_without_file(home):
    result = helm("show")
    assert result.returncode == 0
    assert "none, using defaults" in result.stdout
    assert "(default)" not in result.stdout


def test_show_redacts_url_credentials(home):
    config_file(home).write_text('[services]\njackett = "http://me:s3cret@jackett.test"\n')
    result = helm("show")
    assert "s3cret" not in result.stdout
    assert '"http://***@jackett.test"' in result.stdout


def test_show_reports_broken_config_cleanly(home):
    config_file(home).write_text("[search\n")
    result = helm("show")
    assert result.returncode == 1
    assert "Invalid syntax" in result.stderr
    assert "Traceback" not in result.stderr


def test_redact_url_leaves_plain_urls_alone():
    assert redact_url("http://localhost:18080") == "http://localhost:18080"
    assert redact_url("https://a:b@host:1/x?y=1") == "https://***@host:1/x?y=1"


# In process, so coverage sees the same paths the subprocess tests run.


def run_in_process(arguments, capsys):
    from helm.cli import build_parser
    from helm.core import config_commands

    args = build_parser().parse_args(["config", *arguments])
    code = config_commands.run(args)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_in_process_init_show_and_doctor(home, capsys, http_server, monkeypatch):
    assert run_in_process(["init", "--format", "jsonc"], capsys)[0] == 0
    assert run_in_process(["init"], capsys)[0] == 1
    config_file(home, "config.jsonc").write_text(
        f'{{"services": {{"qbittorrent": "{http_server}", "jackett": "{http_server}"}}, "x": {{"y": 1}}}}'
    )
    code, out, _err = run_in_process(["show", "--origin"], capsys)
    assert code == 0 and "(file)" in out
    code, out, _err = run_in_process(["doctor"], capsys)
    assert code == 0
    assert "x.y" in out and "answers at" in out
    monkeypatch.setenv("HELM_LITE_ONLY", "1")
    config_file(home, "config.jsonc").write_text("{")
    code, out, _err = run_in_process(["doctor"], capsys)
    assert code == 1 and "Invalid syntax" in out


def test_in_process_edit(home, capsys, monkeypatch):
    monkeypatch.setenv("EDITOR", fake_editor(home, "[search]\nmin_seeds = 1\n"))
    assert run_in_process(["edit"], capsys)[0] == 0
    monkeypatch.setenv("EDITOR", fake_editor(home, "", exit_code=2))
    assert run_in_process(["edit"], capsys)[0] == 2
    monkeypatch.setenv("EDITOR", "/nonexistent/editor")
    assert run_in_process(["edit"], capsys)[0] == 1
    monkeypatch.delenv("EDITOR")
    assert run_in_process(["edit"], capsys)[0] == 1


def test_in_process_legacy_and_secrets_checks(home, capsys, monkeypatch):
    monkeypatch.setenv("HELM_LITE_ONLY", "1")
    legacy = home / "config" / "config.json"
    legacy.write_text("{broken")
    secrets = home / "config" / "secrets.env"
    secrets.write_text("QB_PASSWORD=x\n")
    secrets.chmod(0o640)
    code, out, _err = run_in_process(["doctor"], capsys)
    assert code == 0
    assert "cannot be parsed" in out and "chmod 600" in out
    legacy.write_text('{"min_seeds": 8}')
    code, out, _err = run_in_process(["doctor"], capsys)
    assert "next run migrates" in out
    config_file(home).write_text("[search]\nmin_seeds = 8\n")
    secrets.chmod(0o600)
    code, out, _err = run_in_process(["doctor"], capsys)
    assert "config.json" not in out and "is private" in out
