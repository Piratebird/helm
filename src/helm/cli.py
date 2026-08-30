import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

from helm.core.secret_manager import get_secret, get_secrets_file  # noqa: E402

load_dotenv(get_secrets_file())

from helm.core.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, load_config  # noqa: E402
from helm.core.config_wizard import ensure_config  # noqa: E402
from helm.core.logger import get_logger  # noqa: E402
from helm.core.oneshot import wait_for_download  # noqa: E402
from helm.core.qbittorrent_client import add_magnet  # noqa: E402
from helm.core.torrent_filter import dedupe, filter_items  # noqa: E402
from helm.ui.colors import C_ERR, C_LOGO, C_RST, C_SUB, C_TEXT, logo  # noqa: E402
from helm.ui.tui import animated_search, interactive_indexer_selector, interactive_selector  # noqa: E402


def _send_magnet(selected, lite_mode, json_mode=False, success_msg="Torrent sent successfully :)"):
    """Send a torrent to qBittorrent, falling back to the built-in downloader if it is unavailable."""
    if lite_mode:
        from helm.core.lite_downloader import download_magnet

        download_magnet(selected.link)
        return

    try:
        add_magnet(selected.link)
        if not json_mode:
            print(f"{C_TEXT}{success_msg}{C_RST}")
    except Exception as e:
        print(f"\n\033[33m[!] qBittorrent not available ({e}). Falling back to LITE MODE downloader...\033[0m\n")
        from helm.core.lite_downloader import download_magnet

        download_magnet(selected.link)


