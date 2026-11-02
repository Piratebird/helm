import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from helm.core import settings as settings_module
from helm.core.secret_manager import get_secret
from helm.core.settings import (
    BY_PATH,
    FIELDS,
    ConfigError,
    Settings,
    active_config_file,
    get_qb_url,
    load_settings,
    map_legacy,
    parse_jsonc,
    read_file,
    remember,
    render_toml,
    report_config_file,
    settings_schema,
    shadowed_config_files,
)

SRC = str(Path(__file__).resolve().parents[1] / "src")
TOML = """
# comment
[search]
min_seeds = 7
lite_workers = 2
indexer_manager = "prowlarr"
lite_only = true

[services]
jackett = "http://jackett.test:9117/"
qbittorrent_user = "pirate"
"""
JSONC = """
// comment
{
  "search": {
    "min_seeds": 7, /* inline */
    "lite_workers": 2,
    "indexer_manager": "prowlarr",
    "lite_only": true,
  },
  "services": {
    "jackett": "http://jackett.test:9117/",
    "qbittorrent_user": "pirate",
  },
}
"""


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setenv("HELM_CONFIG_DIR", str(path))
    monkeypatch.setenv("HELM_STATE_DIR", str(tmp_path / "state"))
    for field in FIELDS:
        if field.env:
            monkeypatch.delenv(field.env, raising=False)
    for key in ("JACKETT_API_KEY", "QB_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    return path


def snapshot(settings):
    return {field.path: settings.get(field.path) for field in FIELDS}


def test_defaults_without_any_file(config_dir):
    settings = load_settings()
    assert settings.get("search.min_seeds") == 3
    assert settings.origin("search.min_seeds") == "default"
    assert settings.path is None
    assert list(config_dir.iterdir()) == []


def test_toml_and_jsonc_load_identically(config_dir):
    (config_dir / "config.toml").write_text(TOML)
    from_toml = snapshot(load_settings())
    (config_dir / "config.toml").unlink()
    (config_dir / "config.jsonc").write_text(JSONC)
    from_jsonc = snapshot(load_settings())
    assert from_toml == from_jsonc
    assert from_toml["search.min_seeds"] == 7
    assert from_toml["services.jackett"] == "http://jackett.test:9117"


def test_toml_wins_when_both_exist_and_files_never_merge(config_dir):
    (config_dir / "config.toml").write_text("[search]\nmin_seeds = 1\n")
    (config_dir / "config.jsonc").write_text('{"search": {"min_seeds": 9, "lite_workers": 2}}')
    settings = load_settings()
    assert settings.get("search.min_seeds") == 1
    assert settings.get("search.lite_workers") == 6
    assert active_config_file().endswith("config.toml")
    assert [Path(path).name for path in shadowed_config_files()] == ["config.jsonc"]


def test_literal_art_is_preserved_in_toml(config_dir):
    path = config_dir / "config.toml"
    quotes = "'" * 3
    path.write_text("[logo]\nart = " + quotes + "\n _\\_/_\n" + quotes + "\n")
    assert read_file(str(path)) == {"logo": {"art": " _\\_/_\n"}}


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": "http://x//y"}', {"a": "http://x//y"}),
        ('{"a": "/* not a comment */"}', {"a": "/* not a comment */"}),
        ('{"a": "quote \\" // still text"}', {"a": 'quote " // still text'}),
        ('{"a": ",]"}', {"a": ",]"}),
        ("[1, 2, ]", [1, 2]),
        ('{"a": [1,],}', {"a": [1]}),
        ("/* a\nb */ {}", {}),
        ("{} // end", {}),
    ],
)
def test_parse_jsonc(text, expected):
    assert parse_jsonc(text) == expected


@pytest.mark.parametrize("text", ["{", "/* open", '{"a": 1,,}'])
def test_parse_jsonc_rejects_invalid_input(text):
    with pytest.raises(ValueError):
        parse_jsonc(text)


