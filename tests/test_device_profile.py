import json
from types import SimpleNamespace

import pytest

import helm.core.device_profile as device
from helm.core.benchmark import binding_version
from helm.core.device_profile import battery_adb, battery_sysfs, cleanup_probe, energy_delta

DISCHARGING = {"status": 3, "Charge counter": 2000000, "voltage": 4000, "USB powered": False}


@pytest.mark.parametrize(
    "binding,expected",
    [
        (SimpleNamespace(__version__="2.0.11", version="old"), "2.0.11"),
        (SimpleNamespace(version="2.0.9"), "2.0.9"),
        (SimpleNamespace(), "unknown"),
    ],
)
def test_binding_version(binding, expected):
    assert binding_version(binding) == expected


def test_battery_sysfs_reads_first_battery(tmp_path):
    (tmp_path / "usb").mkdir()
    (tmp_path / "usb" / "type").write_text("USB\n")
    battery = tmp_path / "battery"
    battery.mkdir()
    (battery / "type").write_text("Battery\n")
    (battery / "voltage_now").write_text("4000000\n")
    (battery / "status").write_text("Discharging\n")
    assert battery_sysfs(tmp_path) == {"available": True, "voltage_now": 4000000, "status": "Discharging"}


def test_battery_sysfs_without_sensors(tmp_path):
    assert battery_sysfs(tmp_path)["available"] is False
    unreadable = tmp_path / "battery"
    unreadable.mkdir()
    (unreadable / "type").write_text("Battery")
    (unreadable / "current_now").write_text("not a number")
    assert battery_sysfs(tmp_path)["available"] is False


def test_battery_adb_parses_dumpsys():
    text = """Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  level: 80
  voltage: 4100
  temperature: 300
  Charge counter: 3000000
  technology: Li-ion
  status: 9
"""
    assert battery_adb(text) == {
        "AC powered": False,
        "USB powered": True,
        "Wireless powered": False,
        "status": 2,
        "level": 80,
        "voltage": 4100,
        "temperature": 300,
        "Charge counter": 3000000,
    }


def test_energy_is_calculated_only_while_verified_discharging():
    after = {**DISCHARGING, "Charge counter": 1999000, "voltage": 3900}
    result = energy_delta(DISCHARGING, after)
    assert result["consumption_wh"] == pytest.approx(0.00395)
    assert "not application-attributed" in result["scope"]


@pytest.mark.parametrize(
    "before,after,reason",
    [
        ({**DISCHARGING, "USB powered": True}, DISCHARGING, "charging"),
        (DISCHARGING, {**DISCHARGING, "status": 5}, "charging"),
        ({**DISCHARGING, "status": 4}, DISCHARGING, "not verified"),
        (DISCHARGING, {**DISCHARGING, "Charge counter": 2000001}, "increased"),
        ({"status": 3}, {"status": 3}, "unavailable"),
    ],
)
def test_unmeasurable_energy_is_never_reported_as_zero(before, after, reason):
    result = energy_delta(before, after)
    assert result["consumption_wh"] is None
    assert reason in result["reason"]


def test_cleanup_probe_kills_hung_source_and_keeps_results():
    result = cleanup_probe()
    assert result == {
        "passed": True,
        "outcomes": {"stalled": "timeout", "healthy": "success"},
        "retained_results": 1,
        "stalled_process_running_after_return": False,
    }


def test_main_writes_report_and_restores_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(device, "run_trial", lambda *a, **kw: {"search_workers": kw["search_workers"]})
    monkeypatch.setattr(device, "cleanup_probe", lambda: {"passed": True})
    monkeypatch.setenv("HELM_STATE_DIR", "/kept")
    output = tmp_path / "out" / "profile.json"
    assert device.main(["--output", str(output), "--repeats", "2", "--workers", "2", "6"]) == 0
    report = json.loads(output.read_text())
    assert [(p["workers"], len(p["trials"])) for p in report["profiles"]] == [(2, 2), (6, 2)]
    assert report["energy"]["consumption_wh"] is None
    assert device.os.environ["HELM_STATE_DIR"] == "/kept"


def test_failed_cleanup_probe_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(device, "run_trial", lambda *a, **kw: {})
    monkeypatch.setattr(device, "cleanup_probe", lambda: {"passed": False})
    assert device.main(["--output", str(tmp_path / "p.json"), "--repeats", "1", "--workers", "1"]) == 1


@pytest.mark.parametrize("arguments", [["--repeats", "0"], ["--workers", "13"]])
def test_invalid_arguments(tmp_path, arguments):
    with pytest.raises(SystemExit):
        device.main(["--output", str(tmp_path / "p.json"), *arguments])
