import argparse
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

from dotenv import load_dotenv

from helm.core.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, worker_count  # noqa: E402
from helm.core.logger import configure_logging, tail_log  # noqa: E402
from helm.core.secret_manager import get_secret, get_secrets_file  # noqa: E402
from helm.core.settings import ConfigError, load_settings, remember  # noqa: E402
from helm.ui.colors import C_ERR, C_LOGO, C_RST, C_SUB, C_TEXT, logo  # noqa: E402


def _lazy(module, name):
    """Defer a heavy import so --help and headless commands start quickly."""

    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    return call


add_magnet = _lazy("helm.core.qbittorrent_client", "add_magnet")
animated_search = _lazy("helm.ui.tui", "animated_search")
dedupe = _lazy("helm.core.torrent_filter", "dedupe")
ensure_config = _lazy("helm.core.config_wizard", "ensure_config")
filter_items = _lazy("helm.core.torrent_filter", "filter_items")
interactive_indexer_selector = _lazy("helm.ui.tui", "interactive_indexer_selector")
interactive_selector = _lazy("helm.ui.tui", "interactive_selector")
wait_for_download = _lazy("helm.core.oneshot", "wait_for_download")


def _send_magnet(selected, lite_mode, json_mode=False, success_msg="Torrent sent successfully :)"):
    """Send a torrent to qBittorrent, falling back to the built-in downloader if it is unavailable."""
    if lite_mode:
        from helm.core.lite_downloader import download_magnet

        return download_magnet(selected.link)

    try:
        add_magnet(selected.link)
        if not json_mode:
            print(f"{C_TEXT}{success_msg}{C_RST}")
    except Exception as e:
        print(
            f"\n\033[33m[!] qBittorrent not available ({e}). Falling back to LITE MODE downloader...\033[0m\n",
            file=sys.stderr,
        )
        from helm.core.lite_downloader import download_magnet

        return download_magnet(selected.link)
    return True


def _cmd_status():
    """Print running helm containers and the configured Web UI URLs (never prompts)."""
    print("Checking container status and Web UIs...")
    cmd_runner = "docker" if shutil.which("docker") else "podman" if shutil.which("podman") else None
    res = None
    if cmd_runner:
        cmd = [cmd_runner, "ps", "--format", "{{.Names}}: {{.Status}}"]
        try:
            res = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except Exception:
            print(f"Failed to run {cmd_runner} ps.")
    else:
        print("Native Mode - No containers running.")

    if res is not None:
        print("-" * 40)
        helm_containers = [line for line in res.stdout.splitlines() if "helm" in line.lower()]
        print("\n".join(helm_containers) if helm_containers else "No helm containers found.")
        print("-" * 40)

    settings = load_settings()
    container_text = (res.stdout or "").lower() if res else ""
    print("\n--- Web UIs ---")
    if container_text and "jackett" not in container_text:
        print("Jackett:     not running")
    else:
        print(f"Jackett:     {settings.get('services.jackett')}")
    if container_text and "prowlarr" not in container_text:
        print("Prowlarr:    not running")
    else:
        print(f"Prowlarr:    {settings.get('services.prowlarr')}")
    if container_text and "qbittorrent" not in container_text:
        print("qBittorrent: not running")
    else:
        print(f"qBittorrent: {settings.get('services.qbittorrent')}")
    print("---------------")