def test_jsonc_error_keeps_line_number():
    with pytest.raises(json.JSONDecodeError) as error:
        parse_jsonc('/* one\ntwo */\n{"a" 1}')
    assert error.value.lineno == 3


@pytest.mark.parametrize(
    "name,text,message",
    [
        ("config.toml", "[search\n", "Invalid syntax"),
        ("config.jsonc", "{", "Invalid syntax"),
        ("config.jsonc", "[1]", "table of settings"),
    ],
)
def test_broken_user_file_is_reported_and_left_alone(config_dir, name, text, message):
    path = config_dir / name
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_settings()
    assert path.read_text() == text


@pytest.mark.parametrize(
    "text,message",
    [
        ("[search]\nmin_seeds = -1\n", "at least 0"),
        ('[search]\nmin_seeds = "3"\n', "whole number"),
        ("[search]\nmin_seeds = true\n", "whole number"),
        ("[search]\nlite_workers = 13\n", "at most 12"),
        ('[search]\nlite_only = "yes"\n', "true or false"),
        ('[search]\nindexer_manager = "other"\n', "one of"),
        ('[search]\ndefault_type = "other"\n', "one of"),
        ('[services]\njackett = "jackett:9117"\n', "http"),
        ('[services]\nqbittorrent_user = ""\n', "non-empty"),
    ],
)
def test_invalid_values_name_key_and_file(config_dir, text, message):
    (config_dir / "config.toml").write_text(text)
    with pytest.raises(ConfigError, match=message) as error:
        load_settings()
    assert "config.toml" in str(error.value)


def test_all_problems_are_reported_together(config_dir):
    (config_dir / "config.toml").write_text('[search]\nmin_seeds = -1\nlite_only = "x"\n')
    with pytest.raises(ConfigError) as error:
        load_settings()
    assert "search.min_seeds" in str(error.value) and "search.lite_only" in str(error.value)


def test_unknown_keys_are_kept_for_doctor(config_dir):
    (config_dir / "config.toml").write_text("[search]\ntypo = 1\n[future]\nsetting = 'x'\n")
    assert sorted(load_settings().unknown) == ["future.setting", "search.typo"]


def test_precedence_env_file_state_default(config_dir, monkeypatch):
    path = "services.prowlarr"
    remember({path: "http://state.test"})
    assert (load_settings().get(path), load_settings().origin(path)) == ("http://state.test", "state")
    (config_dir / "config.toml").write_text('[services]\nprowlarr = "http://file.test"\n')
    assert (load_settings().get(path), load_settings().origin(path)) == ("http://file.test", "file")
    monkeypatch.setenv("PROWLARR_URL", "http://env.test/")
    assert (load_settings().get(path), load_settings().origin(path)) == ("http://env.test", "env")


def test_empty_environment_value_is_unset(config_dir, monkeypatch):
    monkeypatch.setenv("QB_WEBUI", "")
    assert load_settings().origin("services.qbittorrent") == "default"


@pytest.mark.parametrize("text,expected", [("1", True), ("TRUE", True), ("yes", True), ("0", False), ("no", False)])
def test_environment_booleans(config_dir, monkeypatch, text, expected):
    monkeypatch.setenv("HELM_LITE_ONLY", text)
    assert load_settings().get("search.lite_only") is expected


def test_invalid_environment_names_the_variable(config_dir, monkeypatch):
    monkeypatch.setenv("QB_WEBUI", "not-a-url")
    with pytest.raises(ConfigError, match="QB_WEBUI"):
        load_settings()
    monkeypatch.delenv("QB_WEBUI")
    monkeypatch.setenv("HELM_LITE_WORKERS", "many")
    with pytest.raises(ConfigError, match="HELM_LITE_WORKERS"):
        load_settings()


def test_get_qb_url(config_dir, monkeypatch):
    monkeypatch.setenv("QB_WEBUI", "http://qb.test/")
    assert get_qb_url() == "http://qb.test"


