import os
import sys
import argparse
import json
from dotenv import load_dotenv

load_dotenv(os.path.expanduser('~/.helm_data/.env'))

from helm.core.config_wizard import ensure_config
from helm.core.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, load_config
from helm.core.torrent_filter import filter_items, dedupe
from helm.core.qbittorrent_client import add_magnet
from helm.core.oneshot import spin_up_oneshot, teardown_oneshot, wait_for_download
import re
import time

from helm.ui.colors import C_LOGO, C_SUB, C_TEXT, C_ERR, C_RST, logo
from helm.ui.tui import animated_search, interactive_indexer_selector, interactive_selector

def main():
        parser = argparse.ArgumentParser(description="Helm - Torrent automation MVP")
        parser.add_argument(
            "-q", "--query", help="Search query (bypasses input prompt)", type=str
        )
        parser.add_argument(
            "-t",
            "--type",
            help="Content type (video, games, etc.)",
            type=str,
            default="video",
        )
        parser.add_argument(
            "-a", "--auto",
            action="store_true",
            help="Automatically select the top torrent and send it",
        )
        parser.add_argument(
            "-i", "--indexers",
            action="store_true",
            help="Manage Jackett indexers",
        )
        parser.add_argument(
            "-o", "--oneshot",
            action="store_true",
            help="Start docker stack, search, download, wait for completion, and tear down",
        )
        parser.add_argument(
            "-j", "--json", action="store_true", help="Output results as JSON and exit"
        )
        parser.add_argument(
            "-l", "--lite", action="store_true", help="Lite mode: Search public indexers directly without Jackett"
        )
        args = parser.parse_args()

        if not args.lite:
            config = load_config()
            if "JACKETT_API_KEY" not in config and not os.getenv("JACKETT_API_KEY"):
                try:
                    choice = input(f"\n\033[1m{C_TEXT}? No configuration found. Would you like to run in Lite Mode (zero setup)? [Y/n]:{C_RST} ").strip().lower()
                    if choice != 'n':
                        args.lite = True
                    else:
                        ensure_config()
                except (KeyboardInterrupt, EOFError):
                    print(f"\n{C_SUB}later bozo!{C_RST}")
                    sys.exit(0)
            else:
                ensure_config()

        if args.indexers:
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
                        if selected_indexer['configured']:
                            sys.stdout.write(f"\r\n{C_TEXT}Removing {selected_indexer['title']}...{C_RST}\r\n")
                            sys.stdout.flush()
                            try:
                                manager.remove_indexer(selected_indexer['id'])
                                selected_indexer['configured'] = False
                            except Exception as e:
                                sys.stdout.write(f"\r\n{C_ERR}Failed to remove: {e}{C_RST}\r\n")
                                sys.stdout.flush()
                                time.sleep(2)
                        else:
                            if selected_indexer.get('type', 'public') != 'public':
                                sys.stdout.write(f"\r\n{C_ERR}Cannot add {selected_indexer['type']} trackers via CLI as they require credentials. Please use the Jackett Web UI ({manager.url}).{C_RST}\r\n")
                                sys.stdout.flush()
                                time.sleep(3)
                            else:
                                sys.stdout.write(f"\r\n{C_TEXT}Adding {selected_indexer['title']}...{C_RST}\r\n")
                                sys.stdout.flush()
                                try:
                                    manager.add_indexer(selected_indexer['id'])
                                    selected_indexer['configured'] = True
                                except Exception as e:
                                    error_msg = str(e)
                                    if "500" in error_msg:
                                        error_msg = "Jackett rejected the default config. Please configure this indexer manually via the Jackett Web UI."
                                    sys.stdout.write(f"\r\n{C_ERR}Failed to add: {error_msg}{C_RST}\r\n")
                                    sys.stdout.flush()
                                    time.sleep(3)
                except KeyboardInterrupt:
                    print(f"\n{C_SUB}Exiting indexer management.{C_RST}")
                    sys.exit(0)

        if args.oneshot:
            try:
                spin_up_oneshot()
            except KeyboardInterrupt:
                print(f"\n{C_SUB}later bozo!{C_RST}")
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
                query = input(
                    f"\033[1m{C_TEXT}? What would you like to search for:{C_RST} "
                ).strip()
                content_type = (
                    input(
                        f"\033[1m{C_TEXT}? Content type [video/games/software/books/music] (video):{C_RST} "
                    )
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
            all_items, args.lite = animated_search(search_query, content_type, lite_mode=args.lite)
        except KeyboardInterrupt:
            print(f"\n{C_SUB}later bozo!{C_RST}")
            if args.oneshot:
                teardown_oneshot()
            sys.exit(0)
    
        source_name = "Lite mode" if args.lite else "Jackett"
        print(f"{C_LOGO}{source_name} returned {len(all_items)} raw results{C_RST}")

        config = load_config()
        min_seeds = config.get("min_seeds", 3)

        unique_items = dedupe(all_items)
        filtered = filter_items(
            unique_items, keywords, negatives, min_score=0, min_seeds=min_seeds
        )

        if not filtered:
            if args.json:
                print(json.dumps([]))
            else:
                print(f"{C_ERR}No torrents were found :({C_RST}", file=sys.stderr)
            if args.oneshot:
                teardown_oneshot()
            sys.exit(1)

        if args.json:
            json_output = [
                {"title": t.title, "link": t.link, "seeders": getattr(t, "seeders", 0)}
                for t in filtered
            ]
            print(json.dumps(json_output, indent=2))
            if args.oneshot:
                teardown_oneshot()
            sys.exit(0)

        if args.auto:
            selected = filtered[0]
            if args.lite:
                from helm.core.lite_downloader import download_magnet
                download_magnet(selected.link)
            else:
                try:
                    add_magnet(selected.link)
                    if not args.json:
                        print(f"{C_TEXT}Top torrent sent successfully :){C_RST}")
                except Exception as e:
                    print(f"\n\033[33m[!] qBittorrent not available ({e}). Falling back to LITE MODE downloader...\033[0m\n")
                    from helm.core.lite_downloader import download_magnet
                    download_magnet(selected.link)
            
                if args.oneshot:
                    wait_for_download()
                    teardown_oneshot()
            
            sys.exit(0)

        do_teardown = False
        try:
            selected, do_teardown = interactive_selector(filtered, mode_str)
            if args.lite:
                from helm.core.lite_downloader import download_magnet
                download_magnet(selected.link)
            else:
                try:
                    add_magnet(selected.link)
                    print(f"\n{C_TEXT}Torrent sent successfully :){C_RST}")
                except Exception as e:
                    print(f"\n\033[33m[!] qBittorrent not available ({e}). Falling back to LITE MODE downloader...\033[0m\n")
                    from helm.core.lite_downloader import download_magnet
                    download_magnet(selected.link)
            
                if args.oneshot or do_teardown:
                    wait_for_download()
                    teardown_oneshot()
            
        except KeyboardInterrupt:
            print(f"\n{C_SUB}later bozo!{C_RST}")
            if args.oneshot or do_teardown:
                try:
                    from helm.core.oneshot import teardown_oneshot
                    teardown_oneshot()
                except Exception:
                    pass
            sys.exit()

if __name__ == "__main__":
    main()
