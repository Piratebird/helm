import io
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import helm.cli
import helm.cli.deps
from helm.cli.commands import banner as banner_command
from helm.config.settings import FIELDS
from helm.ui.ansi import CONTROLS, T, Terminal, sgr
from helm.ui.banner import LOGOS
from helm.ui.theme import PRESETS

SRC = Path(__file__).resolve().parents[1] / "src" / "helm"
EMITTED = re.compile(r"\\033\[|\\x1b\[|\\u001b\[|\\e\[")


@pytest.fixture
def home(tmp_path, monkeypatch):
    for key, name in (("HELM_CONFIG_DIR", "config"), ("HELM_STATE_DIR", "state"), ("HELM_DL_DIR", "downloads")):
        monkeypatch.setenv(key, str(tmp_path / name))
    (tmp_path / "config").mkdir()
    for key in {field.env for field in FIELDS if field.env} | {"NO_COLOR", "FORCE_COLOR"}:
        monkeypatch.delenv(key, raising=False)
    T.reset()
    yield tmp_path
    T.reset()


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_no_escape_codes_are_written_outside_the_renderer():
    allowed = {
        # Regular expressions that read escapes in command output; they write none.
        ("ui/banner.py", "SGR = re.compile"),
        ("ui/banner.py", "UNSAFE = re.compile"),
    }
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative == "ui/ansi.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if EMITTED.search(line) and not any(relative == name and marker in line for name, marker in allowed):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == []


def test_old_colour_module_is_gone():
    assert not (SRC / "ui" / "colors.py").exists()


def test_terminal_codes_follow_the_theme_on_a_terminal(home, monkeypatch):
    (home / "config" / "config.toml").write_text('[theme]\npreset = "nord"\naccent = "#010203"\n')
    monkeypatch.setattr(sys, "stdout", Tty())
    codes = Terminal()
    assert codes.LOGO == sgr(PRESETS["nord"]["logo"])
    assert codes.ACCENT == "\033[38;2;1;2;3m"
    assert codes.SUCCESS == sgr(PRESETS["nord"]["success"])
    assert codes.BOLD == "\033[1m"
    assert codes.RST == "\033[0m"
    assert codes.EOL == CONTROLS["EOL"]


def test_colours_are_off_when_piped_or_no_color(home, monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    piped = Terminal()
    assert piped.LOGO == piped.BOLD == piped.RST == ""
    assert piped.HOME == "\033[H" and piped.CLEAR == "\033[2J"
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setenv("NO_COLOR", "1")
    assert Terminal().LOGO == ""


def test_helm_preset_keeps_basic_terminal_colours(home, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    codes = Terminal()
    assert (codes.SUCCESS, codes.WARNING, codes.INFO) == ("\033[32m", "\033[33m", "\033[36m")
    assert codes.LOGO == "\033[38;2;74;105;189m"


def test_broken_config_falls_back_to_the_default_theme(home, monkeypatch):
    (home / "config" / "config.toml").write_text("[theme\n")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert Terminal().LOGO == sgr(PRESETS["helm"]["logo"])


def test_terminal_reset_and_unknown_names(home, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    codes = Terminal()
    assert codes.LOGO == sgr(PRESETS["helm"]["logo"])
    monkeypatch.setenv("HELM_THEME", "tokyo-night")
    assert codes.LOGO == sgr(PRESETS["helm"]["logo"])
    codes.reset()
    assert codes.LOGO == sgr(PRESETS["tokyo-night"]["logo"])
    with pytest.raises(AttributeError):
        _ = codes.PURPLE
    with pytest.raises(AttributeError):
        _ = codes._private


def items():
    from test_cli import _make_items

    return _make_items()


@pytest.mark.parametrize("found", [True, False])
def test_json_stdout_stays_pure_with_colours_forced(home, monkeypatch, capsys, found):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("JACKETT_API_KEY", "testkey")
    monkeypatch.setattr(helm.cli.deps, "ensure_config", lambda: None)
    monkeypatch.setattr(
        helm.cli.deps,
        "animated_search",
        lambda query, content_type, lite_mode=False, show_spinner=True: (items() if found else [], True),
    )
    monkeypatch.setattr(sys, "argv", ["helm-torrent", "search", "--json", "--lite", "ubuntu"])
    with pytest.raises(SystemExit):
        helm.cli.main()
    out, _err = capsys.readouterr()
    assert "\033" not in out
    assert LOGOS["helm"][0] not in out
    if found:
        assert len(json.loads(out)) == 2
    else:
        assert out.strip() in ("", "[]")


def args(**values):
    defaults = {"lite": False, "oneshot": False, "json": False}
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_ui_header_shows_banner_when_enabled(home, capsys):
    (home / "config" / "config.toml").write_text('[banner]\nmodules = ["mode"]\n')
    banner_command.print_header(args(lite=True), "THE HELM - Torrent automation MVP")
    out = capsys.readouterr().out
    assert LOGOS["helm"][0] in out
    assert re.search(r"Mode\s+lite", out)
    assert "THE HELM - Torrent automation MVP" in out
    assert "\033" not in out


def test_ui_header_shows_plain_wordmark_when_disabled(home, capsys):
    (home / "config" / "config.toml").write_text("[banner]\nenabled = false\n")
    banner_command.print_header(args(oneshot=True), "subtitle")
    out = capsys.readouterr().out
    assert LOGOS["helm"][0] in out
    assert "Mode" not in out
    assert out.rstrip().endswith("subtitle")


def test_banner_mode_context(home):
    assert banner_command.banner_context(args(lite=True)) == {"mode": "lite"}
    assert banner_command.banner_context(args(oneshot=True)) == {"mode": "one-shot"}
    assert banner_command.banner_context(args()) == {}
