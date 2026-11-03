"""helm-torrent paths, logs and bug-report."""

import json
import os
import sys

from helm.logger import tail_log
from helm.ui.ansi import T


def run(command):
    if command == "paths":
        from helm.config.config_manager import get_config_dir, get_dl_dir, get_log_dir

        print(f"{T.BOLD}{T.INFO}Helm Data Locations:{T.RST}")
        print(f"  Configuration: {get_config_dir()}")
        print(f"  State/Logs:    {get_log_dir()}")
        print(f"  Downloads:     {get_dl_dir()}")
        return

    if command == "logs":
        from helm.config.config_manager import get_log_dir

        log_file = os.path.join(get_log_dir(), "helm.log")
        if os.path.exists(log_file):
            print("--- Last 20 lines ---")
            try:
                sys.stdout.write(tail_log(log_file))
                print("\n---------------------")
            except Exception as e:
                print(f"Could not read log file: {e}")
        else:
            print("Log file does not exist yet.")
        return

    if command == "bug-report":
        import zipfile

        from helm.config.config_manager import get_log_dir
        from helm.config.settings import read_file, report_config_file

        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.exists(desktop):
            desktop = os.path.expanduser("~")
        zip_path = os.path.join(desktop, "helm-bug-report.zip")
        config_file = report_config_file()
        log_file = os.path.join(get_log_dir(), "helm.log")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(config_file):
                from helm.config.secret_manager import redact_config

                try:
                    zipf.writestr("config.json", json.dumps(redact_config(read_file(config_file)), indent=4))
                except Exception:
                    print("Could not redact the config; omitting it from the bug report.", file=sys.stderr)
            if os.path.exists(log_file):
                from helm.config.secret_manager import redact_text

                with open(log_file, "r", encoding="utf-8", errors="replace") as source, zipf.open(
                    "helm.log", "w"
                ) as target:
                    for line in source:
                        target.write(redact_text(line).encode("utf-8"))
        print(f"Bug report successfully created at: {zip_path}")
        print("Please attach this zip file when creating an issue on GitHub.")
        return