def build_parser():
    base_parser = argparse.ArgumentParser(add_help=False)
    global_opts = base_parser.add_argument_group("Global Options")
    global_opts.add_argument(
        "--config-dir", type=str, default=argparse.SUPPRESS, help="Override the configuration directory"
    )
    global_opts.add_argument("--state-dir", type=str, default=argparse.SUPPRESS, help="Override the state directory")
    global_opts.add_argument(
        "--daemon",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run a headless command in the background, or start a detached tmux UI",
    )
    global_opts.add_argument(
        "--session", default=argparse.SUPPRESS, help="Create a named reconnectable tmux UI session"
    )
    global_opts.add_argument("--status", action="store_true", help="List running containers and their Web UI URLs")
    global_opts.add_argument("--dl-dir", type=str, default=argparse.SUPPRESS, help="Override the downloads directory")
    global_opts.add_argument(
        "--lite-workers",
        type=int,
        choices=range(1, 13),
        default=argparse.SUPPRESS,
        help="Maximum concurrent Lite processes (default: HELM_LITE_WORKERS or 6)",
    )
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
        prog="helm-torrent",
        description="The Helm - Torrent automation CLI",
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
    search_parser.add_argument(
        "-t", "--type", type=str.lower, choices=sorted(CONTENT_PROFILES), default="video", help="Content type"
    )
    search_parser.add_argument(
        "-a", "--auto", action="store_true", help="Automatically select and send the top torrent"
    )

    # Indexers
    idx_parser = subparsers.add_parser("indexers", help="Manage Indexers interactively", parents=[base_parser])
    idx_parser.add_argument("--prowlarr", action="store_true", help="Manage Prowlarr indexers explicitly")
    idx_parser.add_argument("--jackett", action="store_true", help="Manage Jackett indexers explicitly")

    # Utility Commands
    subparsers.add_parser("status", help="List running containers and their Web UI URLs", parents=[base_parser])
    subparsers.add_parser("logs", help="Tail the last 20 lines of the application log", parents=[base_parser])
    subparsers.add_parser(
        "paths", help="Print all system data locations (Config, State, Downloads)", parents=[base_parser]
    )
    subparsers.add_parser(
        "bug-report", help="Zip the config and logs to the Desktop for reporting", parents=[base_parser]
    )

    download_parser = subparsers.add_parser(
        "download", help="Download a magnet, detail URL or .torrent file", parents=[base_parser]
    )
    download_parser.add_argument("source")
    watch_parser = subparsers.add_parser(
        "watch", help="Queue stable .torrent and magnet files from a directory", parents=[base_parser]
    )
    watch_parser.add_argument("directory")
    watch_parser.add_argument("--interval", type=float, default=1)
    watch_parser.add_argument("--once", action="store_true", help="Scan once and submit queued downloads")
    watch_parser.add_argument(
        "--enqueue-only", action="store_true", help="Leave processing to a running serve/watch worker"
    )
    for name in ("serve", "files"):
        service_parser = subparsers.add_parser(
            name,
            help="Authenticated job API and completed-file streaming"
            if name == "serve"
            else "Read-only completed-file server",
            parents=[base_parser],
        )
        service_parser.add_argument("--host", default="127.0.0.1")
        service_parser.add_argument("--port", type=int, default=8765)
    attach_parser = subparsers.add_parser("attach", help="Reconnect to a named tmux UI", parents=[base_parser])
    attach_parser.add_argument("name", nargs="?", default="main")
    daemon_parser = subparsers.add_parser(
        "daemon", help="List or stop managed background commands", parents=[base_parser]
    )
    daemon_parser.add_argument("action", choices=["status", "stop"])
    daemon_parser.add_argument("id", nargs="?")

    return parser


def _parse_args(parser):
    args = parser.parse_args()
    if getattr(args, "status", False):
        args.command = "status"

    if not args.command:
        args.command = "ui"

    if not hasattr(args, "lite"):
        args.lite = False
    if not hasattr(args, "json"):
        args.json = False
    if not hasattr(args, "oneshot"):
        args.oneshot = False

    if args.command == "search":
        args.query = " ".join(args.query).strip()
        if not args.query:
            parser.error("search query must not be empty")
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

    requested = getattr(args, "lite_workers", None)
    if requested is None and "HELM_LITE_WORKERS" not in os.environ:
        requested = load_settings().get("search.lite_workers")
    try:
        workers = worker_count(requested)
    except ValueError as error:
        parser.error(str(error))
    os.environ["HELM_LITE_WORKERS"] = str(workers)

    load_dotenv(get_secrets_file())
    configure_logging()
    return args


def _dispatch_background(args, parser):
    """Handle tmux and daemon management commands; return True when one ran."""
    daemon_mode = getattr(args, "daemon", False)
    session = getattr(args, "session", None)
    if args.command == "attach":
        from helm.core.runtime import attach_session

        attach_session(args.name)
        return True
    if args.command == "daemon":
        from helm.core.runtime import daemon_records, stop_daemon

        if args.action == "status":
            print(json.dumps(daemon_records(), indent=2))
        elif not args.id:
            parser.error("daemon stop requires an id")
        else:
            print(json.dumps({"stopped": stop_daemon(args.id)}))
        return True
    if session or (daemon_mode and args.command == "ui"):
        if args.command not in ("ui", "indexers"):
            parser.error("--session is supported for ui/indexers; headless commands use --daemon")
        from helm.core.runtime import start_session

        start_session(session or "main", sys.argv[1:])
        return True
    if daemon_mode:
        if args.command not in ("search", "download", "watch", "serve", "files"):
            parser.error("--daemon requires a headless command or ui")
        if args.command == "search" and not (args.auto or args.json):
            parser.error("background searches require --auto or --json")
        from helm.core.runtime import start_daemon

        start_daemon(sys.argv[1:])
        return True
    return False


