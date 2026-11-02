"""Banner data: the logo and module lines, with no colour or layout.

Every part runs in its own daemon thread under one shared deadline. A part that
fails shows "unavailable" and one that is too slow shows "timed out". Neither can
break the caller or delay it past banner.timeout_ms, and a stuck part cannot
block interpreter exit either.
"""

import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from helm.core.config_manager import get_config_dir, get_dl_dir, get_state_dir
from helm.ui.colors import logo as wordmark

# The current UI's wordmark, shared so the default look cannot drift.
LOGOS = {"helm": wordmark.strip("\n").split("\n")}
PLACEHOLDER = re.compile(r"\{(logo|accent|text|line|error|muted|reset)\}")
SGR = re.compile(r"\x1b\[[0-9;]*m")
# Any other escape sequence or control character from a command is dropped.
UNSAFE = re.compile(r"\x1b(\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)|.)|[\x00-\x08\x0b-\x1f\x7f]")
MAX_ART_BYTES = 64 * 1024
MAX_ART_LINES = 60
MAX_VALUE = 200
TIMED_OUT = "timed out"
UNAVAILABLE = "unavailable"

# A logo line is a list of (colour name, text). Colour None marks raw command
# output, whose own SGR colours the renderer keeps or strips.
Segment = Tuple[Optional[str], str]


@dataclass
class Entry:
    label: str
    value: str = ""
    status: str = "ok"  # ok, timeout, error, separator


@dataclass
class Banner:
    logo: List[List[Segment]] = field(default_factory=list)
    entries: List[Entry] = field(default_factory=list)
    position: str = "left"


def parse_art(text):
    """Split art into coloured segments. Unknown braces stay as written."""
    lines = []
    color = "logo"
    for raw in text.split("\n")[:MAX_ART_LINES]:
        segments: List[Segment] = []
        position = 0
        for match in PLACEHOLDER.finditer(raw):
            if match.start() > position:
                segments.append((color, raw[position : match.start()]))
            name = match.group(1)
            color = "text" if name == "reset" else name
            position = match.end()
        if position < len(raw):
            segments.append((color, raw[position:]))
        lines.append(segments)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def clean_output(text):
    """Keep SGR colours from a command's output and drop every other control code."""
    kept = []
    for line in text.replace("\r\n", "\n").split("\n")[:MAX_ART_LINES]:
        parts = SGR.split(line)
        codes = SGR.findall(line)
        safe = UNSAFE.sub("", parts[0])
        for code, part in zip(codes, parts[1:]):
            safe += code + UNSAFE.sub("", part)
        kept.append(safe.replace("\t", "    "))
    while kept and not SGR.sub("", kept[-1]).strip():
        kept.pop()
    return kept


def _builtin(name="helm"):
    return [[("logo", line)] for line in LOGOS[name]]


def _command_output(command, timeout):
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=True,
    )
    return result.stdout[:MAX_ART_BYTES].decode("utf-8", errors="replace")


def _read_art_file(path):
    with open(os.path.expanduser(path), "rb") as stream:
        return stream.read(MAX_ART_BYTES).decode("utf-8", errors="replace")


def resolve_logo(settings, timeout):
    """Return logo lines. Any failure falls back to the built-in Helm logo."""
    source = settings.get("logo.source")
    if source == "none":
        return []
    if source.startswith("builtin:"):
        name = source.split(":", 1)[1]
        return _builtin(name if name in LOGOS else "helm")
    if source == "file":
        art = settings.get("logo.art")
        path = settings.get("logo.art_file")
        if art is None and path:
            art = _read_art_file(path)
        if not art or not art.strip():
            return _builtin()
        return parse_art(art)
    lines = clean_output(_command_output(shlex.split(source[4:]), timeout))
    return [[(None, line)] for line in lines] or _builtin()


def _version():
    try:
        from importlib.metadata import version
    except ImportError:  # pragma: no cover - Python 3.8 has importlib.metadata
        return "unknown"
    try:
        return version("helm-torrent")
    except Exception:
        return "unknown"


def _mode(settings, context):
    if context.get("mode"):
        return context["mode"]
    if settings.get("search.lite_only"):
        return "lite"
    return settings.get("search.indexer_manager")


def _lite_sources():
    from helm.core.lite_plugin_loader import plugin_jobs

    bundled = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
    return len(plugin_jobs([os.path.join(get_config_dir(), "plugins"), bundled]))


