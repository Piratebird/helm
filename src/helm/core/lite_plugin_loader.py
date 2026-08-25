import importlib.util
import os
import sys

from helm.core.logger import get_logger

logger = get_logger(__name__)

# Ensure our plugins directory is in the path so helpers and novaprinter can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../plugins')))

import novaprinter


def load_plugins(plugin_dir):
    plugins = []
    if not os.path.exists(plugin_dir):
        return plugins

    # Also add user plugin dirs to sys.path so they can import helpers natively
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    for file in os.listdir(plugin_dir):
        # Ignore our SDK files and standard dunder files
        if file.endswith('.py') and not file.startswith('__') and file not in ('helpers.py', 'novaprinter.py'):
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
                    mod.print = lambda *args, **kwargs: get_logger(module_name).debug(" ".join(str(a) for a in args))

                    spec.loader.exec_module(mod)

                    # Search for the plugin class inside the module.
                    # Usually the class has a 'search' method and is not a built-in.
                    for attr_name in dir(mod):
                        if attr_name.startswith('__'):
                            continue
                        attr = getattr(mod, attr_name)
                        if isinstance(attr, type) and hasattr(attr, 'search'):
                            plugins.append(attr())
                            break
            except Exception:
                logger.debug(f"Event: Failed to load plugin {file}", exc_info=True)

    return plugins

def run_plugins(query, plugin_dirs):
    novaprinter.plugin_results.clear()

    plugins = []
    for d in plugin_dirs:
        plugins.extend(load_plugins(d))

    import concurrent.futures

    def execute_plugin(plugin):
        try:
            plugin.search(query)
        except Exception:
            logger.debug(f"Event: Error running plugin {getattr(plugin, 'name', plugin.__class__.__name__)}", exc_info=True)

    # Run all plugins concurrently with a timeout
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
    futures = [executor.submit(execute_plugin, p) for p in plugins]
    concurrent.futures.wait(futures, timeout=15)
    # Shut down without waiting for stalled threads to finish so the UI stays snappy
    executor.shutdown(wait=False)

    results = list(novaprinter.plugin_results)
    novaprinter.plugin_results.clear()

    return results