def _run_download(source, lite):
    """Run one queued download, also waiting if a service worker already owns it."""
    from helm.core.jobs import JobStore, process_one

    store = JobStore()
    job = store.enqueue(source, lite)
    while store.get(job["id"])["status"] in ("pending", "running"):
        if not process_one(store):
            time.sleep(1)
    final = store.get(job["id"])
    print(json.dumps({"id": final["id"], "status": final["status"], "error": final["error"]}))
    return 0 if final["status"] in ("succeeded", "submitted") else 1


def _run_headless(args, parser):
    settings = load_settings()
    lite = args.lite or settings.get("search.lite_only")
    if not lite and not (get_secret("QB_PASSWORD") or settings.origin("services.qbittorrent") != "default"):
        lite = True
    if args.command == "watch":
        from helm.core.watch import run_watch

        return run_watch(args.directory, lite, args.interval, args.once, args.enqueue_only)
    if args.command in ("serve", "files"):
        if not 0 <= args.port <= 65535:
            parser.error("port must be between 0 and 65535")
        from helm.core.server import run_server

        return run_server(args.host, args.port, lite, read_only=args.command == "files")
    return _run_download(args.source, lite)


def _run_utility(command):
    if command == "paths":
        from helm.core.config_manager import get_config_dir, get_dl_dir, get_log_dir

        print("\033[1m\033[36mHelm Data Locations:\033[0m")
        print(f"  Configuration: {get_config_dir()}")
        print(f"  State/Logs:    {get_log_dir()}")
        print(f"  Downloads:     {get_dl_dir()}")
        return

    if command == "logs":
        from helm.core.config_manager import get_log_dir

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

        from helm.core.config_manager import get_log_dir
        from helm.core.settings import read_file, report_config_file

        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.exists(desktop):
            desktop = os.path.expanduser("~")
        zip_path = os.path.join(desktop, "helm-bug-report.zip")
        config_file = report_config_file()
        log_file = os.path.join(get_log_dir(), "helm.log")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(config_file):
                from helm.core.secret_manager import redact_config

                try:
                    zipf.writestr("config.json", json.dumps(redact_config(read_file(config_file)), indent=4))
                except Exception:
                    print("Could not redact the config; omitting it from the bug report.", file=sys.stderr)
            if os.path.exists(log_file):
                from helm.core.secret_manager import redact_text

                with open(log_file, "r", encoding="utf-8", errors="replace") as source, zipf.open(
                    "helm.log", "w"
                ) as target:
                    for line in source:
                        target.write(redact_text(line).encode("utf-8"))
        print(f"Bug report successfully created at: {zip_path}")
        print("Please attach this zip file when creating an issue on GitHub.")
        return


def main():
    """Console entry point. It bypasses the __main__ block below."""
    try:
        run()
    except ConfigError as error:
        print(f"\033[31m[!] {error}\033[0m", file=sys.stderr)
        raise SystemExit(1) from None