def test_edits_are_seen_without_restarting(config_dir):
    path = config_dir / "config.toml"
    path.write_text("[search]\nmin_seeds = 1\n")
    assert load_settings().get("search.min_seeds") == 1
    path.write_text("[search]\nmin_seeds = 22\n")
    assert load_settings().get("search.min_seeds") == 22


def test_repeated_loads_parse_once(config_dir, monkeypatch):
    (config_dir / "config.toml").write_text("[search]\nmin_seeds = 1\n")
    calls = []
    real = settings_module.read_file
    monkeypatch.setattr(settings_module, "read_file", lambda path: calls.append(path) or real(path))
    for _ in range(5):
        load_settings()
    assert len(calls) == 1


def test_remember_is_private_keeps_user_file_and_reports_shadowing(config_dir):
    user = config_dir / "config.toml"
    original = "# keep me\n[search]\nmin_seeds = 9\n"
    user.write_text(original)
    assert remember({"search.min_seeds": 4, "search.lite_only": True}) == ["search.min_seeds"]
    assert user.read_text() == original
    settings = load_settings()
    assert settings.get("search.min_seeds") == 9
    assert settings.get("search.lite_only") is True
    state = Path(settings_module.state_file())
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert not [item for item in state.parent.iterdir() if item.suffix == ".tmp"]


def test_remember_validates(config_dir):
    with pytest.raises(KeyError):
        remember({"search.nope": 1})
    with pytest.raises(ValueError):
        remember({"search.min_seeds": "x"})


def test_damaged_state_does_not_stop_startup(config_dir):
    state = Path(settings_module.state_file())
    state.parent.mkdir(parents=True)
    state.write_text("{")
    assert load_settings().get("search.min_seeds") == 3
    remember({"search.min_seeds": 5})
    assert load_settings().get("search.min_seeds") == 5
    state.write_text('{"search.min_seeds": "bad", "other": 1}')
    assert load_settings().origin("search.min_seeds") == "default"


def test_map_legacy_drops_defaults_invalid_values_and_retired_keys():
    assert map_legacy(
        {
            "min_seeds": 3,
            "LITE_MODE_ONLY": False,
            "QB_USERNAME": "pirate",
            "JACKETT_URL": "http://localhost:9117",
            "INDEXER_MANAGER": "unknown",
            "qualities": ["1080p"],
            "EXECUTION_MODE": "native",
            "indexers": ["yts"],
        }
    ) == {"services.qbittorrent_user": "pirate", "services.jackett": "http://localhost:9117"}


def test_render_toml_round_trips(tmp_path):
    values = {"search.min_seeds": 5, "search.lite_only": True, "services.jackett": 'http://x/"q"'}
    path = tmp_path / "config.toml"
    path.write_text(render_toml(values))
    assert read_file(str(path)) == {
        "search": {"min_seeds": 5, "lite_only": True},
        "services": {"jackett": 'http://x/"q"'},
    }


def test_migration_creates_toml_once_and_keeps_original(config_dir, capsys):
    legacy = config_dir / "config.json"
    original = json.dumps({"min_seeds": 8, "QB_WEBUI": "http://localhost:8080", "qualities": ["x"]})
    legacy.write_text(original)
    settings = load_settings()
    assert settings.get("search.min_seeds") == 8
    assert settings.get("services.qbittorrent") == "http://localhost:8080"
    assert settings.origin("search.min_seeds") == "file"
    assert legacy.read_text() == original
    assert (config_dir / "config.json.pre-toml.bak").read_text() == original
    assert "Migrated" in capsys.readouterr().err

    target = config_dir / "config.toml"
    target.write_text("[search]\nmin_seeds = 2\n")
    legacy.write_text(json.dumps({"min_seeds": 99}))
    assert load_settings().get("search.min_seeds") == 2
    assert target.read_text() == "[search]\nmin_seeds = 2\n"


