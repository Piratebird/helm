"""Layered settings: defaults, runtime state, the user's config file, environment.

The user's config.toml or config.jsonc is never rewritten after it is created,
so its comments survive. Values Helm decides at runtime go to the state layer,
which ranks below the user's file. Secrets stay in secrets.env.
"""

import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Tuple

from helm.core import config_manager
from helm.core.config_manager import CONTENT_PROFILES, get_config_dir, get_state_dir


class ConfigError(ValueError):
    """A configuration value or file that needs the user's attention."""


@dataclass(frozen=True)
class Field:
    path: str
    kind: str
    default: Any
    env: str = ""
    legacy: str = ""
    choices: Tuple[str, ...] = ()
    minimum: Any = None
    maximum: Any = None
    # Shown in the config template for settings that have no default value.
    example: Any = None


THEMES = ("helm", "tokyo-night", "nord", "catppuccin-mocha")
COLOR_NAMES = ("logo", "accent", "text", "line", "error", "muted", "success", "warning", "info")
MODULE_NAMES = ("version", "mode", "indexers", "jobs", "downloads", "services", "separator")
DEFAULT_MODULES = ("version", "mode", "indexers", "jobs", "downloads")
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
HELM_COLORS = {
    "logo": "#4A69BD",
    "accent": "#B33939",
    "text": "#EADAC9",
    "line": "#CD6133",
    "error": "#FF5252",
    "muted": "#918383",
    # The current UI uses the terminal's own palette for these, so the helm
    # preset keeps them as basic ANSI colours instead of fixed hex values.
    "success": "ansi:32",
    "warning": "ansi:33",
    "info": "ansi:36",
}
COLOR_EXAMPLES = {name: value for name, value in HELM_COLORS.items() if value.startswith("#")}
COLOR_EXAMPLES.update({"success": "#4CAF50", "warning": "#E0AF68", "info": "#7DCFFF"})

FIELDS = (
    Field("search.min_seeds", "int", 3, legacy="min_seeds", minimum=0),
    Field("search.lite_workers", "int", 6, env="HELM_LITE_WORKERS", minimum=1, maximum=12),
    Field("search.default_type", "str", "video", choices=tuple(sorted(CONTENT_PROFILES))),
    Field(
        "search.indexer_manager",
        "str",
        "jackett",
        env="INDEXER_MANAGER",
        legacy="INDEXER_MANAGER",
        choices=("both", "jackett", "prowlarr"),
    ),
    Field("search.lite_only", "bool", False, env="HELM_LITE_ONLY", legacy="LITE_MODE_ONLY"),
    Field("services.jackett", "url", "http://localhost:19117", env="JACKETT_URL", legacy="JACKETT_URL"),
    Field("services.prowlarr", "url", "http://localhost:19696", env="PROWLARR_URL", legacy="PROWLARR_URL"),
    Field("services.qbittorrent", "url", "http://localhost:18080", env="QB_WEBUI", legacy="QB_WEBUI"),
    Field("services.qbittorrent_user", "str", "admin", env="QB_USERNAME", legacy="QB_USERNAME"),
    Field(
        "services.qbittorrent_downloads_root",
        "str",
        "/downloads",
        env="QB_DOWNLOADS_ROOT",
        legacy="QB_DOWNLOADS_ROOT",
    ),
    Field("theme.preset", "str", "helm", env="HELM_THEME", choices=THEMES),
    *(Field(f"theme.{name}", "color", None, example=COLOR_EXAMPLES[name]) for name in COLOR_NAMES),
    Field("logo.source", "logo", "builtin:helm"),
    Field("logo.position", "str", "left", choices=("left", "top")),
    Field("logo.art", "art", None, example="{logo}  ~~ my helm ~~  {accent}<3"),
    Field("logo.art_file", "path", None, example="~/.config/helm/logo.txt"),
    Field("banner.enabled", "bool", True, env="HELM_BANNER"),
    Field("banner.modules", "modules", DEFAULT_MODULES),
    Field("banner.timeout_ms", "int", 400, minimum=50, maximum=5000),
)
BY_PATH = {field.path: field for field in FIELDS}
USER_FILES = ("config.toml", "config.jsonc")

