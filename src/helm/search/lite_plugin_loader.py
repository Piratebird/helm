import importlib.util
import os
import sys

from helm.logger import get_logger
from helm.search.rss_fetcher import SearchResults, SourceOutcome, source_status

logger = get_logger(__name__)
PLUGIN_SEARCH_TIMEOUT = 30

# Ensure our plugins directory is in the path so helpers and novaprinter can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../plugins")))

import novaprinter  # noqa: E402


def plugin_jobs(plugin_dirs):
    from helm.search.search_runner import SearchJob

    jobs = []
    for directory in plugin_dirs:
        if not os.path.isdir(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if (
                filename.endswith(".py")
                and not filename.startswith("__")
                and filename not in ("helpers.py", "novaprinter.py", "piratebay.py")
            ):
                jobs.append(SearchJob(filename[:-3], "plugin", os.path.join(directory, filename)))
    return jobs


def load_plugin(filepath):
    directory = os.path.dirname(filepath)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    module_name = os.path.basename(filepath)[:-3]
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load plugin")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    mod.print = lambda *args, **kwargs: get_logger(module_name).debug(" ".join(str(a) for a in args))
    spec.loader.exec_module(mod)
    for attr in vars(mod).values():
        if isinstance(attr, type) and attr.__module__ == module_name and hasattr(attr, "search"):
            return attr()
    raise ValueError("Plugin has no search class")


def load_plugins(plugin_dir):
    plugins = []
    for job in plugin_jobs([plugin_dir]):
        try:
            plugins.append(load_plugin(job.target))
        except Exception:
            logger.debug("Failed to load %s", job.source, exc_info=True)
    return plugins


def plugin_categories(plugin, content_type):
    supported = getattr(plugin, "supported_categories", {"all": "all"})
    aliases = {
        "video": ("movies", "tv", "anime"),
        "books": ("books",),
        "games": ("games",),
        "software": ("software",),
        "music": ("music",),
    }
    selected = []
    for category in content_type.split(","):
        candidates = aliases.get(category, (category,))
        matching = [key for key in candidates if key in supported]
        if not matching:
            matching = ["all"] if "all" in supported else []
        for key in matching:
            if key not in selected:
                selected.append(key)
    # all already includes narrower categories; avoid duplicate requests.
    return ["all"] if "all" in selected else selected


def _execute_plugin(plugin, query, content_type="all"):
    name = getattr(plugin, "name", plugin.__class__.__name__)
    collector = novaprinter.get_results()
    collector.clear()
    novaprinter.get_errors().clear()
    before = len(collector)
    status = None
    try:
        for category in plugin_categories(plugin, content_type):
            if category == "all":
                plugin.search(query)
            else:
                plugin.search(query, category)
    except Exception as error:
        status = source_status(error)
        logger.debug(f"Event: Error running plugin {name}", exc_info=True)
    errors = novaprinter.get_errors()
    if errors and status is None:
        status = errors[0]
    after = len(collector)
    for item in collector:
        item.indexer = name
    logger.info(f"Event: Plugin {name} returned {after - before} results")
    # Pool workers can run several plugins. Return a snapshot before the next
    # plugin clears that worker's thread-local collector.
    return SearchResults(collector, [SourceOutcome(name, status or ("success" if collector else "empty"), after)])


def run_plugins(query, plugin_dirs, content_type="all"):
    from helm.search.search_runner import run_searches

    return run_searches(plugin_jobs(plugin_dirs), query, content_type, timeout=PLUGIN_SEARCH_TIMEOUT)
