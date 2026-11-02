import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from helm.core.settings import BY_PATH, COLOR_NAMES, FIELDS, HEX_COLOR, THEMES, Settings, check
from helm.ui.ansi import color_enabled, render_banner, sgr, visible_width
from helm.ui.banner import (
    LOGOS,
    TIMED_OUT,
    UNAVAILABLE,
    Banner,
    Entry,
    build_banner,
    clean_output,
    parse_art,
    resolve_logo,
    run_with_deadline,
)
from helm.ui.theme import PRESETS, load_theme

SRC = str(Path(__file__).resolve().parents[1] / "src")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SCRUBBED = {field.env for field in FIELDS if field.env} | {
    "NO_COLOR",
    "FORCE_COLOR",
    "JACKETT_API_KEY",
    "PROWLARR_API_KEY",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    for key, name in (("HELM_CONFIG_DIR", "config"), ("HELM_STATE_DIR", "state"), ("HELM_DL_DIR", "downloads")):
        monkeypatch.setenv(key, str(tmp_path / name))
    (tmp_path / "config").mkdir()
    for key in SCRUBBED:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def make_settings(**values):
    """Settings with the given values, written as theme__preset for theme.preset."""
    resolved = {}
    for key, value in values.items():
        path = key.replace("__", ".")
        resolved[path] = check(BY_PATH[path], value) if value is not None else None
    return Settings(resolved)


def python_command(code):
    return [sys.executable, "-c", code]


@pytest.fixture
def server():
    """A real local HTTP server. Set server.routes[path] = (status, content type, body)."""
    routes = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, kind, body = routes.get(self.path.split("?")[0], (404, "text/plain", b""))
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            routes.setdefault("_seen", []).append(self.path)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.routes = routes
    yield httpd
    httpd.shutdown()
    httpd.server_close()


# Themes


def test_presets_match_settings_and_define_every_colour():
    assert tuple(PRESETS) == THEMES
    for name, preset in PRESETS.items():
        assert tuple(preset) == COLOR_NAMES
        for value in preset.values():
            assert HEX_COLOR.match(value) or (name == "helm" and re.match(r"^ansi:\d+$", value))


# The exact codes and wordmark the UI printed before themes existed.
OLD_CODES = {
    "logo": "\033[38;2;74;105;189m",
    "accent": "\033[38;2;179;57;57m",
    "text": "\033[38;2;234;218;201m",
    "line": "\033[38;2;205;97;51m",
    "error": "\033[38;2;255;82;82m",
    "success": "\033[32m",
    "warning": "\033[33m",
    "info": "\033[36m",
}
OLD_WORDMARK = """
▗▖ ▗▖▗▄▄▄▖▗▖   ▗▖  ▗▖
▐▌ ▐▌▐▌   ▐▌   ▐▛▚▞▜▌
▐▛▀▜▌▐▛▀▀▘▐▌   ▐▌  ▐▌
▐▌ ▐▌▐▙▄▄▖▐▙▄▄▖▐▌  ▐▌
"""


def test_helm_preset_keeps_the_current_look():
    from helm.ui.banner import WORDMARK

    theme = load_theme(make_settings())
    assert theme.name == "helm"
    for name, code in OLD_CODES.items():
        assert sgr(theme.get(name)) == code, name
    assert WORDMARK == OLD_WORDMARK
    assert LOGOS["helm"] == OLD_WORDMARK.strip("\n").split("\n")


def test_single_colour_override_keeps_the_rest_of_the_preset():
    theme = load_theme(make_settings(theme__preset="nord", theme__logo="#123456"))
    assert theme.get("logo") == "#123456"
    assert theme.get("accent") == PRESETS["nord"]["accent"]


# Logo


def test_parse_art_placeholders_carry_across_lines():
    lines = parse_art("{accent}A{line}B\nC{reset}D {unknown} {logo}E\n\n")
    assert lines == [
        [("accent", "A"), ("line", "B")],
        [("line", "C"), ("text", "D {unknown} "), ("logo", "E")],
    ]


def test_parse_art_limits_lines():
    assert len(parse_art("x\n" * 500)) == 60


def test_clean_output_keeps_colour_and_drops_other_control_codes():
    raw = "\x1b[31mred\x1b[0m\x1b[2J\x1b]0;title\x07\x07ok\r\nnext\tline\x1b[?25l\n\n"
    assert clean_output(raw) == ["\x1b[31mred\x1b[0mok", "next    line"]


def test_resolve_logo_sources(home):
    timeout = 2
    assert resolve_logo(make_settings(logo__source="none"), timeout) == []
    assert resolve_logo(make_settings(logo__source="builtin:missing"), timeout) == resolve_logo(
        make_settings(), timeout
    )
    assert resolve_logo(make_settings(logo__source="file", logo__art="{accent}hi"), timeout) == [[("accent", "hi")]]
    art = home / "art.txt"
    art.write_text("{line}rope\n")
    from_file = make_settings(logo__source="file", logo__art_file=str(art))
    assert resolve_logo(from_file, timeout) == [[("line", "rope")]]
    blank = make_settings(logo__source="file", logo__art="  ")
    assert resolve_logo(blank, timeout) == resolve_logo(make_settings(), timeout)
    printing = "cmd:" + " ".join(map(repr, python_command("print('\\x1b[32mgreen\\x1b[0m')")))
    assert resolve_logo(make_settings(logo__source=printing), timeout) == [[(None, "\x1b[32mgreen\x1b[0m")]]
    silent = "cmd:" + " ".join(map(repr, python_command("pass")))
    assert resolve_logo(make_settings(logo__source=silent), timeout) == resolve_logo(make_settings(), timeout)


@pytest.mark.parametrize(
    "source",
    [
        "cmd:/nonexistent/figlet",
        "cmd:" + " ".join(map(repr, python_command("import sys; sys.exit(4)"))),
        "cmd:" + " ".join(map(repr, python_command("import time; time.sleep(5)"))),
    ],
)
def test_failing_or_slow_logo_command_falls_back_quickly(home, source):
    settings = make_settings(logo__source=source, banner__timeout_ms=300, banner__modules=["version"])
    started = time.monotonic()
    banner = build_banner(settings)
    assert time.monotonic() - started < 1.5
    assert banner.logo == [[("logo", line)] for line in LOGOS["helm"]]


def test_missing_art_file_falls_back(home):
    settings = make_settings(logo__source="file", logo__art_file=str(home / "missing.txt"), banner__modules=[])
    assert build_banner(settings).logo == [[("logo", line)] for line in LOGOS["helm"]]


# Modules


def test_run_with_deadline_reports_errors_and_timeouts():
    def fail():
        raise RuntimeError("boom")

    started = time.monotonic()
    results = run_with_deadline([lambda: 1, fail, lambda: time.sleep(5)], 0.2)
    assert time.monotonic() - started < 1
    assert results == {0: ("ok", 1), 1: ("error", None), 2: ("timeout", None)}


def test_custom_modules_and_separator(home):
    modules = [
        {"type": "command", "label": "Hello", "command": python_command("print('\\n\\x1b[1mhi there\\x1b[0m')")},
        "separator",
        {"type": "command", "label": "Broken", "command": python_command("import sys; sys.exit(1)")},
        {"type": "command", "label": "Slow", "command": python_command("import time; time.sleep(5)")},
        {"type": "command", "label": "Quiet", "command": python_command("pass")},
    ]
    started = time.monotonic()
    banner = build_banner(make_settings(banner__modules=modules, banner__timeout_ms=1500))
    assert time.monotonic() - started < 3
    assert [(entry.label, entry.value, entry.status) for entry in banner.entries] == [
        ("Hello", "hi there", "ok"),
        ("", "", "separator"),
        ("Broken", UNAVAILABLE, "error"),
        ("Slow", TIMED_OUT, "timeout"),
        ("Quiet", "none", "ok"),
    ]


def entry_value(banner, label):
    return next(entry.value for entry in banner.entries if entry.label == label)


def test_builtin_modules_without_services(home):
    settings = make_settings(
        search__lite_only=True,
        banner__modules=["version", "mode", "indexers", "jobs", "downloads", "services"],
        banner__timeout_ms=3000,
    )
    banner = build_banner(settings)
    assert all(entry.status == "ok" for entry in banner.entries), banner.entries
    assert entry_value(banner, "Version") not in ("", "unknown")
    assert entry_value(banner, "Mode") == "lite"
    assert re.match(r"^\d+ Lite sources$", entry_value(banner, "Indexers"))
    assert int(entry_value(banner, "Indexers").split()[0]) > 0
    assert entry_value(banner, "Jobs") == "none yet"
    assert not (home / "state" / "jobs.sqlite3").exists()
    assert "GiB free" in entry_value(banner, "Downloads")
    assert entry_value(banner, "Services") == "not used in Lite mode"


def test_mode_follows_context_and_manager(home):
    settings = make_settings(search__indexer_manager="both", banner__modules=["mode"])
    assert entry_value(build_banner(settings), "Mode") == "both"
    assert entry_value(build_banner(settings, {"mode": "lite"}), "Mode") == "lite"


def test_jobs_module_counts_active_jobs(home):
    from helm.core.jobs import JobStore

    store = JobStore()
    magnets = [f"magnet:?xt=urn:btih:{str(index) * 40}" for index in range(4)]
    jobs = [store.enqueue(magnet) for magnet in magnets]
    store.update(jobs[0]["id"], "running")
    store.update(jobs[1]["id"], "succeeded")
    banner = build_banner(make_settings(banner__modules=["jobs"]))
    assert entry_value(banner, "Jobs") == "1 running, 2 queued"
    for job in jobs:
        store.update(job["id"], "succeeded")
    assert entry_value(build_banner(make_settings(banner__modules=["jobs"])), "Jobs") == "idle"


def test_downloads_module_shortens_home(home, monkeypatch):
    monkeypatch.setenv("HELM_DL_DIR", os.path.join(os.path.expanduser("~"), "helm-banner-test-missing", "dl"))
    value = entry_value(build_banner(make_settings(banner__modules=["downloads"])), "Downloads")
    assert value.startswith("~" + os.sep + "helm-banner-test-missing")


def test_indexer_and_service_modules_against_real_servers(home, server, monkeypatch):
    server.routes["/api/v2.0/indexers/all/results/torznab/api"] = (
        200,
        "application/xml",
        b'<indexers><indexer id="a" configured="true"/><indexer id="b" configured="true"/></indexers>',
    )
    server.routes["/api/v1/indexer"] = (
        200,
        "application/json",
        json.dumps([{"enable": True}, {"enable": False}]).encode(),
    )
    monkeypatch.setenv("JACKETT_API_KEY", "jackett-key-123")
    monkeypatch.setenv("PROWLARR_API_KEY", "prowlarr-key-456")
    settings = make_settings(
        search__indexer_manager="both",
        services__jackett=server.url,
        services__prowlarr=server.url,
        services__qbittorrent="http://127.0.0.1:9",
        banner__modules=["indexers", "services"],
        banner__timeout_ms=3000,
    )
    banner = build_banner(settings)
    assert entry_value(banner, "Indexers") == "2 in jackett, 1 in prowlarr"
    assert entry_value(banner, "Services") == "jackett up, prowlarr up, qbittorrent down"
    assert any("configured=true" in path for path in server.routes["_seen"])


def test_indexers_without_api_key(home):
    banner = build_banner(make_settings(banner__modules=["indexers"]))
    assert entry_value(banner, "Indexers") == "jackett has no API key"


def test_indexer_server_error_is_unavailable(home, server, monkeypatch):
    monkeypatch.setenv("JACKETT_API_KEY", "jackett-key-123")
    settings = make_settings(services__jackett=server.url, banner__modules=["indexers"], banner__timeout_ms=3000)
    banner = build_banner(settings)
    assert banner.entries[0].status == "error"


# Rendering


def sample_banner(position="left"):
    return Banner(
        [[("logo", "AB")], [("accent", "C"), (None, "\x1b[31mD\x1b[0m")]],
        [Entry("Version", "1.0"), Entry("", status="separator"), Entry("Jobs", TIMED_OUT, "timeout")],
        position,
    )


def test_render_plain_left_layout():
    text = render_banner(sample_banner(), load_theme(make_settings()), color=False)
    assert text == "AB   Version  1.0\nCD   " + "─" * len("Jobs     timed out") + "\n     Jobs     timed out\n"


def test_render_plain_top_layout_and_empty_parts():
    theme = load_theme(make_settings())
    assert render_banner(sample_banner("top"), theme, color=False).startswith("AB\nCD\n\nVersion  1.0\n")
    assert render_banner(Banner([], [Entry("A", "b")]), theme, color=False) == "A  b\n"
    assert render_banner(Banner([[("logo", "X")]], []), theme, color=False) == "X\n"
    assert render_banner(Banner(), theme, color=False) == ""


def test_render_colour_uses_theme_and_keeps_alignment():
    theme = load_theme(make_settings(theme__preset="catppuccin-mocha"))
    text = render_banner(sample_banner(), theme, color=True)
    assert sgr(PRESETS["catppuccin-mocha"]["logo"]) + "AB" in text
    assert sgr(PRESETS["catppuccin-mocha"]["muted"]) + TIMED_OUT in text
    assert "\x1b[31mD\x1b[0m" in text
    plain = render_banner(sample_banner(), theme, color=False)
    assert ANSI.sub("", text) == plain


def test_visible_width_counts_wide_and_combining_characters():
    assert visible_width("\x1b[31mab\x1b[0m") == 2
    assert visible_width("漢字") == 4
    assert visible_width("é") == 1


def test_color_enabled_follows_environment(monkeypatch):
    class Terminal:
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert color_enabled(Terminal())
    assert not color_enabled(object())
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert color_enabled(object())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not color_enabled(Terminal())


# Command


def helm(*arguments, **extra_env):
    environment = {key: value for key, value in os.environ.items() if key not in SCRUBBED}
    environment["PYTHONPATH"] = SRC
    environment.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "helm.cli", *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_banner_command_prints_even_when_disabled(home):
    (home / "config" / "config.toml").write_text("[banner]\nenabled = false\n")
    result = helm("banner")
    assert result.returncode == 0, result.stderr
    assert LOGOS["helm"][0] in result.stdout
    assert "Version" in result.stdout
    assert "\x1b[" not in result.stdout


def test_banner_command_colours_when_forced(home):
    result = helm("banner", FORCE_COLOR="1", HELM_THEME="nord")
    assert sgr(PRESETS["nord"]["logo"]) in result.stdout


def test_banner_command_json_and_lite_mode(home):
    result = helm("banner", "--json", "--lite")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["theme"] == "helm"
    assert data["logo"] == LOGOS["helm"]
    assert {"label": "Mode", "value": "lite", "status": "ok"} in data["modules"]


def test_slow_module_cannot_delay_the_command(home):
    slow = json.dumps(python_command("import time; time.sleep(20)"))
    (home / "config" / "config.toml").write_text(
        f'[banner]\ntimeout_ms = 300\nmodules = ["version", {{ type = "command", label = "Slow", command = {slow} }}]\n'
    )
    started = time.monotonic()
    result = helm("banner")
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr
    assert "timed out" in result.stdout
    assert elapsed < 10


def test_banner_command_reports_bad_settings(home):
    (home / "config" / "config.toml").write_text('[banner]\nmodules = ["weather"]\n')
    result = helm("banner")
    assert result.returncode == 1
    assert "unknown module" in result.stderr
    assert "Traceback" not in result.stderr


def test_doctor_warns_about_logo_settings(home):
    config = home / "config" / "config.toml"
    cases = [
        ('source = "builtin:ship"', "unknown logo"),
        ('source = "file"', "needs logo.art"),
        (f'source = "file"\nart_file = "{home / "nope.txt"}"', "does not exist"),
        ('source = "cmd:/nonexistent/figlet helm"', "not installed"),
    ]
    for body, message in cases:
        config.write_text(f"[search]\nlite_only = true\n[logo]\n{body}\n")
        result = helm("config", "doctor")
        assert result.returncode == 0, result.stdout
        assert any(message in line for line in result.stdout.splitlines() if line.startswith("[warn]")), result.stdout
