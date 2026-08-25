import importlib.util
import sys

spec = importlib.util.spec_from_file_location("test_plugin", "tests/test_plugin.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["test_plugin"] = mod
mod.print = lambda *args, **kwargs: sys.stdout.write("INTERCEPTED: " + " ".join(str(a) for a in args) + "\n")
spec.loader.exec_module(mod)
mod.search()