_file_cache: dict = {}


def parse_jsonc(text):
    """Parse JSON with // and /* */ comments and trailing commas."""
    output = []
    index = 0
    in_string = False
    while index < len(text):
        character = text[index]
        pair = text[index : index + 2]
        if in_string:
            output.append(character)
            if character == "\\":
                output.append(text[index + 1 : index + 2])
                index += 1
            elif character == '"':
                in_string = False
            index += 1
        elif character == '"':
            in_string = True
            output.append(character)
            index += 1
        elif pair == "//":
            end = text.find("\n", index)
            index = len(text) if end == -1 else end
        elif pair == "/*":
            end = text.find("*/", index + 2)
            if end == -1:
                raise ValueError("Unterminated block comment")
            # Keeping line breaks keeps parser error positions accurate.
            output.append("\n" * text.count("\n", index, end))
            index = end + 2
        else:
            if character in "]}":
                position = len(output) - 1
                while position >= 0 and output[position].isspace():
                    position -= 1
                if position >= 0 and output[position] == ",":
                    output[position] = " "
            output.append(character)
            index += 1
    return json.loads("".join(output))


def read_file(path):
    """Read a TOML, JSONC or JSON config file into a dictionary."""
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"Cannot read {path}: {error}") from error
    try:
        if str(path).endswith(".toml"):
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]
            data = tomllib.loads(text)
        else:
            data = parse_jsonc(text) if text.strip() else {}
    except ValueError as error:
        raise ConfigError(f"Invalid syntax in {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a table of settings")
    return data


def flatten(data, prefix=""):
    values = {}
    for key, value in data.items():
        path = prefix + str(key)
        if isinstance(value, dict):
            values.update(flatten(value, path + "."))
        else:
            values[path] = value
    return values


def _check_logo_source(value):
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if value in ("none", "file"):
        return value
    kind, _, rest = value.partition(":")
    if kind == "builtin" and rest.strip():
        return value
    if kind == "cmd":
        try:
            if shlex.split(rest):
                return value
        except ValueError as error:
            raise ValueError(f"has an invalid command: {error}") from None
    raise ValueError('must be "none", "file", "builtin:<name>" or "cmd:<command>"')


def _check_art(value):
    if isinstance(value, list) and all(isinstance(line, str) for line in value):
        return "\n".join(value)
    if isinstance(value, str):
        return value
    raise ValueError("must be a string or a list of strings")


def _check_module(item):
    if isinstance(item, str):
        if item not in MODULE_NAMES:
            raise ValueError(f"has unknown module {item!r}; use one of " + ", ".join(MODULE_NAMES))
        return item
    if not isinstance(item, dict) or item.get("type") != "command":
        raise ValueError('entries must be module names or tables with type = "command"')
    extra = set(item) - {"type", "label", "command"}
    if extra:
        raise ValueError("command entries only take type, label and command, not " + ", ".join(sorted(extra)))
    label = item.get("label")
    command = item.get("command")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("command entries need a non-empty label")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("command entries need command as a non-empty list of strings")
    return {"type": "command", "label": label, "command": list(command)}


def check(field, value):
    """Return a valid value for a field, or raise ValueError."""
    if field.kind == "color":
        if not isinstance(value, str) or not HEX_COLOR.match(value):
            raise ValueError('must be a colour like "#4A69BD"')
        return value
    if field.kind == "logo":
        return _check_logo_source(value)
    if field.kind == "art":
        return _check_art(value)
    if field.kind == "modules":
        if not isinstance(value, list):
            raise ValueError("must be a list")
        return tuple(_check_module(item) for item in value)
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
    elif field.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("must be a whole number")
        if field.minimum is not None and value < field.minimum:
            raise ValueError(f"must be at least {field.minimum}")
        if field.maximum is not None and value > field.maximum:
            raise ValueError(f"must be at most {field.maximum}")
    else:
        if not isinstance(value, str) or not value:
            raise ValueError("must be a non-empty string")
        if field.kind == "url":
            if not value.lower().startswith(("http://", "https://")):
                raise ValueError("must start with http:// or https://")
            value = value.rstrip("/")
        if field.choices and value not in field.choices:
            raise ValueError("must be one of " + ", ".join(field.choices))
    return value


