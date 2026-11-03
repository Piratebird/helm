"""The interactive search session behind helm-torrent, ui and search."""

import json
import os
import re
import sys

from helm.cli import deps
from helm.cli.commands.banner import print_header
from helm.cli.commands.indexers import manage_indexers
from helm.config.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, TORZNAB_GROUPS, WEAK_NEGATIVE_KEYWORDS
from helm.ui.ansi import T


def send_magnet(selected, lite_mode, json_mode=False, success_msg="Torrent sent successfully :)", tag=None):
    """Send a torrent to qBittorrent, falling back to the built-in downloader if it is unavailable."""
    from helm.search import plugin_download

    # A plugin result may link to a details page that only its plugin can turn into a torrent.
    link = plugin_download.resolve(selected) or selected.link
    try:
        return _send(link, lite_mode, json_mode, success_msg, tag)
    finally:
        if link != selected.link and plugin_download.is_plugin_temp_file(link):
            try:
                os.remove(link)
            except OSError:
                pass


def _send(link, lite_mode, json_mode, success_msg, tag):
    if lite_mode:
        from helm.torrents.lite_downloader import download_torrent

        return download_torrent(link)

    try:
        deps.add_magnet(link, tag=f"helm,{tag}" if tag else "helm")
        if not json_mode:
            print(f"{T.TEXT}{success_msg}{T.RST}")
    except Exception as e:
        print(
            f"\n{T.WARNING}[!] qBittorrent not available ({e}). Falling back to LITE MODE downloader...{T.RST}\n",
            file=sys.stderr,
        )
        from helm.torrents.lite_downloader import download_torrent

        return download_torrent(link)
    return True


def run(args, teardown_oneshot):
    if args.command == "indexers":
        manage_indexers(args)

    from helm.torrents.qbittorrent_client import session_tag

    mode_str = " (ONE-SHOT MODE)" if args.oneshot else ""
    tag = session_tag()  # marks this run's torrents, so the wait ignores everyone else's

    if not args.json and not args.query:
        print_header(args, f"THE HELM - Torrent automation MVP{mode_str}")

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
                    search_query = input(f"{T.BOLD}{T.TEXT}? What would you like to search for:{T.RST} ").strip()
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{T.ACCENT}later bozo!{T.RST}")
                    raise SystemExit(0) from None

                if not search_query:
                    continue

                from helm.ui.tui import interactive_checkbox_selector

                try:
                    selected_cats = interactive_checkbox_selector(
                        ["video", "games", "software", "books", "music"], "Content type"
                    )
                except KeyboardInterrupt:
                    print(f"\n{T.ACCENT}later bozo!{T.RST}")
                    raise SystemExit(0) from None

                if selected_cats == "BACK":
                    # Go back to query prompt by staying in PROMPT
                    print(f"{T.CLEAR}{T.HOME}", end="")
                    continue
                categories = selected_cats
                print(f"{T.CLEAR}{T.HOME}", end="")
                state = "SEARCHING"

        elif state == "SEARCHING":
            keywords = []
            negatives = []
            weak_negatives = []
            groups = set()
            for cat in categories:
                keywords.extend(CONTENT_PROFILES.get(cat, []))
                negatives.extend(NEGATIVE_KEYWORDS.get(cat, []))
                weak_negatives.extend(WEAK_NEGATIVE_KEYWORDS.get(cat, []))
                groups.update(TORZNAB_GROUPS.get(cat, ()))

            # Collect profiles for scoring
            cat_profiles = {cat: CONTENT_PROFILES.get(cat, []) for cat in categories}
            all_positives = [kw for kws in cat_profiles.values() for kw in kws]

            # Remove selected keywords from negatives to prevent cross-cancellation
            negatives = [n for n in negatives if n not in all_positives]

            query_words = set(re.findall(r"\b\w+\b", search_query.lower()))
            negatives = [n for n in negatives if n.lower() not in query_words]
            weak_negatives = [n for n in weak_negatives if n not in all_positives and n.lower() not in query_words]

            # Special case for books
            cleaned_query = search_query
            if "books" in categories and len(categories) == 1:
                cleaned_query = re.sub(r"[^\w\s]", "", search_query)

            # Pass comma-separated categories so Jackett can hit multiple endpoints
            try:
                all_items, args.lite = deps.animated_search(
                    cleaned_query, ",".join(categories), lite_mode=args.lite, show_spinner=not args.json
                )
            except KeyboardInterrupt:
                print(f"\n{T.ACCENT}later bozo!{T.RST}")
                raise SystemExit(0) from None

            source_name = "Lite mode" if args.lite else "Jackett & Lite"
            if not args.json:
                print(f"{T.LOGO}{source_name} returned {len(all_items)} raw results{T.RST}")

            min_seeds = deps.load_settings().get("search.min_seeds")

            unique_items = deps.dedupe(all_items)
            hidden = []
            filtered = deps.filter_items(
                unique_items,
                cat_profiles,
                negatives,
                min_score=0,
                min_seeds=min_seeds,
                weak_negatives=weak_negatives,
                hidden=hidden,
                groups=groups,
            )

            if not filtered:
                if args.json:
                    print(json.dumps([]))
                    raise SystemExit(1) from None
                else:
                    print(f"\n{T.ERROR}No torrents were found :({T.RST}", file=sys.stderr)
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
                        print(f"{T.CLEAR}{T.HOME}", end="")
                        state = "PROMPT"
                        continue

            if args.json:
                from helm.config.secret_manager import sanitize_link

                json_output = [
                    {
                        "title": t.title,
                        "link": sanitize_link(t.link),
                        "seeders": getattr(t, "seeders", 0),
                        "indexer": getattr(t, "indexer", "Unknown"),
                        "sources": getattr(t, "sources", None) or [getattr(t, "indexer", "Unknown")],
                    }
                    for t in filtered
                ]
                print(json.dumps(json_output, indent=2))
                sys.stdout.flush()
                raise SystemExit(0) from None

            if args.auto:
                selected = filtered[0]
                if (
                    send_magnet(selected, args.lite, args.json, success_msg="Top torrent sent successfully :)", tag=tag)
                    is False
                ):
                    raise SystemExit(1)
                if args.oneshot and not args.lite:
                    deps.wait_for_download(tag)
                raise SystemExit(0) from None

            state = "RESULTS"

        elif state == "RESULTS":
            do_teardown = False
            try:
                media_type_str = "+".join(categories) if categories else ""
                selected, do_teardown = deps.interactive_selector(
                    filtered, mode_str, lite_mode=args.lite, media_type=media_type_str, hidden=hidden
                )

                if do_teardown == "BACK":
                    print(f"{T.CLEAR}{T.HOME}", end="")
                    if args.query:
                        # Cannot go back if started from CLI args
                        raise SystemExit(0) from None
                    state = "PROMPT"
                    continue

                print()
                if send_magnet(selected, args.lite, args.json, tag=tag) is False:
                    raise SystemExit(1)
                if (args.oneshot or do_teardown) and not args.lite:
                    deps.wait_for_download(tag)
                    if not args.oneshot:
                        teardown_oneshot()
                break

            except KeyboardInterrupt:
                print(f"\n{T.ACCENT}later bozo!{T.RST}")
                if do_teardown and not args.oneshot and not args.lite:
                    try:
                        teardown_oneshot()
                    except Exception:
                        pass
                raise SystemExit(0) from None
