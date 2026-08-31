import concurrent.futures
import importlib.util
import os
import sys

from helm.core.logger import get_logger

logger = get_logger(__name__)

# Ensure our plugins directory is in the path so helpers and novaprinter can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../plugins")))

import novaprinter  # noqa: E402


def load_plugins(plugin_dir):
    plugins = []
    if not os.path.exists(plugin_dir):
        return plugins

    # Also add user plugin dirs to sys.path so they can import helpers natively
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    for file in os.listdir(plugin_dir):
        # Ignore our SDK files and standard dunder files
        if file.endswith(".py") and not file.startswith("__") and file not in ("helpers.py", "novaprinter.py"):
            filepath = os.path.join(plugin_dir, file)
            module_name = file[:-3]
            try:
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = mod

                    # Intercept standard logging by configuring the root logger
                    get_logger("")

                    # Intercept third-party prints by injecting a custom print into the module's globals
                    mod.print = lambda *args, **kwargs: get_logger(module_name).debug(" ".join(str(a) for a in args))  # noqa: B023

                    spec.loader.exec_module(mod)

                    # Search for the plugin class inside the module.
                    # Usually the class has a 'search' method and is not a built-in.
                    for attr_name in dir(mod):
                        if attr_name.startswith("__"):
                            continue
                        attr = getattr(mod, attr_name)
                        if isinstance(attr, type) and hasattr(attr, "search"):
                            plugins.append(attr())
                            break
            except Exception:
                logger.debug(f"Event: Failed to load plugin {file}", exc_info=True)

    return plugins


def _execute_plugin(plugin, query):
    name = getattr(plugin, "name", plugin.__class__.__name__)
    collector = novaprinter.get_results()
    collector.clear()  # pooled worker threads keep their thread-local between runs
    before = len(collector)
    try:
        plugin.search(query)
    except Exception:
        logger.debug(f"Event: Error running plugin {name}", exc_info=True)
    after = len(collector)
    for item in collector:
        item.indexer = name
    logger.info(f"Event: Plugin {name} returned {after - before} results")
    return collector


def run_plugins(query, plugin_dirs):
    plugins = []
    for d in plugin_dirs:
        plugins.extend(load_plugins(d))

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    futures = [executor.submit(_execute_plugin, p, query) for p in plugins]
    # Give slow-but-alive plugins up to 30s; each plugin's own HTTP requests
    # already time out at ~15s so a hung socket is the only straggler.
    done, _ = concurrent.futures.wait(futures, timeout=30)
    # Abandon stragglers so the UI stays snappy. Their future writes land in a
    # private thread-local collector (never the return value), so they cannot
    # contaminate this or any later search.
    executor.shutdown(wait=False)

    results = []
    for future in done:
        try:
            results.extend(future.result())
        except Exception:
            logger.debug("Event: Failed to collect plugin results", exc_info=True)

    return results
