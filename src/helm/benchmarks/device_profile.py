"""On-device PSS, CPU and cancellation measurements; honest battery accounting."""

import argparse
import json
import os
import platform
import tempfile
from pathlib import Path

from helm.benchmarks.benchmark import run_trial
from helm.search.search_runner import SearchJob, run_searches
from helm.services.runtime import process_alive


def battery_sysfs(root=Path("/sys/class/power_supply")):
    for directory in root.glob("*"):
        try:
            if (directory / "type").read_text().strip().lower() != "battery":
                continue
            sample = {"available": True}
            for filename in ("energy_now", "charge_counter", "voltage_now", "current_now", "status"):
                path = directory / filename
                if path.is_file():
                    value = path.read_text().strip()
                    sample[filename] = value if filename == "status" else int(value)
            return sample
        except (OSError, ValueError):
            continue
    return {"available": False, "reason": "No readable battery sysfs sensors"}


def battery_adb(text):
    values = {}
    for line in text.splitlines():
        key, separator, value = line.strip().partition(":")
        if not separator or key in values:
            continue
        if key in ("Charge counter", "voltage", "status", "level", "temperature"):
            try:
                values[key] = int(value.strip())
            except ValueError:
                pass
        elif key in ("USB powered", "AC powered", "Wireless powered"):
            values[key] = value.strip().lower() == "true"
    return values


def energy_delta(before, after):
    # Android charge counters are microampere-hours and dumpsys voltage is mV.
    # Charging measurements are explicitly inconclusive, not app energy usage.
    charging = any(before.get(key) or after.get(key) for key in ("USB powered", "AC powered", "Wireless powered"))
    if charging or before.get("status") in (2, 5) or after.get("status") in (2, 5):
        return {"consumption_wh": None, "reason": "Device charging; net battery change cannot measure workload energy"}
    if before.get("status") != 3 or after.get("status") != 3:
        return {"consumption_wh": None, "reason": "Discharging state was not verified"}
    if all(key in before and key in after for key in ("Charge counter", "voltage")):
        charge = before["Charge counter"] - after["Charge counter"]
        voltage = (before["voltage"] + after["voltage"]) / 2
        if charge < 0:
            return {"consumption_wh": None, "reason": "Charge counter increased"}
        return {
            "consumption_wh": charge * voltage / 1e9,
            "scope": "Whole-device net battery estimate; not application-attributed",
        }
    return {"consumption_wh": None, "reason": "Charge counter or voltage unavailable"}


def cleanup_probe():
    with tempfile.TemporaryDirectory(prefix="helm-cleanup-probe-") as temporary:
        directory = Path(temporary)
        pidfile = directory / "stalled.pid"
        stalled = directory / "stalled.py"
        stalled.write_text(
            f"import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n"
        )
        healthy = directory / "healthy.py"
        healthy.write_text(
            "from novaprinter import prettyPrinter\nclass healthy:\n    name='healthy'\n    def search(self, query, cat='all'):\n        prettyPrinter({'name':'generated','link':'magnet:?xt=urn:btih:'+'a'*40})\n"
        )
        result = run_searches(
            [SearchJob("stalled", "plugin", str(stalled)), SearchJob("healthy", "plugin", str(healthy))],
            "generated",
            timeout=3,
            max_workers=2,
        )
        pid = int(pidfile.read_text()) if pidfile.exists() else None
        outcome = {item.source: item.status for item in result.outcomes}
        alive = process_alive(pid) if pid else None
        passed = outcome == {"stalled": "timeout", "healthy": "success"} and alive is False
        return {
            "passed": passed,
            "outcomes": outcome,
            "retained_results": len(result),
            "stalled_process_running_after_return": alive,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, nargs="+", default=[2, 6])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    if args.repeats < 1 or any(not 1 <= value <= 12 for value in args.workers):
        parser.error("positive repeats and worker counts 1–12 required")
    report = {
        "schema_version": 1,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "battery_before": battery_sysfs(),
        "profiles": [],
    }
    original = {key: os.environ.get(key) for key in ("HELM_CONFIG_DIR", "HELM_STATE_DIR")}
    try:
        with tempfile.TemporaryDirectory(prefix="helm-device-profile-") as temporary:
            os.environ["HELM_CONFIG_DIR"] = str(Path(temporary) / "config")
            os.environ["HELM_STATE_DIR"] = str(Path(temporary) / "state")
            for workers in args.workers:
                trials = [
                    run_trial(0.1, 4 * 1024**2, 30, fixture_plugins=12, fixture_results=100, search_workers=workers)
                    for _ in range(args.repeats)
                ]
                report["profiles"].append({"workers": workers, "trials": trials})
            report["cleanup"] = cleanup_probe()
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    report["battery_after"] = battery_sysfs()
    report["energy"] = {
        "consumption_wh": None,
        "reason": "Use external ADB battery samples if Android denies app sensor access",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "cleanup": report["cleanup"]}), flush=True)
    return 0 if report["cleanup"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
