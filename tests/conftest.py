import os
import tempfile

# Isolate the whole session from the real XDG config/state dirs so tests never
# touch ~/.config/helm or ~/.local/state/helm. Set before test modules are
# imported. Logging is now configured explicitly after CLI path overrides.
_TMP_BASE = tempfile.mkdtemp(prefix="helm-tests-")
os.environ["HELM_CONFIG_DIR"] = os.path.join(_TMP_BASE, "config")
os.environ["HELM_STATE_DIR"] = os.path.join(_TMP_BASE, "state")
os.environ["HELM_DL_DIR"] = os.path.join(_TMP_BASE, "downloads")


def pytest_runtest_setup(item):
    # The secret manager caches the contents of secrets.env; make sure a cached
    # value from a previous test's temp dir never leaks into the next test.
    import helm.core.secret_manager as sm

    sm._invalidate_cache()
    import sys

    printer = sys.modules.get("novaprinter")
    if printer is not None:
        printer.get_results().clear()
        printer.get_errors().clear()


def pytest_runtest_teardown(item):
    import logging

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_helm_handler", False):
            root.removeHandler(handler)
            handler.close()