def test_migration_moves_secrets_out_of_config_json(config_dir):
    legacy = config_dir / "config.json"
    legacy.write_text(json.dumps({"JACKETT_API_KEY": "abc123", "min_seeds": 8}))
    load_settings()
    assert get_secret("JACKETT_API_KEY") == "abc123"
    assert "abc123" not in legacy.read_text()
    assert "abc123" not in (config_dir / "config.toml").read_text()


@pytest.mark.parametrize("text", ["", "{}", '{"min_seeds": 3, "qualities": []}'])
def test_nothing_to_migrate_creates_no_file(config_dir, text):
    (config_dir / "config.json").write_text(text)
    assert load_settings().path is None
    assert not (config_dir / "config.toml").exists()


def test_corrupt_legacy_file_is_backed_up_once(config_dir, capsys):
    (config_dir / "config.json").write_text("{broken")
    assert load_settings().get("search.min_seeds") == 3
    assert load_settings().get("search.min_seeds") == 3
    assert (config_dir / "config.json.bak").read_text() == "{broken"
    assert capsys.readouterr().err.count("corrupted") == 1


def test_setup_rerun_before_migration_is_honoured(config_dir):
    (config_dir / "config.json").write_text('{"INDEXER_MANAGER": "prowlarr"}')
    assert load_settings().get("search.indexer_manager") == "prowlarr"


def test_report_config_file_prefers_active_file(config_dir):
    assert report_config_file().endswith("config.json")
    (config_dir / "config.jsonc").write_text("{}")
    assert report_config_file().endswith("config.jsonc")


def test_settings_from_legacy_and_unknown_origin():
    settings = Settings.from_legacy({"min_seeds": 9})
    assert settings.get("search.min_seeds") == 9
    assert settings.origin("search.min_seeds") == "file"
    with pytest.raises(KeyError):
        settings.origin("search.nope")


def test_schema_covers_every_field_and_its_defaults():
    schema = settings_schema()
    for field in FIELDS:
        section, key = field.path.split(".", 1)
        entry = schema["properties"][section]["properties"][key]
        assert entry["default"] == json.loads(json.dumps(field.default))
    assert schema["properties"]["search"]["properties"]["lite_workers"]["maximum"] == 12
    assert "prowlarr" in schema["properties"]["search"]["properties"]["indexer_manager"]["enum"]
    assert BY_PATH["services.jackett"].default == "http://localhost:19117"


