"""Argument parsing for helm-torrent."""

import argparse
import os

from dotenv import load_dotenv

from helm.cli import deps
from helm.config.config_manager import CONTENT_PROFILES, worker_count
from helm.config.secret_manager import get_secrets_file
from helm.logger import configure_logging


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
    init_parser = subparsers.add_parser(
        "init", help="Set up the container stack: compose file, .env.docker, API keys", parents=[base_parser]
    )
    from helm.stack.stack_setup import add_arguments as add_init_arguments

    add_init_arguments(init_parser)

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

    subparsers.add_parser("banner", help="Print the banner, even when banner.enabled is false", parents=[base_parser])

    from helm.config.config_commands import add_parser as add_config_parser

    add_config_parser(subparsers, [base_parser])

    return parser


def parse_args(parser):
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

    # Config commands must run on a broken file and report true origins, and setup
    # must not stop on one, so they neither load settings early nor export the
    # worker count.
    if args.command not in ("config", "init"):
        requested = getattr(args, "lite_workers", None)
        if requested is None and "HELM_LITE_WORKERS" not in os.environ:
            requested = deps.load_settings().get("search.lite_workers")
        try:
            workers = worker_count(requested)
        except ValueError as error:
            parser.error(str(error))
        os.environ["HELM_LITE_WORKERS"] = str(workers)

    load_dotenv(get_secrets_file())
    configure_logging()
    return args
