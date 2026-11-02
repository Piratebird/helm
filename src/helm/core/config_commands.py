"""The `helm-torrent config` commands: init, edit, doctor and show.

These commands must keep working when the config file is broken, so they never
rely on load_settings() succeeding, except `show`, which reports the error.
"""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from helm.core.config_manager import get_config_dir, get_dl_dir, get_state_dir
from helm.core.secret_manager import get_secrets_file
from helm.core.settings import (
    FIELDS,
    USER_FILES,
    ConfigError,
    active_config_file,
    atomic_write,
    load_settings,
    map_legacy,
    migrate_legacy,
    read_file,
    resolve_settings,
    shadowed_config_files,
)

DESCRIPTIONS = {
    "search.min_seeds": "Hide results with fewer seeders than this.",
    "search.lite_workers": "Maximum concurrent Lite source processes, 1 to 12.",
    "search.default_type": "Content profile used when none is given.",
    "search.indexer_manager": "Which manager to search: jackett, prowlarr or both.",
    "search.lite_only": "Always use Lite mode and skip the indexer managers.",
    "services.jackett": "Jackett web address.",
    "services.prowlarr": "Prowlarr web address.",
    "services.qbittorrent": "qBittorrent Web UI address.",
    "services.qbittorrent_user": "qBittorrent user name. The password stays in secrets.env.",
    "services.qbittorrent_downloads_root": "Download path as qBittorrent sees it, such as a container mount.",
    "theme.preset": "Colour preset: helm, tokyo-night, nord or catppuccin-mocha.",
    "theme.logo": "Override the preset's logo colour.",
    "theme.accent": "Override the preset's accent colour, used for labels.",
    "theme.text": "Override the preset's text colour.",
    "theme.line": "Override the preset's line colour, used for separators.",
    "theme.error": "Override the preset's error colour.",
    "theme.muted": "Override the preset's muted colour, used for placeholders.",
    "logo.source": 'Logo to show: "builtin:helm", "file" (art or art_file), "cmd:<command>" or "none".',
    "logo.position": "Logo position next to the banner text: left or top.",
    "logo.art": 'Your own art for source = "file". Colour placeholders: {logo} {accent} {text} {line} {muted} {reset}.',
    "logo.art_file": 'File holding your art, used when source = "file" and art is not set.',
    "banner.enabled": "Show the banner when the UI starts. `helm-torrent banner` always prints it.",
    "banner.modules": 'Banner lines in order. Built in: version, mode, indexers, jobs, downloads, services, separator. Custom: { type = "command", label = "...", command = ["..."] }.',
    "banner.timeout_ms": "Time limit for all banner lines together. Slow lines show a placeholder.",
}
FORMATS = {"toml": USER_FILES[0], "jsonc": USER_FILES[1]}
PROBE_TIMEOUT = 2.0


def add_parser(subparsers, parents):
    """Register `config` and its required nested actions."""
    config_parser = subparsers.add_parser("config", help="Create, edit, check and show settings", parents=parents)
    actions = config_parser.add_subparsers(dest="config_action", metavar="<action>")
    actions.required = True
    init_parser = actions.add_parser("init", help="Create a commented config file once", parents=parents)
    init_parser.add_argument("--format", choices=sorted(FORMATS), default="toml", help="File format (default: toml)")
    actions.add_parser("edit", help="Open the config file in $VISUAL or $EDITOR", parents=parents)
    actions.add_parser("doctor", help="Check settings, paths and service reachability", parents=parents)
    show_parser = actions.add_parser("show", help="Print every setting", parents=parents)
    show_parser.add_argument("--origin", action="store_true", help="Also show where each value came from")


def run(args):
    """Run one config action and return its exit code."""
    if args.config_action == "init":
        return init(args.format)
    if args.config_action == "show":
        return show(args.origin)
    return edit() if args.config_action == "edit" else doctor()


def _sample(field):
    """Return the value shown for a field in the template."""
    value = field.example if field.default is None else field.default
    return json.dumps(list(value) if isinstance(value, tuple) else value)