def helm(config_dir, *arguments):
    state = str(config_dir.parent / "state")
    environment = {
        **os.environ,
        "PYTHONPATH": SRC,
        "HELM_CONFIG_DIR": str(config_dir),
        "HELM_STATE_DIR": state,
    }
    return subprocess.run(
        [sys.executable, "-m", "helm.cli", *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_broken_config_gives_clean_error_through_cli(config_dir):
    (config_dir / "config.toml").write_text("[search]\nmin_seeds = -1\n")
    result = helm(config_dir, "paths")
    assert result.returncode == 1
    assert "search.min_seeds" in result.stderr
    assert "Traceback" not in result.stderr


def test_console_entry_point_reports_config_errors_cleanly(config_dir):
    (config_dir / "config.toml").write_text("[search\n")
    environment = {
        **os.environ,
        "PYTHONPATH": SRC,
        "HELM_CONFIG_DIR": str(config_dir),
        "HELM_STATE_DIR": str(config_dir.parent / "state"),
    }
    code = "import sys, helm.cli; sys.argv = ['helm-torrent', 'status']; helm.cli.main()"
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "Invalid syntax" in result.stderr
    assert "Traceback" not in result.stderr


def test_config_worker_count_reaches_the_process(config_dir):
    (config_dir / "config.toml").write_text("[search]\nlite_workers = 3\n")
    code = (
        "import os, sys\n"
        "sys.argv = ['helm-torrent', 'paths']\n"
        "import helm.cli\n"
        "try:\n"
        "    helm.cli.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(os.environ['HELM_LITE_WORKERS'])\n"
    )
    environment = {
        **{key: value for key, value in os.environ.items() if key != "HELM_LITE_WORKERS"},
        "PYTHONPATH": SRC,
        "HELM_CONFIG_DIR": str(config_dir),
        "HELM_STATE_DIR": str(config_dir.parent / "state"),
    }
    result = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=30)
    assert result.stdout.strip().endswith("3"), result.stderr


@pytest.mark.parametrize(
    "path, value, expected",
    [
        ("theme.preset", "nord", "nord"),
        ("theme.logo", "#aabbcc", "#aabbcc"),
        ("logo.source", "builtin:helm", "builtin:helm"),
        ("logo.source", "cmd:figlet 'the helm'", "cmd:figlet 'the helm'"),
        ("logo.source", "none", "none"),
        ("logo.art", ["a", "b"], "a\nb"),
        ("logo.art", "a\nb", "a\nb"),
        ("banner.modules", ["version", "separator"], ("version", "separator")),
        (
            "banner.modules",
            [{"type": "command", "label": "Up", "command": ["uptime"]}],
            ({"type": "command", "label": "Up", "command": ["uptime"]},),
        ),
    ],
)
def test_banner_and_theme_values_are_accepted(path, value, expected):
    from helm.core.settings import check

    assert check(BY_PATH[path], value) == expected


@pytest.mark.parametrize(
    "path, value, message",
    [
        ("theme.preset", "solarized", "must be one of"),
        ("theme.logo", "blue", "colour"),
        ("theme.logo", "#abc", "colour"),
        ("logo.source", "cmd:", "must be"),
        ("logo.source", "cmd:'unclosed", "invalid command"),
        ("logo.source", "builtin:", "must be"),
        ("logo.source", 3, "string"),
        ("logo.art", ["a", 1], "list of strings"),
        ("banner.modules", "version", "must be a list"),
        ("banner.modules", ["weather"], "unknown module"),
        ("banner.modules", [{"type": "shell"}], 'type = "command"'),
        ("banner.modules", [{"type": "command", "label": "", "command": ["x"]}], "label"),
        ("banner.modules", [{"type": "command", "label": "x", "command": "uptime"}], "list of strings"),
        ("banner.modules", [{"type": "command", "label": "x", "command": ["x"], "shell": True}], "not shell"),
        ("banner.timeout_ms", 10, "at least 50"),
    ],
)
def test_banner_and_theme_values_are_rejected(path, value, message):
    from helm.core.settings import check

    with pytest.raises(ValueError, match=message):
        check(BY_PATH[path], value)


def test_banner_settings_load_from_toml_and_jsonc(config_dir):
    (config_dir / "config.toml").write_text(
        '[theme]\npreset = "nord"\n[banner]\nmodules = ["jobs", { type = "command", label = "Up", command = ["uptime"] }]\n'
    )
    from_toml = load_settings()
    (config_dir / "config.toml").unlink()
    (config_dir / "config.jsonc").write_text(
        '{"theme": {"preset": "nord"}, "banner": {"modules": ["jobs", {"type": "command", "label": "Up", "command": ["uptime"]}]}}'
    )
    from_jsonc = load_settings()
    for path in ("theme.preset", "banner.modules"):
        assert from_toml.get(path) == from_jsonc.get(path)
    assert from_toml.get("banner.modules")[1]["command"] == ["uptime"]


def test_theme_and_banner_environment_overrides(config_dir, monkeypatch):
    monkeypatch.setenv("HELM_THEME", "tokyo-night")
    monkeypatch.setenv("HELM_BANNER", "0")
    settings = load_settings()
    assert settings.get("theme.preset") == "tokyo-night"
    assert settings.get("banner.enabled") is False
    assert settings.origin("theme.preset") == "env"