def from_env(field, text):
    if field.kind == "bool":
        return text.strip().lower() in ("1", "true", "yes", "on")
    if field.kind == "int":
        try:
            return int(text)
        except ValueError:
            raise ValueError("must be a whole number") from None
    return text


class Settings:
    """Resolved values, and the layer each one came from."""

    def __init__(self, values=None, origins=None, path=None, unknown=()):
        self._values = {field.path: field.default for field in FIELDS}
        self._values.update(values or {})
        self._origins = dict(origins or {})
        self.path = path
        self.unknown = tuple(unknown)

    @classmethod
    def from_legacy(cls, config):
        values = map_legacy(config)
        return cls(values, {path: "file" for path in values})

    def get(self, path):
        return self._values[path]

    def origin(self, path):
        if path not in BY_PATH:
            raise KeyError(path)
        return self._origins.get(path, "default")


def state_file():
    return os.path.join(get_state_dir(), "settings.json")


def active_config_file():
    """Return the config file in use. TOML wins, and files are never merged."""
    for name in USER_FILES:
        path = os.path.join(get_config_dir(), name)
        if os.path.isfile(path):
            return path
    return None


def shadowed_config_files():
    """Return config files ignored because a higher-priority one exists."""
    paths = [os.path.join(get_config_dir(), name) for name in USER_FILES]
    return [path for path in paths if os.path.isfile(path)][1:]


def report_config_file():
    """Return the file a bug report should describe."""
    return active_config_file() or config_manager.get_config_file()


def atomic_write(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".helm-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def map_legacy(config):
    """Convert config.json keys, keeping only valid values that differ from defaults.

    A default written by old code must not outrank later runtime changes.
    """
    values = {}
    for field in FIELDS:
        if field.legacy and field.legacy in config:
            try:
                value = check(field, config[field.legacy])
            except ValueError:
                continue
            if value != field.default:
                values[field.path] = value
    return values


def render_toml(values):
    sections: dict = {}
    for path, value in values.items():
        section, key = path.split(".", 1)
        sections.setdefault(section, []).append((key, value))
    lines = ["# Helm settings. Helm never rewrites this file, so edit it freely.", ""]
    for section, items in sections.items():
        lines.append(f"[{section}]")
        # JSON strings, booleans and integers are all valid TOML values here.
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in items)
        lines.append("")
    return "\n".join(lines)


def migrate_legacy():
    """Create config.toml from config.json once. The original file stays in place."""
    if active_config_file():
        return
    config_manager._migrate_old_config()
    legacy = config_manager.get_config_file()
    if not os.path.isfile(legacy):
        return
    try:
        data = read_file(legacy)
    except ConfigError:
        backup = legacy + ".bak"
        if not os.path.exists(backup):
            shutil.copy(legacy, backup)
            print(f"Ignoring corrupted config; a copy is at {backup}", file=sys.stderr)
        return
    if not data:
        return

    from helm.core.secret_manager import migrate_config_secrets

    had_secrets = any(key in data for key in config_manager.SECRET_KEYS)
    data = migrate_config_secrets(data)
    if had_secrets:
        atomic_write(legacy, json.dumps(data, indent=4))
    values = map_legacy(data)
    if values:
        shutil.copy2(legacy, legacy + ".pre-toml.bak")
        target = os.path.join(get_config_dir(), USER_FILES[0])
        atomic_write(target, render_toml(values))
        print(f"Migrated settings to {target}", file=sys.stderr)


def _load_cached(path):
    """Parse a file once per change, so long-running services see edits cheaply."""
    details = os.stat(path)
    key = (details.st_mtime_ns, details.st_size)
    cached = _file_cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    data = flatten(read_file(path))
    _file_cache[path] = (key, data)
    return data