def run():
    parser = build_parser()
    args = _parse_args(parser)
    if _dispatch_background(args, parser):
        return
    if args.command in ("watch", "serve", "files", "download"):
        raise SystemExit(_run_headless(args, parser))

    if args.command == "status":
        # Container status is a read-only query: it must never spin up the
        # ephemeral stack, prompt for config, or require an API key. Reap it
        # as early as possible so agents can call it non-interactively.
        _cmd_status()
        return

    if args.command == "init":
        from helm.core.init_env import bootstrap_env

        try:
            bootstrap_env()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C_SUB}later bozo!{C_RST}")
        raise SystemExit(0) from None

    if args.command in ("paths", "logs", "bug-report"):
        _run_utility(args.command)
        raise SystemExit(0) from None

    if not args.lite:
        if load_settings().get("search.lite_only"):
            args.lite = True
        elif (
            not os.getenv("JACKETT_API_KEY")
            and not get_secret("JACKETT_API_KEY")
            and not os.getenv("PROWLARR_API_KEY")
            and not get_secret("PROWLARR_API_KEY")
        ):
            if args.json or os.environ.get("HELM_DAEMONIZED") == "1":
                # Machine-readable output must never interleave prompts; fall
                # back to Lite Mode silently instead of asking the user.
                args.lite = True
                remember({"search.lite_only": True})
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
                        remember({"search.lite_only": True})
                    else:
                        ensure_config()
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{C_SUB}later bozo!{C_RST}")
                    raise SystemExit(0) from None
        else:
            ensure_config()

    # Lite searches never start a container stack, even with --oneshot.
    args.oneshot = args.oneshot and not args.lite
    from helm.core.oneshot import spin_up_oneshot, teardown_oneshot

    started = False

    def cleanup():
        nonlocal started
        if started:
            started = False
            teardown_oneshot()
        elif not args.oneshot and not args.lite:
            # Ctrl-E explicitly requests cleanup for an existing permanent stack.
            teardown_oneshot()

    try:
        if args.oneshot:
            # Mark ownership before startup so partial startup is cleaned up too.
            started = True
            spin_up_oneshot()
        _run_session(args, cleanup)
    finally:
        if started:
            cleanup()


def _manage_indexers(args):
    if args.lite:
        print(f"{C_ERR}Cannot manage Indexers in Lite Mode.{C_RST}", file=sys.stderr)
        raise SystemExit(1) from None
    ensure_config()

    use_prowlarr = getattr(args, "prowlarr", False)
    use_jackett = getattr(args, "jackett", False)

    if not use_prowlarr and not use_jackett:
        manager_type = load_settings().get("search.indexer_manager")
    elif use_prowlarr:
        manager_type = "prowlarr"
    else:
        manager_type = "jackett"

    try:
        if manager_type == "prowlarr":
            from helm.core.indexer_manager import ProwlarrManager

            manager = ProwlarrManager()
            mgr_name = "Prowlarr"
        else:
            from helm.core.indexer_manager import JackettManager

            manager = JackettManager()
            mgr_name = "Jackett"
    except Exception as e:
        print(f"{C_ERR}Failed to initialize Manager: {e}{C_RST}", file=sys.stderr)
        raise SystemExit(1) from None

    sys.stdout.write(f"{C_LOGO}Fetching indexers from {mgr_name}...{C_RST}\r\n")
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
                choice = input(f"\033[1m{C_TEXT}? Would you like to proceed to search? [Y/n]:{C_RST} ").strip().lower()
                if choice == "n":
                    raise SystemExit(0) from None
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C_SUB}later bozo!{C_RST}")
                raise SystemExit(0) from None


