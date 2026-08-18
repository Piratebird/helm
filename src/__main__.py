import os
import sys
import argparse
import json
from dotenv import load_dotenv

load_dotenv()

from core.config_wizard import ensure_config
from core.rss_fetcher import search_jackett
from core.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS, load_config
from core.torrent_filter import filter_items, dedupe
from core.qbittorrent_client import add_magnet
from core.oneshot import spin_up_oneshot, teardown_oneshot, wait_for_download
import re
import time
import threading
import tty
import termios
import select


def hex_to_ansi(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"\033[38;2;{r};{g};{b}m"


class Palette:
    NAVY = hex_to_ansi("#192A56")  # Text 'The HELM'
    MAROON = hex_to_ansi("#542023")  # Steering Wheel
    BROWN = hex_to_ansi("#5C4026")  # Anchor and Ropes
    CREAM = hex_to_ansi("#EADAC9")  # Background
    LIGHT_NAVY = hex_to_ansi("#4A69BD")  # Brighter variant for readability
    BRIGHT_MAROON = hex_to_ansi("#B33939")
    BRIGHT_BROWN = hex_to_ansi("#CD6133")
    RESET = "\033[0m"


C_LOGO = Palette.LIGHT_NAVY
C_SUB = Palette.BRIGHT_MAROON
C_TEXT = Palette.CREAM
C_LINE = Palette.BRIGHT_BROWN
C_ERR = hex_to_ansi("#FF5252")
C_RST = Palette.RESET


def format_size(size_bytes):
    if not size_bytes:
        return "????"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return "????"


def animated_search(query, content_type):
    done = False

    def animate():
        # cool ass animation while searching
        chars = [".  ", ".. ", "...", "   "]
        i = 0
        while not done:
            sys.stdout.write(f"\r{C_LOGO}Searching" + chars[i % 4] + f"{C_RST}")
            sys.stdout.flush()
            time.sleep(0.4)
            i += 1
        sys.stdout.write("\r" + " " * 20 + "\r")
        sys.stdout.flush()

    t = threading.Thread(target=animate)
    t.start()

    res = []
    try:
        retries = 3
        for attempt in range(retries):
            try:
                res = search_jackett(query, content_type)
                if res:
                    break
                elif attempt < retries - 1:
                    time.sleep(2)
            except Exception as e:
                if attempt == retries - 1:
                    sys.stdout.write(f"\n{C_ERR}Search failed after {retries} attempts: {e}{C_RST}\n")
                time.sleep(2)
    finally:
        done = True
        t.join()

    return res


# w logo perchance ?
logo = r"""
▗▖ ▗▖▗▄▄▄▖▗▖   ▗▖  ▗▖
▐▌ ▐▌▐▌   ▐▌   ▐▛▚▞▜▌
▐▛▀▜▌▐▛▀▀▘▐▌   ▐▌  ▐▌
▐▌ ▐▌▐▙▄▄▖▐▙▄▄▖▐▌  ▐▌
"""

if __name__ == "__main__":
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
    args = parser.parse_args()

    ensure_config()

    if args.indexers:
        from core.indexer_manager import JackettManager
        try:
            manager = JackettManager()
        except Exception as e:
            print(f"{C_ERR}Failed to initialize Jackett Manager: {e}{C_RST}", file=sys.stderr)
            sys.exit(1)
            
        sys.stdout.write(f"{C_LOGO}Fetching indexers from Jackett...{C_RST}\r\n")
        sys.stdout.flush()
        all_indexers = manager.get_all_indexers()
        
        def interactive_indexer_selector(indexer_list):
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            current_input = ""
            selected_index = 0
            
            def redraw():
                nonlocal selected_index
                sys.stdout.write("\033[2J\033[H")
                logo_crlf = logo.replace("\n", "\r\n")
                sys.stdout.write(f"{C_LOGO}{logo_crlf}{C_RST}\r\n")
                sys.stdout.write(f"{C_SUB}THE HELM - Indexer Management{C_RST}\r\n\r\n")
                
                search_term = current_input.lower()
                if search_term.startswith("/"):
                    search_term = search_term[1:]

                disp_items = [idx for idx in indexer_list if search_term in idx['title'].lower() or search_term in idx['id'].lower()]
                
                # Sort: Configured first, then alphabetically
                disp_items.sort(key=lambda x: (not x['configured'], x['title'].lower()))
                
                if selected_index >= len(disp_items):
                    selected_index = max(0, len(disp_items) - 1)
                    
                limit = 40
                start_idx = 0
                if len(disp_items) > limit:
                    start_idx = max(0, selected_index - (limit // 2))
                    if start_idx + limit > len(disp_items):
                        start_idx = max(0, len(disp_items) - limit)
                        
                window_items = disp_items[start_idx : start_idx + limit]
                
                sys.stdout.write(f"\033[1m{C_TEXT}Found {len(disp_items)} indexers (showing {start_idx + 1}-{start_idx + len(window_items)}):{C_RST}\r\n")
                sys.stdout.write(f"{C_LINE}" + "━" * 80 + f"{C_RST}\r\n")
                
                import unicodedata
                def get_display_width(s):
                    return sum(2 if unicodedata.east_asian_width(c) in ('F', 'W') else 1 for c in s)

                for i, idx in enumerate(window_items):
                    actual_i = start_idx + i
                    title = idx['title']
                    if get_display_width(title) > 40:
                        # Truncate by display width
                        current_w = 0
                        new_title = ""
                        for c in title:
                            cw = 2 if unicodedata.east_asian_width(c) in ('F', 'W') else 1
                            if current_w + cw > 37:
                                break
                            new_title += c
                            current_w += cw
                        title = new_title + "..."
                    
                    status = "\033[32m[CONFIGURED]\033[0m" if idx['configured'] else "\033[31m[UNCONFIGURED]\033[0m"
                    typ = idx.get('type', 'unknown')
                    
                    title_pad = max(2, 42 - get_display_width(title))
                    info_str = f"{status} {typ}"
                    
                    if actual_i == selected_index:
                        sys.stdout.write(f"\033[7m\033[1m{C_LOGO} ❯ {title}{C_RST}\033[7m{' ' * title_pad}{C_TEXT}{info_str}{C_RST}\r\n")
                    else:
                        sys.stdout.write(f"\033[1m{C_SUB}   {C_RST} {C_LOGO}{title}{C_RST}{' ' * title_pad}\033[1m{C_TEXT}{info_str}{C_RST}\r\n")
                
                if len(disp_items) > limit:
                    remaining = len(disp_items) - (start_idx + limit)
                    if remaining > 0:
                        sys.stdout.write(f"\r\n\033[3m{C_SUB}... and {remaining} more items below{C_RST}\033[0m\r\n")
                    if start_idx > 0:
                        sys.stdout.write(f"\r\n\033[3m{C_SUB}... and {start_idx} items above{C_RST}\033[0m\r\n")
                
                sys.stdout.write(f"{C_LINE}" + "━" * 80 + f"{C_RST}\r\n")
                prompt = f"\033[1m{C_TEXT}❯ Search (Arrows=Move, Enter=Toggle Config):{C_RST} {current_input}"
                sys.stdout.write(prompt)
                sys.stdout.flush()
                return disp_items

            try:
                tty.setraw(sys.stdin.fileno())
                sys.stdout.write("\033[?25l")
                items_to_show = redraw()

                while True:
                    ch = os.read(fd, 1).decode("utf-8", "ignore")
                    if ch == "\x03":
                        raise KeyboardInterrupt
                    elif ch == "\x1b":
                        if select.select([fd], [], [], 0.05)[0]:
                            ch2 = os.read(fd, 1).decode("utf-8", "ignore")
                            if ch2 == "[":
                                if select.select([fd], [], [], 0.05)[0]:
                                    ch3 = os.read(fd, 1).decode("utf-8", "ignore")
                                    if ch3 == "A":
                                        selected_index = max(0, selected_index - 1)
                                    elif ch3 == "B":
                                        selected_index = min(len(items_to_show) - 1, selected_index + 1)
                        else:
                            raise KeyboardInterrupt
                        items_to_show = redraw()
                    elif ch in ("\r", "\n"):
                        if items_to_show:
                            return items_to_show[selected_index]
                    elif ch in ("\x7f", "\x08", "\b"):
                        current_input = current_input[:-1]
                        selected_index = 0
                        items_to_show = redraw()
                    elif ch == "\x15":
                        current_input = ""
                        selected_index = 0
                        items_to_show = redraw()
                    elif ch == "\x17":
                        current_input = " ".join(current_input.rstrip().split(" ")[:-1])
                        if current_input:
                            current_input += " "
                        selected_index = 0
                        items_to_show = redraw()
                    else:
                        if ch.isprintable():
                            current_input += ch
                            selected_index = 0
                            items_to_show = redraw()
            finally:
                sys.stdout.write("\033[?25h")
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                
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
    
    # Public trackers have terrible search engines that fail if there is punctuation (e.g. "Charlotte's Web").
    # We strip punctuation for book searches to maximize the chance of finding the book.
    search_query = query
    if content_type == "books":
        search_query = re.sub(r"[^\w\s]", "", query)
        
    try:
        all_items = animated_search(search_query, content_type)
    except KeyboardInterrupt:
        print(f"\n{C_SUB}later bozo!{C_RST}")
        if args.oneshot:
            teardown_oneshot()
        sys.exit(0)
    print(f"{C_LOGO}Jackett returned {len(all_items)} raw results{C_RST}")

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
        add_magnet(selected.link)
        if not args.json:
            print(f"{C_TEXT}Top torrent sent successfully :){C_RST}")
        
        if args.oneshot:
            wait_for_download()
            teardown_oneshot()
            
        sys.exit(0)

    def interactive_selector(filtered_list):
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        current_input = ""
        selected_index = 0

        def redraw():
            nonlocal selected_index
            # Clear screen and reset cursor
            sys.stdout.write("\033[2J\033[H")

            # Print header again
            logo_crlf = logo.replace("\n", "\r\n")
            sys.stdout.write(f"{C_LOGO}{logo_crlf}{C_RST}\r\n")
            sys.stdout.write(f"{C_SUB}THE HELM - Torrent automation MVP{mode_str}{C_RST}\r\n\r\n")

            # live filter heck yeah
            search_term = current_input.lower()
            if search_term.startswith("/"):
                search_term = search_term[1:]

            disp_items = [t for t in filtered_list if search_term in t.title.lower()]

            if selected_index >= len(disp_items):
                selected_index = max(0, len(disp_items) - 1)

            limit = 40
            start_idx = 0
            if len(disp_items) > limit:
                start_idx = max(0, selected_index - (limit // 2))
                if start_idx + limit > len(disp_items):
                    start_idx = max(0, len(disp_items) - limit)

            window_items = disp_items[start_idx : start_idx + limit]

            # Find max seed width for alignment
            max_seeds = max(
                [getattr(t, "seeders", 0) for t in window_items] + [0]
            )
            seed_width = len(str(max_seeds))

            sys.stdout.write(
                f"\033[1m{C_TEXT}Found {len(disp_items)} results (showing {start_idx + 1}-{start_idx + len(window_items)}):{C_RST}\r\n"
            )
            sys.stdout.write(f"{C_LINE}" + "━" * 80 + f"{C_RST}\r\n")

            import unicodedata
            def get_display_width(s):
                return sum(2 if unicodedata.east_asian_width(c) in ('F', 'W') else 1 for c in s)

            for i, t in enumerate(window_items):
                actual_i = start_idx + i
                title = t.title
                
                if get_display_width(title) > 38:
                    current_w = 0
                    new_title = ""
                    for c in title:
                        cw = 2 if unicodedata.east_asian_width(c) in ('F', 'W') else 1
                        if current_w + cw > 35:
                            break
                        new_title += c
                        current_w += cw
                    title = new_title + "..."
                    
                title_pad = max(2, 40 - get_display_width(title))

                seeds = getattr(t, "seeders", 0)
                leechs = getattr(t, "leechers", 0)
                size_str = format_size(getattr(t, "size", 0))
                date_str = getattr(t, "pubdate", "") or "????-??-??"

                info_str = (
                    f"[{size_str:>7}] [{date_str:>10}] [{seeds:>4}↑ {leechs:>3}↓]"
                )

                score = getattr(t, "score", 1)
                if score == 0:
                    title = f"\033[33m[GENERIC]\033[0m {title}"
                    # Adjust padding because of ANSI escape codes
                    title_pad += 19

                if actual_i == selected_index:
                    # Highlighted row
                    sys.stdout.write(
                        f"\033[7m\033[1m{C_LOGO} ❯ {title}{C_RST}\033[7m{' ' * title_pad}{C_TEXT}{info_str}{C_RST}\r\n"
                    )
                else:
                    sys.stdout.write(
                        f"\033[1m{C_SUB}   {C_RST} {C_LOGO}{title}{C_RST}{' ' * title_pad}\033[1m{C_TEXT}{info_str}{C_RST}\r\n"
                    )

            if len(disp_items) > limit:
                remaining = len(disp_items) - (start_idx + limit)
                if remaining > 0:
                    sys.stdout.write(
                        f"\r\n\033[3m{C_SUB}... and {remaining} more items below{C_RST}\033[0m\r\n"
                    )
                if start_idx > 0:
                    sys.stdout.write(
                        f"\r\n\033[3m{C_SUB}... and {start_idx} items above{C_RST}\033[0m\r\n"
                    )

            sys.stdout.write(f"{C_LINE}" + "━" * 80 + f"{C_RST}\r\n")
            prompt = f"\033[1m{C_TEXT}❯ Filter (Arrows=Move, Enter=Download, Ctrl+E=Download+Teardown):{C_RST} {current_input}"
            sys.stdout.write(prompt)
            sys.stdout.flush()
            return disp_items

        try:
            tty.setraw(sys.stdin.fileno())
            sys.stdout.write("\033[?25l")  # Hide cursor
            items_to_show = redraw()

            while True:
                ch = os.read(fd, 1).decode("utf-8", "ignore")
                if ch == "\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                elif ch == "\x1b":  # Escape sequence
                    if select.select([fd], [], [], 0.05)[0]:
                        ch2 = os.read(fd, 1).decode("utf-8", "ignore")
                        if ch2 == "[":
                            if select.select([fd], [], [], 0.05)[0]:
                                ch3 = os.read(fd, 1).decode("utf-8", "ignore")
                                if ch3 == "A":  # Up
                                    selected_index = max(0, selected_index - 1)
                                elif ch3 == "B":  # Down
                                    selected_index = min(
                                        len(items_to_show) - 1, selected_index + 1
                                    )
                    else:
                        raise KeyboardInterrupt  # Plain Escape exits
                    items_to_show = redraw()
                elif ch == '\x05': # Ctrl+E (One-shot teardown mode)
                    if items_to_show:
                        return items_to_show[selected_index], True
                elif ch in ("\r", "\n"):  # Enter
                    if items_to_show:
                        return items_to_show[selected_index], False
                elif ch in ("\x7f", "\x08", "\b"):  # Backspace
                    current_input = current_input[:-1]
                    selected_index = 0
                    items_to_show = redraw()
                elif ch == "\x15":  # Ctrl+U to clear line
                    current_input = ""
                    selected_index = 0
                    items_to_show = redraw()
                elif ch == "\x17":  # Ctrl+W to delete word
                    current_input = " ".join(current_input.rstrip().split(" ")[:-1])
                    if current_input:
                        current_input += " "
                    selected_index = 0
                    items_to_show = redraw()
                else:
                    # Ignore non-printable characters for the prompt buffer
                    if ch.isprintable():
                        current_input += ch
                        selected_index = 0
                        items_to_show = redraw()
        finally:
            sys.stdout.write("\033[?25h")  # Show cursor
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    do_teardown = False
    try:
        selected, do_teardown = interactive_selector(filtered)
        add_magnet(selected.link)
        print(f"\n{C_TEXT}Torrent sent successfully :){C_RST}")
        
        if args.oneshot or do_teardown:
            wait_for_download()
            teardown_oneshot()
            
    except KeyboardInterrupt:
        print(f"\n{C_SUB}later bozo!{C_RST}")
        if args.oneshot or do_teardown:
            try:
                from core.oneshot import teardown_oneshot
                teardown_oneshot()
            except Exception:
                pass
        sys.exit()