def _load_state():
    """Read runtime values. Helm owns this file, so damage only loses those values."""
    path = state_file()
    if not os.path.isfile(path):
        return {}
    try:
        data = _load_cached(path)
    except ConfigError:
        return {}
    values = {}
    for key, value in data.items():
        if key in BY_PATH:
            try:
                values[key] = check(BY_PATH[key], value)
            except ValueError:
                continue
    return values


def load_settings():
    """Resolve settings: environment, then config file, then state, then defaults."""
    migrate_legacy()
    settings, problems = resolve_settings(strict=True)
    if problems:
        raise ConfigError(f"Invalid settings ({settings.path or 'environment'}): " + "; ".join(problems))
    return settings


def resolve_settings(strict=False):
    """Resolve settings without migrating, and return them with every problem found.

    Invalid values are skipped, so the result holds what would be used if they
    were removed. With strict=True a file that cannot be parsed raises instead.
    """
    values = {}
    origins = {}
    for path, value in _load_state().items():
        values[path] = value
        origins[path] = "state"

    active = active_config_file()
    unknown = []
    problems = []
    data = {}
    if active:
        try:
            data = _load_cached(active)
        except ConfigError as error:
            if strict:
                raise
            problems.append(str(error))
        for path, value in data.items():
            field = BY_PATH.get(path)
            if field is None:
                unknown.append(path)
                continue
            try:
                values[path] = check(field, value)
                origins[path] = "file"
            except ValueError as error:
                problems.append(f"{path} {error}")

    for field in FIELDS:
        text = os.environ.get(field.env) if field.env else None
        if text:
            try:
                values[field.path] = check(field, from_env(field, text))
                origins[field.path] = "env"
            except ValueError as error:
                problems.append(f"{field.env} {error}")

    return Settings(values, origins, active, unknown), problems


def remember(updates):
    """Store runtime values, and return those the user's file will keep overriding."""
    clean = {}
    for path, value in updates.items():
        if path not in BY_PATH:
            raise KeyError(path)
        clean[path] = check(BY_PATH[path], value)
    existing = {}
    path = state_file()
    if os.path.isfile(path):
        try:
            existing = flatten(read_file(path))
        except ConfigError:
            pass
    existing.update(clean)
    atomic_write(path, json.dumps(existing, indent=4))
    settings = load_settings()
    return [key for key in clean if settings.origin(key) == "file"]


def get_qb_url():
    return load_settings().get("services.qbittorrent")


def settings_schema():
    """Return one JSON Schema that describes both config formats."""
    kinds = {
        "bool": "boolean",
        "int": "integer",
        "str": "string",
        "url": "string",
        "color": "string",
        "logo": "string",
        "path": "string",
    }
    strings = {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}
    command = {
        "type": "object",
        "properties": {"type": {"const": "command"}, "label": {"type": "string", "minLength": 1}, "command": strings},
        "required": ["type", "label", "command"],
        "additionalProperties": False,
    }
    special = {
        "art": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
        "modules": {"type": "array", "items": {"anyOf": [{"enum": list(MODULE_NAMES)}, command]}},
    }
    sections: dict = {}
    for field in FIELDS:
        section, key = field.path.split(".", 1)
        entry: dict = dict(special[field.kind]) if field.kind in special else {"type": kinds[field.kind]}
        entry["default"] = list(field.default) if isinstance(field.default, tuple) else field.default
        if field.kind == "color":
            entry["pattern"] = HEX_COLOR.pattern
        if field.kind == "logo":
            entry["pattern"] = "^(none|file|builtin:.+|cmd:.+)$"
        if field.kind == "url":
            entry["pattern"] = "^[Hh][Tt][Tt][Pp][Ss]?://"
        if field.choices:
            entry["enum"] = list(field.choices)
        if field.minimum is not None:
            entry["minimum"] = field.minimum
        if field.maximum is not None:
            entry["maximum"] = field.maximum
        sections.setdefault(section, {"type": "object", "properties": {}})["properties"][key] = entry
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": sections}