def _run_session(args, teardown_oneshot):
    if args.command == "indexers":
        _manage_indexers(args)

    mode_str = " (ONE-SHOT MODE)" if args.oneshot else ""

    if not args.json and not args.query:
        print(f"{C_LOGO}{logo}{C_RST}")
        print(f"{C_SUB}THE HELM - Torrent automation MVP{mode_str}{C_RST}\n")

    # State Machine Loop
    state = "PROMPT"
    search_query = ""
    categories = []
    filtered = []

    while True:
        if state == "PROMPT":
            if args.query:
                search_query = args.query
                categories = [args.type.lower()]
                state = "SEARCHING"
            else:
                try:
                    search_query = input(f"\033[1m{C_TEXT}? What would you like to search for:{C_RST} ").strip()
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{C_SUB}later bozo!{C_RST}")
                    raise SystemExit(0) from None

                if not search_query:
                    continue

                from helm.ui.tui import interactive_checkbox_selector

                try:
                    selected_cats = interactive_checkbox_selector(
                        ["video", "games", "software", "books", "music"], "Content type"
                    )
                except KeyboardInterrupt:
                    print(f"\n{C_SUB}later bozo!{C_RST}")
                    raise SystemExit(0) from None

                if selected_cats == "BACK":
                    # Go back to query prompt by staying in PROMPT
                    print("\033[2J\033[H", end="")
                    continue
                categories = selected_cats
                print("\033[2J\033[H", end="")
                state = "SEARCHING"

        elif state == "SEARCHING":
            keywords = []
            negatives = []
            for cat in categories:
                keywords.extend(CONTENT_PROFILES.get(cat, []))
                negatives.extend(NEGATIVE_KEYWORDS.get(cat, []))

            # Collect profiles for scoring
            cat_profiles = {cat: CONTENT_PROFILES.get(cat, []) for cat in categories}
            all_positives = [kw for kws in cat_profiles.values() for kw in kws]

            # Remove selected keywords from negatives to prevent cross-cancellation
            negatives = [n for n in negatives if n not in all_positives]

            query_words = set(re.findall(r"\b\w+\b", search_query.lower()))
            negatives = [n for n in negatives if n.lower() not in query_words]

            # Special case for books
            cleaned_query = search_query
            if "books" in categories and len(categories) == 1:
                cleaned_query = re.sub(r"[^\w\s]", "", search_query)

            # Pass comma-separated categories so Jackett can hit multiple endpoints
            try:
                all_items, args.lite = animated_search(
                    cleaned_query, ",".join(categories), lite_mode=args.lite, show_spinner=not args.json
                )
            except KeyboardInterrupt:
                print(f"\n{C_SUB}later bozo!{C_RST}")
                raise SystemExit(0) from None

            source_name = "Lite mode" if args.lite else "Jackett & Lite"
            if not args.json:
                print(f"{C_LOGO}{source_name} returned {len(all_items)} raw results{C_RST}")

            min_seeds = load_settings().get("search.min_seeds")

            unique_items = dedupe(all_items)
            filtered = filter_items(unique_items, cat_profiles, negatives, min_score=0, min_seeds=min_seeds)

            if not filtered:
                if args.json:
                    print(json.dumps([]))
                    raise SystemExit(1) from None
                else:
                    print(f"\n{C_ERR}No torrents were found :({C_RST}", file=sys.stderr)
                    if not all_items and args.lite:
                        print(
                            "Lite sources may be unreachable or have no matches. "
                            "Try a specific query and check 'helm-torrent logs' for source errors.",
                            file=sys.stderr,
                        )
                    elif all_items:
                        print(
                            f"All {len(all_items)} results were removed by filters "
                            f"(minimum seeders: {min_seeds}). Review min_seeds and content type.",
                            file=sys.stderr,
                        )
                    # If we have arguments, we exit. Else we go back to prompt.
                    if args.query:
                        raise SystemExit(1) from None
                    else:
                        input("Press Enter to try again...")
                        print("\033[2J\033[H", end="")
                        state = "PROMPT"
                        continue

            if args.json:
                from helm.core.secret_manager import sanitize_link

                json_output = [
                    {
                        "title": t.title,
                        "link": sanitize_link(t.link),
                        "seeders": getattr(t, "seeders", 0),
                        "indexer": getattr(t, "indexer", "Unknown"),
                    }
                    for t in filtered
                ]
                print(json.dumps(json_output, indent=2))
                sys.stdout.flush()
                raise SystemExit(0) from None

            if args.auto:
                selected = filtered[0]
                if (
                    _send_magnet(selected, args.lite, args.json, success_msg="Top torrent sent successfully :)")
                    is False
                ):
                    raise SystemExit(1)
                if args.oneshot and not args.lite:
                    wait_for_download()
                raise SystemExit(0) from None

            state = "RESULTS"

        elif state == "RESULTS":
            do_teardown = False
            try:
                media_type_str = "+".join(categories) if categories else ""
                selected, do_teardown = interactive_selector(
                    filtered, mode_str, lite_mode=args.lite, media_type=media_type_str
                )

                if do_teardown == "BACK":
                    print("\033[2J\033[H", end="")
                    if args.query:
                        # Cannot go back if started from CLI args
                        raise SystemExit(0) from None
                    state = "PROMPT"
                    continue

                print()
                if _send_magnet(selected, args.lite, args.json) is False:
                    raise SystemExit(1)
                if (args.oneshot or do_teardown) and not args.lite:
                    wait_for_download()
                    if not args.oneshot:
                        teardown_oneshot()
                break

            except KeyboardInterrupt:
                print(f"\n{C_SUB}later bozo!{C_RST}")
                if do_teardown and not args.oneshot and not args.lite:
                    try:
                        teardown_oneshot()
                    except Exception:
                        pass
                raise SystemExit(0) from None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        import sys

        print("\n\033[33m[!] Process interrupted by user.\033[0m", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        import sys

        print(f"\n\033[31m[!] An unexpected error occurred: {e}\033[0m", file=sys.stderr)
        sys.exit(1)
