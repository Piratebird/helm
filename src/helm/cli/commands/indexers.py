"""helm-torrent indexers: add and remove Jackett or Prowlarr indexers."""

import sys
import time

from helm.cli import deps
from helm.ui.ansi import T


def manage_indexers(args):
    if args.lite:
        print(f"{T.ERROR}Cannot manage Indexers in Lite Mode.{T.RST}", file=sys.stderr)
        raise SystemExit(1) from None
    deps.ensure_config()

    use_prowlarr = getattr(args, "prowlarr", False)
    use_jackett = getattr(args, "jackett", False)

    if not use_prowlarr and not use_jackett:
        manager_type = deps.load_settings().get("search.indexer_manager")
    elif use_prowlarr:
        manager_type = "prowlarr"
    else:
        manager_type = "jackett"

    try:
        if manager_type == "prowlarr":
            from helm.search.indexer_manager import ProwlarrManager

            manager = ProwlarrManager()
            mgr_name = "Prowlarr"
        else:
            from helm.search.indexer_manager import JackettManager

            manager = JackettManager()
            mgr_name = "Jackett"
    except Exception as e:
        print(f"{T.ERROR}Failed to initialize Manager: {e}{T.RST}", file=sys.stderr)
        raise SystemExit(1) from None

    sys.stdout.write(f"{T.LOGO}Fetching indexers from {mgr_name}...{T.RST}\r\n")
    sys.stdout.flush()
    all_indexers = manager.get_all_indexers()

    while True:
        try:
            selected_indexer = deps.interactive_indexer_selector(all_indexers)
            if selected_indexer:
                if selected_indexer["configured"]:
                    sys.stdout.write(f"\r\n{T.TEXT}Removing {selected_indexer['title']}...{T.RST}\r\n")
                    sys.stdout.flush()
                    try:
                        manager.remove_indexer(selected_indexer["id"])
                        selected_indexer["configured"] = False
                    except Exception as e:
                        sys.stdout.write(f"\r\n{T.ERROR}Failed to remove: {e}{T.RST}\r\n")
                        sys.stdout.flush()
                        time.sleep(2)
                else:
                    if selected_indexer.get("type", "public") != "public":
                        sys.stdout.write(
                            f"\r\n{T.ERROR}Cannot add {selected_indexer['type']} trackers via CLI as they require credentials. Please use the Jackett Web UI ({manager.url}).{T.RST}\r\n"
                        )
                        sys.stdout.flush()
                        time.sleep(3)
                    else:
                        sys.stdout.write(f"\r\n{T.TEXT}Adding {selected_indexer['title']}...{T.RST}\r\n")
                        sys.stdout.flush()
                        try:
                            manager.add_indexer(selected_indexer["id"])
                            selected_indexer["configured"] = True
                        except Exception as e:
                            error_msg = str(e)
                            if "500" in error_msg:
                                error_msg = "Jackett rejected the default config. Please configure this indexer manually via the Jackett Web UI."
                            sys.stdout.write(f"\r\n{T.ERROR}Failed to add: {error_msg}{T.RST}\r\n")
                            sys.stdout.flush()
                            time.sleep(3)
        except KeyboardInterrupt:
            sys.stdout.write(f"\n{T.ACCENT}Exiting indexer management.{T.RST}\n")
            sys.stdout.flush()
            try:
                choice = input(f"{T.BOLD}{T.TEXT}? Would you like to proceed to search? [Y/n]:{T.RST} ").strip().lower()
                if choice == "n":
                    raise SystemExit(0) from None
            except (KeyboardInterrupt, EOFError):
                print(f"\n{T.ACCENT}later bozo!{T.RST}")
                raise SystemExit(0) from None