def main():
    base_parser = argparse.ArgumentParser(add_help=False)
    global_opts = base_parser.add_argument_group("Global Options")
    global_opts.add_argument(
        "--config-dir", type=str, default=argparse.SUPPRESS, help="Override the configuration directory"
    )
    global_opts.add_argument("--state-dir", type=str, default=argparse.SUPPRESS, help="Override the state directory")
    global_opts.add_argument("--dl-dir", type=str, default=argparse.SUPPRESS, help="Override the downloads directory")
    global_opts.add_argument(
        "-o",
        "--oneshot",
        action="store_true",
        default=argparse.SUPPRESS,
        help="One-shot mode: start stack, download, tear down",
    )
    global_opts.add_argument(
        "-l",
        "--lite",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Lite mode: bypass Jackett and use public trackers",
    )
    global_opts.add_argument(
        "-j", "--json", action="store_true", default=argparse.SUPPRESS, help="Output results as JSON"
    )

    parser = argparse.ArgumentParser(
        description="Helm - Torrent automation MVP",
        formatter_class=argparse.RawTextHelpFormatter,
        parents=[base_parser],
    )

    subparsers = parser.add_subparsers(dest="command", title="Commands", metavar="<command>")

    # UI
    # Init
    subparsers.add_parser(
        "init", help="Bootstrap and initialize the container environment natively", parents=[base_parser]
    )

    # UI
    subparsers.add_parser(
        "ui", help="Launch the interactive terminal UI (Default if no command given)", parents=[base_parser]
    )

    # Search
    search_parser = subparsers.add_parser(
        "search", help="Search and download via CLI flags without TUI", parents=[base_parser]
    )
    search_parser.add_argument("query", type=str, nargs="+", help="Search query")
    search_parser.add_argument("-t", "--type", type=str, default="video", help="Content type (video, games, etc.)")
    search_parser.add_argument(
        "-a", "--auto", action="store_true", help="Automatically select and send the top torrent"
    )

    # Indexers
    subparsers.add_parser("indexers", help="Manage Jackett indexers interactively", parents=[base_parser])

    # Utility Commands
    subparsers.add_parser("logs", help="Tail the last 20 lines of the application log", parents=[base_parser])
    subparsers.add_parser(
        "paths", help="Print all system data locations (Config, State, Downloads)", parents=[base_parser]
    )
    subparsers.add_parser(
        "bug-report", help="Zip the config and logs to the Desktop for reporting", parents=[base_parser]
    )

    # Parse arguments
    args = parser.parse_args()

    if not args.command:
        args.command = "ui"

    if not hasattr(args, "lite"):
        args.lite = False
    if not hasattr(args, "json"):
        args.json = False
    if not hasattr(args, "oneshot"):
        args.oneshot = False

    if args.command == "search":
        args.query = " ".join(args.query)
    else:
        args.query = None
        args.type = "video"
        args.auto = False

    if args.command not in ("ui", "search", "indexers"):
        args.oneshot = False

    if getattr(args, "config_dir", None):
        os.environ["HELM_CONFIG_DIR"] = os.path.abspath(args.config_dir)
    if getattr(args, "state_dir", None):
        os.environ["HELM_STATE_DIR"] = os.path.abspath(args.state_dir)
    if getattr(args, "dl_dir", None):
        os.environ["HELM_DL_DIR"] = os.path.abspath(args.dl_dir)

    get_logger("")  # Initialize root logger after directory overrides

    if args.command == "init":
        from helm.core.init_env import bootstrap_env

        bootstrap_env()
        sys.exit(0)

    if args.command == "paths":
        from helm.core.config_manager import get_config_dir, get_dl_dir, get_log_dir

        print("\033[1m\033[36mHelm Data Locations:\033[0m")
        print(f"  Configuration: {get_config_dir()}")
        print(f"  State/Logs:    {get_log_dir()}")
        print(f"  Downloads:     {get_dl_dir()}")
        sys.exit(0)

    if args.command == "logs":
        from helm.core.config_manager import get_log_dir

        log_file = os.path.join(get_log_dir(), "helm.log")
        if os.path.exists(log_file):
            print("--- Last 20 lines ---")
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines[-20:]:
                        sys.stdout.write(line)
                    print("\n---------------------")
            except Exception as e:
                print(f"Could not read log file: {e}")
        else:
            print("Log file does not exist yet.")
        sys.exit(0)

    if args.command == "bug-report":
        import zipfile

        from helm.core.config_manager import get_config_file, get_log_dir

        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.exists(desktop):
            desktop = os.path.expanduser("~")
        zip_path = os.path.join(desktop, "helm-bug-report.zip")
        config_file = get_config_file()
        log_file = os.path.join(get_log_dir(), "helm.log")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(config_file):
                from helm.core.secret_manager import redact_config

                try:
                    with open(config_file, "r") as f:
                        cfg_data = json.load(f)
                    zipf.writestr("config.json", json.dumps(redact_config(cfg_data), indent=4))
                except Exception:
                    zipf.write(config_file, arcname="config.json")
            if os.path.exists(log_file):
                zipf.write(log_file, arcname="helm.log")
        print(f"Bug report successfully created at: {zip_path}")
        print("Please attach this zip file when creating an issue on GitHub.")
        sys.exit(0)

    if not args.lite:
        config = load_config()
        if config.get("LITE_MODE_ONLY"):
            args.lite = True
        elif not os.getenv("JACKETT_API_KEY") and not get_secret("JACKETT_API_KEY"):
            if args.json:
                # Machine-readable output must never interleave prompts; fall
                # back to Lite Mode silently instead of asking the user.
                args.lite = True
                config["LITE_MODE_ONLY"] = True
                from helm.core.config_manager import save_config

                save_config(config)
            else:
                try:
                    choice = (
                        input(
                            f"\n\033[1m{C_TEXT}? No configuration found. Would you like to run in Lite Mode (zero setup)? [Y/n]:{C_RST} "
                        )
                        .strip()
                        .lower()
                    )
                    if choice != "n":
                        args.lite = True
                        config["LITE_MODE_ONLY"] = True
                        from helm.core.config_manager import save_config

                        save_config(config)
                    else:
                        ensure_config()
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{C_SUB}later bozo!{C_RST}")
                    sys.exit(0)
        else:
            ensure_config()

    if args.oneshot:
        from helm.core.oneshot import spin_up_oneshot, teardown_oneshot

        try:
            spin_up_oneshot()
        except KeyboardInterrupt:
            print(f"\n{C_SUB}later bozo!{C_RST}")
            teardown_oneshot()
            sys.exit(0)

    if args.command == "indexers":
        if args.lite:
            print(f"{C_ERR}Cannot manage Jackett indexers in Lite Mode.{C_RST}", file=sys.stderr)
            sys.exit(1)
        ensure_config()
        from helm.core.indexer_manager import JackettManager

        try:
            manager = JackettManager()
        except Exception as e:
            print(f"{C_ERR}Failed to initialize Jackett Manager: {e}{C_RST}", file=sys.stderr)
            sys.exit(1)

        sys.stdout.write(f"{C_LOGO}Fetching indexers from Jackett...{C_RST}\r\n")
        sys.stdout.flush()
        all_indexers = manager.get_all_indexers()

        while True:
            try:
                selected_indexer = interactive_indexer_selector(all_indexers)
                if selected_indexer:
                    if selected_indexer["configured"]:
                        sys.stdout.write(f"\r\n{C_TEXT}Removing {selected_indexer['title']}...{C_RST}\r\n")
                        sys.stdout.flush()
                        try:
                            manager.remove_indexer(selected_indexer["id"])
                            selected_indexer["configured"] = False
                        except Exception as e:
                            sys.stdout.write(f"\r\n{C_ERR}Failed to remove: {e}{C_RST}\r\n")
                            sys.stdout.flush()
                            time.sleep(2)
                    else:
                        if selected_indexer.get("type", "public") != "public":
                            sys.stdout.write(
                                f"\r\n{C_ERR}Cannot add {selected_indexer['type']} trackers via CLI as they require credentials. Please use the Jackett Web UI ({manager.url}).{C_RST}\r\n"
                            )
                            sys.stdout.flush()
                            time.sleep(3)
                        else:
                            sys.stdout.write(f"\r\n{C_TEXT}Adding {selected_indexer['title']}...{C_RST}\r\n")
                            sys.stdout.flush()
                            try:
                                manager.add_indexer(selected_indexer["id"])
                                selected_indexer["configured"] = True
                            except Exception as e:
                                error_msg = str(e)
                                if "500" in error_msg:
                                    error_msg = "Jackett rejected the default config. Please configure this indexer manually via the Jackett Web UI."
                                sys.stdout.write(f"\r\n{C_ERR}Failed to add: {error_msg}{C_RST}\r\n")
                                sys.stdout.flush()
                                time.sleep(3)
            except KeyboardInterrupt:
                sys.stdout.write(f"\n{C_SUB}Exiting indexer management.{C_RST}\n")
                sys.stdout.flush()
                try:
                    choice = (
                        input(f"\033[1m{C_TEXT}? Would you like to proceed to search? [Y/n]:{C_RST} ").strip().lower()
                    )
                    if choice == "n":
                        if args.oneshot:
                            from helm.core.oneshot import teardown_oneshot

                            teardown_oneshot()
                        sys.exit(0)
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{C_SUB}later bozo!{C_RST}")
                    if args.oneshot:
                        from helm.core.oneshot import teardown_oneshot

                        teardown_oneshot()
                    sys.exit(0)

    mode_str = " (ONE-SHOT MODE)" if args.oneshot else ""

    if not args.json and not args.query:
        print(f"{C_LOGO}{logo}{C_RST}")
        print(f"{C_SUB}THE HELM - Torrent automation MVP{mode_str}{C_RST}\n")

    if args.query:
        query = args.query
        content_type = args.type.lower()
    else:
        try:
            query = input(f"\033[1m{C_TEXT}? What would you like to search for:{C_RST} ").strip()
            content_type = (
                input(f"\033[1m{C_TEXT}? Content type [video/games/software/books/music] (video):{C_RST} ")
                .strip()
                .lower()
                or "video"
            )
        except KeyboardInterrupt:
            print(f"\n{C_SUB}later bozo!{C_RST}")
            if args.oneshot:
                teardown_oneshot()
            sys.exit(0)

    keywords = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["video"])
    negatives = NEGATIVE_KEYWORDS.get(content_type, NEGATIVE_KEYWORDS["video"])

    query_words = set(re.findall(r"\b\w+\b", query.lower()))
    negatives = [n for n in negatives if n.lower() not in query_words]

    search_query = query
    if content_type == "books":
        search_query = re.sub(r"[^\w\s]", "", query)

    try:
        all_items, args.lite = animated_search(
            search_query, content_type, lite_mode=args.lite, show_spinner=not args.json
        )
    except KeyboardInterrupt:
        print(f"\n{C_SUB}later bozo!{C_RST}")
        if args.oneshot:
            teardown_oneshot()
        sys.exit(0)

    source_name = "Lite mode" if args.lite else "Jackett & Lite"
    if not args.json:
        print(f"{C_LOGO}{source_name} returned {len(all_items)} raw results{C_RST}")

    config = load_config()
    min_seeds = config.get("min_seeds", 3)

    unique_items = dedupe(all_items)
    filtered = filter_items(unique_items, keywords, negatives, min_score=0, min_seeds=min_seeds)

    if not filtered:
        if args.json:
            print(json.dumps([]))
        else:
            print(f"{C_ERR}No torrents were found :({C_RST}", file=sys.stderr)
        if args.oneshot:
            teardown_oneshot()
        sys.exit(1)

    if args.json:
        from helm.core.secret_manager import sanitize_link

        json_output = [
            {
                "title": t.title,
                "link": sanitize_link(t.link),
                "seeders": getattr(t, "seeders", 0),
            }
            for t in filtered
        ]
        print(json.dumps(json_output, indent=2))
        if args.oneshot:
            teardown_oneshot()
        sys.exit(0)

    if args.auto:
        selected = filtered[0]
        _send_magnet(selected, args.lite, args.json, success_msg="Top torrent sent successfully :)")
        if args.oneshot and not args.lite:
            wait_for_download()
            teardown_oneshot()

        sys.exit(0)

    do_teardown = False
    try:
        selected, do_teardown = interactive_selector(filtered, mode_str, lite_mode=args.lite)
        _send_magnet(selected, args.lite, args.json)
        if (args.oneshot or do_teardown) and not args.lite:
            wait_for_download()
            teardown_oneshot()

    except KeyboardInterrupt:
        print(f"\n{C_SUB}later bozo!{C_RST}")
        if args.oneshot or do_teardown:
            try:
                teardown_oneshot()
            except Exception:
                pass
        sys.exit()


if __name__ == "__main__":
    main()
