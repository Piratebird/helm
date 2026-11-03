"""helm-torrent attach and daemon, plus --daemon and --session."""

import json
import sys


def dispatch_background(args, parser):
    """Handle tmux and daemon management commands; return True when one ran."""
    daemon_mode = getattr(args, "daemon", False)
    session = getattr(args, "session", None)
    if args.command == "attach":
        from helm.services.runtime import attach_session

        attach_session(args.name)
        return True
    if args.command == "daemon":
        from helm.services.runtime import daemon_records, prune_daemons, stop_daemon

        if args.action == "status":
            print(json.dumps(daemon_records(), indent=2))
        elif args.action == "prune":
            print(json.dumps({"pruned": prune_daemons()}))
        elif not args.id:
            parser.error("daemon stop requires an id")
        else:
            print(json.dumps({"stopped": stop_daemon(args.id)}))
        return True
    if session or (daemon_mode and args.command == "ui"):
        if args.command not in ("ui", "indexers"):
            parser.error("--session is supported for ui/indexers; headless commands use --daemon")
        from helm.services.runtime import start_session

        start_session(session or "main", sys.argv[1:])
        return True
    if daemon_mode:
        if args.command not in ("search", "download", "watch", "serve", "files"):
            parser.error("--daemon requires a headless command or ui")
        if args.command == "search" and not (args.auto or args.json):
            parser.error("background searches require --auto or --json")
        from helm.services.runtime import start_daemon

        start_daemon(sys.argv[1:])
        return True
    return False