def _manager_count(name, settings, timeout):
    import xml.etree.ElementTree as ET

    import requests

    from helm.core.secret_manager import get_secret

    url = settings.get(f"services.{name}")
    key = get_secret(f"{name.upper()}_API_KEY")
    if not key:
        return f"{name} has no API key"
    if name == "jackett":
        response = requests.get(
            f"{url}/api/v2.0/indexers/all/results/torznab/api",
            params={"apikey": key, "t": "indexers", "configured": "true"},
            timeout=timeout,
        )
        response.raise_for_status()
        count = len(ET.fromstring(response.content).findall("indexer"))
    else:
        response = requests.get(f"{url}/api/v1/indexer", headers={"X-Api-Key": key}, timeout=timeout)
        response.raise_for_status()
        count = sum(1 for indexer in response.json() if indexer.get("enable"))
    return f"{count} in {name}"


def _indexers(settings, timeout):
    if settings.get("search.lite_only"):
        return f"{_lite_sources()} Lite sources"
    manager = settings.get("search.indexer_manager")
    names = [name for name in ("jackett", "prowlarr") if manager in (name, "both")]
    return ", ".join(_manager_count(name, settings, timeout) for name in names)


def _jobs():
    path = os.path.join(get_state_dir(), "jobs.sqlite3")
    if not os.path.isfile(path):
        return "none yet"
    # Read only, so the banner never creates or migrates the queue.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
    try:
        rows = dict(connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    finally:
        connection.close()
    active = [
        f"{rows[status]} {label}"
        for status, label in (("running", "running"), ("submitted", "in qBittorrent"), ("pending", "queued"))
        if rows.get(status)
    ]
    return ", ".join(active) if active else "idle"


def _downloads():
    path = get_dl_dir()
    existing = path
    while not os.path.exists(existing) and os.path.dirname(existing) != existing:
        existing = os.path.dirname(existing)
    free = shutil.disk_usage(existing).free / 1024**3
    home = os.path.expanduser("~")
    shown = "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path
    return f"{shown} ({free:.1f} GiB free)"


def _services(settings, timeout):
    from helm.core.config_commands import probe

    if settings.get("search.lite_only"):
        return "not used in Lite mode"
    manager = settings.get("search.indexer_manager")
    names = [name for name in ("jackett", "prowlarr") if manager in (name, "both")] + ["qbittorrent"]
    states = []
    for name in names:
        reachable = probe(settings.get(f"services.{name}"), timeout=timeout) is None
        states.append(f"{name} {'up' if reachable else 'down'}")
    return ", ".join(states)


def _custom(command, timeout):
    lines = clean_output(_command_output(command, timeout))
    first = next((SGR.sub("", line).strip() for line in lines if SGR.sub("", line).strip()), "")
    return first[:MAX_VALUE]


LABELS = {
    "version": "Version",
    "mode": "Mode",
    "indexers": "Indexers",
    "jobs": "Jobs",
    "downloads": "Downloads",
    "services": "Services",
}


def _task(module, settings, context, timeout):
    """Return (label, function) for one module setting."""
    if isinstance(module, dict):
        return module["label"], lambda: _custom(module["command"], timeout)
    functions = {
        "version": _version,
        "mode": lambda: _mode(settings, context),
        "indexers": lambda: _indexers(settings, timeout),
        "jobs": _jobs,
        "downloads": _downloads,
        "services": lambda: _services(settings, timeout),
    }
    return LABELS[module], functions[module]


def run_with_deadline(functions, timeout):
    """Run functions in daemon threads and return {index: (status, value)} by the deadline."""
    results: dict = {}

    def work(index, function):
        try:
            results[index] = ("ok", function())
        except Exception:
            results[index] = ("error", None)

    threads = []
    for index, function in enumerate(functions):
        thread = threading.Thread(target=work, args=(index, function), name=f"helm-banner-{index}", daemon=True)
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return {index: results.get(index, ("timeout", None)) for index in range(len(functions))}


def build_banner(settings, context=None):
    """Collect banner data within banner.timeout_ms. Never raises for a failing part."""
    context = context or {}
    timeout = settings.get("banner.timeout_ms") / 1000
    modules = settings.get("banner.modules")
    tasks = [_task(module, settings, context, timeout) for module in modules if module != "separator"]
    functions = [lambda: resolve_logo(settings, timeout)] + [function for _label, function in tasks]
    results = run_with_deadline(functions, timeout)

    status, logo = results[0]
    if status != "ok":
        logo = _builtin()
    entries = []
    index = 1
    for module in modules:
        if module == "separator":
            entries.append(Entry("", status="separator"))
            continue
        label = tasks[index - 1][0]
        status, value = results[index]
        index += 1
        if status == "ok":
            text = str(value)[:MAX_VALUE] if value is not None and str(value) else "none"
            entries.append(Entry(label, text))
        else:
            entries.append(Entry(label, TIMED_OUT if status == "timeout" else UNAVAILABLE, status))
    return Banner(logo, entries, settings.get("logo.position"))