def _sections():
    sections: dict = {}
    for field in FIELDS:
        section, key = field.path.split(".", 1)
        sections.setdefault(section, []).append((key, field))
    return sections


def render_template(file_format):
    """Return a config file where every setting is commented out at its default."""
    header = [
        "Helm settings. Helm never rewrites this file, so edit it freely.",
        "Every value below is the default. Uncomment a line to change it.",
        "Environment variables override this file. Secrets belong in secrets.env.",
    ]
    if file_format == "toml":
        lines = ["# " + line for line in header] + [""]
        for section, items in _sections().items():
            lines.append(f"[{section}]")
            for key, field in items:
                lines.append("# " + DESCRIPTIONS[field.path])
                lines.append(f"# {key} = {_sample(field)}")
            lines.append("")
        return "\n".join(lines)
    lines = ["// " + line for line in header] + ["{"]
    for section, items in _sections().items():
        lines.append(f'  "{section}": {{')
        for key, field in items:
            lines.append("    // " + DESCRIPTIONS[field.path])
            lines.append(f'    // "{key}": {_sample(field)},')
        lines.append("  },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _existing_user_files():
    paths = [os.path.join(get_config_dir(), name) for name in USER_FILES]
    return [path for path in paths if os.path.exists(path)]


def init(file_format="toml"):
    migrate_legacy()
    existing = _existing_user_files()
    if existing:
        print(f"[error] {existing[0]} already exists. Use `helm-torrent config edit` to change it.", file=sys.stderr)
        return 1
    path = os.path.join(get_config_dir(), FORMATS[file_format])
    atomic_write(path, render_template(file_format))
    print(f"Created {path}")
    return 0


def _editor_command():
    for name in ("VISUAL", "EDITOR"):
        value = os.environ.get(name, "").strip()
        if value:
            return shlex.split(value)
    return None


def edit():
    command = _editor_command()
    if not command:
        print("[error] Set $VISUAL or $EDITOR to choose an editor.", file=sys.stderr)
        return 1
    path = active_config_file()
    if path is None:
        code = init("toml")
        if code:
            return code
        path = active_config_file()
    try:
        result = subprocess.run([*command, path])
    except OSError as error:
        print(f"[error] Cannot start {command[0]}: {error}", file=sys.stderr)
        return 1
    if result.returncode:
        print(f"[error] {command[0]} exited with status {result.returncode}", file=sys.stderr)
        return result.returncode
    current, problems = resolve_settings()
    report = Report()
    report.check_file(current, problems)
    report.print()
    return report.exit_code()


def redact_url(value):
    """Hide user information such as passwords in a URL."""
    parts = urlsplit(value)
    if "@" not in parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, "***@" + host, parts.path, parts.query, parts.fragment))


def _display(field, value):
    if field.kind == "url":
        value = redact_url(value)
    return json.dumps(value)


class Report:
    """Collect [ok], [warn] and [error] lines."""

    def __init__(self):
        self.lines = []

    def add(self, level, message):
        self.lines.append((level, message))

    def print(self):
        for level, message in self.lines:
            print(f"[{level}] {message}")

    def exit_code(self):
        return 1 if any(level == "error" for level, _message in self.lines) else 0

    def check_file(self, settings, problems):
        if settings.path:
            if problems:
                for problem in problems:
                    self.add("error", problem)
            else:
                self.add("ok", f"{settings.path} is valid")
        else:
            self.add("ok", "No config file; using defaults. Create one with `helm-torrent config init`.")
            for problem in problems:
                self.add("error", problem)
        for key in settings.unknown:
            self.add("warn", f"Unknown setting {key} is ignored")


def _check_shadowed(report):
    for path in shadowed_config_files():
        report.add("warn", f"{path} is ignored because {active_config_file()} exists. Files are never merged.")


