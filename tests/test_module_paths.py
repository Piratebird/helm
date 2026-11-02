"""The package layout, and the old paths kept for Lite plugins."""

import importlib
import pkgutil
from pathlib import Path

import helm

PACKAGES = ("benchmarks", "cli", "config", "core", "net", "plugins", "search", "services", "stack", "torrents", "ui")


def test_old_plugin_paths_point_at_the_same_objects():
    from helm.core import http, logger, rss_fetcher
    from helm.logger import get_logger
    from helm.net.http import get_shared_session
    from helm.search.rss_fetcher import TorrentItem, source_status

    assert http.get_shared_session is get_shared_session
    assert logger.get_logger is get_logger
    assert rss_fetcher.TorrentItem is TorrentItem
    assert rss_fetcher.source_status is source_status


def test_core_keeps_only_the_plugin_modules():
    core = Path(helm.__file__).parent / "core"
    assert sorted(p.stem for p in core.glob("*.py")) == ["__init__", "http", "logger", "rss_fetcher"]


def test_every_package_imports():
    root = Path(helm.__file__).parent
    found = sorted(p.name for p in root.iterdir() if (p / "__init__.py").is_file())
    assert found == sorted(PACKAGES)
    for package in PACKAGES:
        if package == "plugins":
            continue  # plugins are loaded one by one in worker processes
        module = importlib.import_module(f"helm.{package}")
        for info in pkgutil.walk_packages(module.__path__, prefix=f"helm.{package}."):
            if not info.name.endswith(".__main__"):
                importlib.import_module(info.name)


def test_cli_has_one_module_per_command_group():
    import helm.cli.commands as commands

    names = sorted(info.name for info in pkgutil.iter_modules(commands.__path__))
    assert names == ["background", "banner", "headless", "indexers", "session", "status", "utility"]
