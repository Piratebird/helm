"""The helm-torrent entry point: parse arguments and hand off to a command."""

import os
import sys

from helm.cli import deps
from helm.cli.commands import background, banner, headless, session, status, utility
from helm.cli.parser import build_parser, parse_args
from helm.config.settings import ConfigError
from helm.ui.ansi import T


def main():
    """Console entry point. It bypasses helm/cli/__main__.py, which wraps it for python -m."""
    try:
        run()
    except ConfigError as error:
        print(f"{T.ERROR}[!] {error}{T.RST}", file=sys.stderr)
        raise SystemExit(1) from None


def run():
    parser = build_parser()
    args = parse_args(parser)
    if background.dispatch_background(args, parser):
        return
    if args.command == "config":
        from helm.config.config_commands import run as run_config

        raise SystemExit(run_config(args))
    if args.command == "banner":
        banner.run(args)
        return
    if args.command in ("watch", "serve", "files", "download"):
        raise SystemExit(headless.run(args, parser))

    if args.command == "status":
        # Container status is a read-only query: it must never spin up the
        # ephemeral stack, prompt for config, or require an API key. Reap it
        # as early as possible so agents can call it non-interactively.
        status.run()
        return

    if args.command == "init":
        from helm.stack.stack_setup import main as setup_main

        try:
            code = setup_main(args)
        except KeyboardInterrupt:
            print(f"\n{T.ACCENT}later bozo!{T.RST}")
            code = 130
        raise SystemExit(code) from None

    if args.command in ("paths", "logs", "bug-report"):
        utility.run(args.command)
        raise SystemExit(0) from None

    if not args.lite:
        if deps.load_settings().get("search.lite_only"):
            args.lite = True
        elif (
            not os.getenv("JACKETT_API_KEY")
            and not deps.get_secret("JACKETT_API_KEY")
            and not os.getenv("PROWLARR_API_KEY")
            and not deps.get_secret("PROWLARR_API_KEY")
        ):
            if args.json or os.environ.get("HELM_DAEMONIZED") == "1":
                # Machine-readable output must never interleave prompts; fall
                # back to Lite Mode silently instead of asking the user.
                args.lite = True
                deps.remember({"search.lite_only": True})
            else:
                try:
                    choice = (
                        input(
                            f"\n{T.BOLD}{T.TEXT}? No configuration found. Would you like to run in Lite Mode (zero setup)? [Y/n]:{T.RST} "
                        )
                        .strip()
                        .lower()
                    )
                    if choice != "n":
                        args.lite = True
                        deps.remember({"search.lite_only": True})
                    else:
                        deps.ensure_config()
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{T.ACCENT}later bozo!{T.RST}")
                    raise SystemExit(0) from None
        else:
            deps.ensure_config()

    # Lite searches never start a container stack, even with --oneshot.
    args.oneshot = args.oneshot and not args.lite
    from helm.stack.oneshot import spin_up_oneshot, teardown_oneshot

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
        session.run(args, cleanup)
    finally:
        if started:
            cleanup()