def _check_legacy(report, settings):
    path = os.path.join(get_config_dir(), "config.json")
    if not os.path.isfile(path):
        return
    try:
        values = map_legacy(read_file(path))
    except ConfigError:
        report.add("warn", f"{path} cannot be parsed and is ignored")
        return
    if not values:
        return
    if settings.path is None:
        report.add("warn", f"{path} has settings that the next run migrates to config.toml")
        return
    differing = sorted(key for key, value in values.items() if settings.get(key) != value)
    if differing:
        report.add(
            "warn",
            f"{path} is ignored for settings; set {', '.join(differing)} in {os.path.basename(settings.path)}",
        )


def _check_secrets(report):
    path = get_secrets_file()
    if not os.path.isfile(path):
        report.add("ok", "No secrets.env yet")
        return
    if os.name != "posix":
        return
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        report.add("warn", f"{path} has mode {mode:04o}; run `chmod 600 {shlex.quote(path)}`")
    else:
        report.add("ok", f"{path} is private ({mode:04o})")


def _nearest_existing(path):
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _check_directory(report, label, path):
    existing = _nearest_existing(os.path.abspath(path))
    writable = os.path.isdir(existing) and os.access(existing, os.W_OK | os.X_OK)
    if not writable:
        report.add("error", f"{label} {path} is not writable")
    elif existing == os.path.abspath(path):
        report.add("ok", f"{label} {path} is writable")
    else:
        report.add("ok", f"{label} {path} will be created")


def probe(url, timeout=PROBE_TIMEOUT):
    """Return None when a server answers at url, or a short reason when not."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return None
    except urllib.error.HTTPError:
        # Any HTTP answer, such as 401 from a login page, proves reachability.
        return None
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        return str(reason) or type(error).__name__


def _check_services(report, settings):
    if settings.get("search.lite_only"):
        report.add("ok", "Service checks skipped because search.lite_only is set")
        return
    manager = settings.get("search.indexer_manager")
    names = [name for name in ("jackett", "prowlarr") if manager in (name, "both")]
    for name in [*names, "qbittorrent"]:
        url = settings.get(f"services.{name}")
        reason = probe(url)
        shown = redact_url(url)
        if reason is None:
            report.add("ok", f"{name} answers at {shown}")
        else:
            report.add("warn", f"{name} is not reachable at {shown}: {reason}")


def _check_logo(report, settings):
    """Warn about logo settings that would fall back to the built-in logo."""
    source = settings.get("logo.source")
    if source.startswith("builtin:"):
        from helm.ui.banner import LOGOS

        name = source.split(":", 1)[1]
        if name not in LOGOS:
            report.add("warn", f"logo.source names unknown logo {name!r}; built in: {', '.join(sorted(LOGOS))}")
    elif source == "file" and settings.get("logo.art") is None:
        path = settings.get("logo.art_file")
        if not path:
            report.add("warn", 'logo.source = "file" needs logo.art or logo.art_file')
        elif not os.path.isfile(os.path.expanduser(path)):
            report.add("warn", f"logo.art_file {path} does not exist")
    elif source.startswith("cmd:"):
        program = shlex.split(source[4:])[0]
        if shutil.which(program) is None:
            report.add("warn", f"logo command {program!r} is not installed; the built-in logo is used")


def doctor():
    settings, problems = resolve_settings()
    report = Report()
    report.check_file(settings, problems)
    _check_logo(report, settings)
    _check_shadowed(report)
    _check_legacy(report, settings)
    _check_secrets(report)
    _check_directory(report, "Download directory", get_dl_dir())
    _check_directory(report, "State directory", get_state_dir())
    _check_services(report, settings)
    report.print()
    return report.exit_code()


def show(with_origin=False):
    current = load_settings()
    print(f"# Config file: {current.path or 'none, using defaults'}")
    width = max(len(field.path) for field in FIELDS)
    for field in FIELDS:
        line = f"{field.path.ljust(width)} = {_display(field, current.get(field.path))}"
        if with_origin:
            line += f"  ({current.origin(field.path)})"
        print(line)
    return 0
