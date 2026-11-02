"""helm-torrent download, watch, serve and files."""

import json
import time

from helm.cli import deps


def run_download(source, lite):
    """Run one queued download, also waiting if a service worker already owns it."""
    from helm.services.jobs import JobStore, process_one

    store = JobStore()
    job = store.enqueue(source, lite)
    while store.get(job["id"])["status"] in ("pending", "running"):
        if not process_one(store):
            time.sleep(1)
    final = store.get(job["id"])
    print(json.dumps({"id": final["id"], "status": final["status"], "error": final["error"]}))
    return 0 if final["status"] in ("succeeded", "submitted") else 1


def run(args, parser):
    settings = deps.load_settings()
    lite = args.lite or settings.get("search.lite_only")
    if not lite and not (deps.get_secret("QB_PASSWORD") or settings.origin("services.qbittorrent") != "default"):
        lite = True
    if args.command == "watch":
        from helm.services.watch import run_watch

        return run_watch(args.directory, lite, args.interval, args.once, args.enqueue_only)
    if args.command in ("serve", "files"):
        if not 0 <= args.port <= 65535:
            parser.error("port must be between 0 and 65535")
        from helm.services.server import run_server

        return run_server(args.host, args.port, lite, read_only=args.command == "files")
    return run_download(args.source, lite)
