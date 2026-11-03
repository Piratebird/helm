import os
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import msvcrt
    import time
else:
    import select
    import termios
    import tty
import unicodedata  # noqa: E402

from helm.ui.ansi import T  # noqa: E402
from helm.ui.banner import WORDMARK as logo  # noqa: E402


def _read_key(fd):
    if IS_WINDOWS:
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b"\x00", b"\xe0"):
                    ch2 = msvcrt.getch()
                    if ch2 == b"H":
                        return "UP"
                    if ch2 == b"P":
                        return "DOWN"
                    continue
                if ch == b"\x1b":
                    return "ESC"
                try:
                    ch_str = ch.decode("utf-8", "ignore")
                    if ch_str == "\r":
                        return "\n"
                    return ch_str
                except:  # noqa: E722
                    continue
            time.sleep(0.01)
    else:
        ch = os.read(fd, 1).decode("utf-8", "ignore")
        if ch == "\x1b":
            if select.select([fd], [], [], 0.05)[0]:
                ch2 = os.read(fd, 1).decode("utf-8", "ignore")
                if ch2 == "[":
                    if select.select([fd], [], [], 0.05)[0]:
                        ch3 = os.read(fd, 1).decode("utf-8", "ignore")
                        if ch3 == "A":
                            return "UP"
                        if ch3 == "B":
                            return "DOWN"
            else:
                return "ESC"
        if ch == "\x7f":
            return "\b"
        return ch


def format_size(size_bytes):
    if not size_bytes:
        return "????"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return "????"


def animated_search(query, content_type, lite_mode=False, show_spinner=True):
    done = False

    def animate():
        chars = [".  ", ".. ", "...", "   "]
        i = 0
        while not done:
            sys.stdout.write(f"\r{T.LOGO}Searching" + chars[i % 4] + T.RST)
            sys.stdout.flush()
            time.sleep(0.4)
            i += 1
        sys.stdout.write("\r" + " " * 20 + "\r")
        sys.stdout.flush()

    t = None
    if show_spinner:
        t = threading.Thread(target=animate)
        t.start()

    from helm.search.rss_fetcher import SearchResults

    res = SearchResults()
    used_lite = lite_mode
    try:
        import concurrent.futures

        def fetch_jackett():
            from helm.search.rss_fetcher import search_indexers

            return search_indexers(query, content_type, fallback_lite=False)

        def fetch_lite():
            from helm.search.lite_fetcher import search_lite

            return search_lite(query, content_type)

        if not lite_mode:
            jackett_future = None
            lite_future = None

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                jackett_future = executor.submit(fetch_jackett)
                lite_future = executor.submit(fetch_lite)

                try:
                    res.extend(jackett_future.result())
                except Exception as e:
                    print(
                        f"Indexer manager unavailable ({type(e).__name__}); using Lite results.",
                        file=sys.stderr,
                    )
                    used_lite = True

                try:
                    res.extend(lite_future.result())
                except Exception as error:
                    from helm.search.rss_fetcher import SourceOutcome, source_status

                    res.outcomes.append(SourceOutcome("Lite", source_status(error)))
        else:
            res.extend(fetch_lite())

    finally:
        done = True
        if t is not None:
            t.join()

    if res.outcomes:
        summary = "; ".join(f"{o.source}: {o.status} ({o.count})" for o in res.outcomes)
        print(f"Sources: {summary}", file=sys.stderr)
    return res, used_lite


def get_display_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("F", "W") else 1 for c in s)


def _truncate_to_width(text, max_width):
    """Truncate *text* to *max_width* display columns, appending '...' when cut."""
    if get_display_width(text) <= max_width:
        return text
    current_w = 0
    cut = ""
    for c in text:
        cw = 2 if unicodedata.east_asian_width(c) in ("F", "W") else 1
        if current_w + cw > max_width - 3:
            break
        cut += c
        current_w += cw
    return cut + "..."


def _paginate(count, selected, limit):
    """Return (start_index, stop_index) for a scrolled window of size *limit*."""
    if count <= limit:
        return 0, count
    start = max(0, selected - (limit // 2))
    if start + limit > count:
        start = max(0, count - limit)
    return start, min(count, start + limit)


def _append_scroll_footer(buf, count, start, stop, limit):
    if count <= limit:
        return
    remaining = count - stop
    if remaining > 0:
        buf.append(f"{T.EOL}\r\n{T.ITALIC}{T.ACCENT}... and {remaining} more items below{T.RST}{T.RST}{T.EOL}\r\n")
    if start > 0:
        buf.append(f"{T.EOL}\r\n{T.ITALIC}{T.ACCENT}... and {start} items above{T.RST}{T.RST}{T.EOL}\r\n")


def _enter_raw_input(fd):
    if not IS_WINDOWS:
        tty.setraw(sys.stdin.fileno())
    sys.stdout.write(T.HIDE_CURSOR)


def _exit_raw_input(fd, old_settings):
    sys.stdout.write(T.SHOW_CURSOR)
    if not IS_WINDOWS:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def interactive_indexer_selector(indexer_list):
    fd = sys.stdin.fileno() if not IS_WINDOWS else None
    old_settings = termios.tcgetattr(fd) if not IS_WINDOWS else None
    current_input = ""
    selected_index = 0

    def redraw():
        nonlocal selected_index
        buf = []
        buf.append(T.HOME)
        logo_crlf = logo.replace("\n", f"{T.EOL}\r\n")
        buf.append(f"{T.LOGO}{logo_crlf}{T.RST}{T.EOL}\r\n")
        buf.append(f"{T.ACCENT}THE HELM - Indexer Management{T.RST}{T.EOL}\r\n{T.EOL}\r\n")

        search_term = current_input.lower()
        if search_term.startswith("/"):
            search_term = search_term[1:]

        disp_items = [
            idx for idx in indexer_list if search_term in idx["title"].lower() or search_term in idx["id"].lower()
        ]
        disp_items.sort(key=lambda x: (not x["configured"], x["title"].lower()))

        if selected_index >= len(disp_items):
            selected_index = max(0, len(disp_items) - 1)

        limit = 40
        start_idx, stop_idx = _paginate(len(disp_items), selected_index, limit)

        window_items = disp_items[start_idx:stop_idx]

        buf.append(
            f"{T.BOLD}{T.TEXT}Found {len(disp_items)} indexers (showing {start_idx + 1}-{start_idx + len(window_items)}):{T.RST}{T.EOL}\r\n"
        )
        buf.append(T.LINE + "━" * 80 + f"{T.RST}{T.EOL}\r\n")

        for i, idx in enumerate(window_items):
            actual_i = start_idx + i
            title = _truncate_to_width(idx["title"], 40)

            status = f"{T.SUCCESS}[CONFIGURED]{T.RST}" if idx["configured"] else f"{T.ERROR}[UNCONFIGURED]{T.RST}"
            typ = idx.get("type", "unknown")

            title_pad = max(2, 42 - get_display_width(title))
            info_str = f"{status} {typ}"

            if actual_i == selected_index:
                buf.append(
                    f"{T.REVERSE}{T.BOLD}{T.LOGO} ❯ {title}{T.RST}{T.REVERSE}{' ' * title_pad}{T.TEXT}{info_str}{T.RST}{T.EOL}\r\n"
                )
            else:
                buf.append(
                    f"{T.BOLD}{T.ACCENT}   {T.RST} {T.LOGO}{title}{T.RST}{' ' * title_pad}{T.BOLD}{T.TEXT}{info_str}{T.RST}{T.EOL}\r\n"
                )

        _append_scroll_footer(buf, len(disp_items), start_idx, stop_idx, limit)

        buf.append(T.LINE + "━" * 80 + f"{T.RST}{T.EOL}\r\n")
        prompt = f"{T.BOLD}{T.TEXT}❯ Search (Arrows=Move, Enter=Toggle Config, Esc/Ctrl-C=Exit):{T.RST} {current_input}"
        buf.append(prompt + T.CLEAR_DOWN)
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        return disp_items

    try:
        _enter_raw_input(fd)
        items_to_show = redraw()

        while True:
            ch = _read_key(fd)
            if ch == "\x03" or ch == "ESC":
                raise KeyboardInterrupt
            elif ch == "UP":
                selected_index = max(0, selected_index - 1)
                items_to_show = redraw()
            elif ch == "DOWN":
                selected_index = min(len(items_to_show) - 1, selected_index + 1)
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
        _exit_raw_input(fd, old_settings)


def interactive_selector(filtered_list, mode_str="", lite_mode=False, media_type="", hidden=None):
    """Pick one result. *hidden* holds (item, reason) pairs the filters removed; ``show:all`` lists them too."""
    hidden = list(hidden or ())
    hidden_reason = {id(item): reason for item, reason in hidden}
    fd = sys.stdin.fileno() if not IS_WINDOWS else None
    old_settings = termios.tcgetattr(fd) if not IS_WINDOWS else None
    current_input = ""
    selected_index = 0

    def redraw():
        nonlocal selected_index
        buf = []
        buf.append(T.HOME)

        logo_crlf = logo.replace("\n", f"{T.EOL}\r\n")
        buf.append(f"{T.LOGO}{logo_crlf}{T.RST}{T.EOL}\r\n")
        buf.append(f"{T.ACCENT}THE HELM - Torrent automation MVP{mode_str}{T.RST}{T.EOL}\r\n{T.EOL}\r\n")

        raw_terms = current_input.lower().split()
        title_terms = []
        cat_filter = None
        sort_key = None
        sort_desc = True
        show_all = False

        for term in raw_terms:
            if term == "show:all":
                show_all = True
            elif term.startswith("cat:"):
                cat_filter = term.split(":", 1)[1]
            elif term.startswith("sort:"):
                sort_val = term.split(":", 1)[1]
                if sort_val.startswith("-"):
                    sort_desc = False
                    sort_key = sort_val[1:]
                elif sort_val.endswith("-asc"):
                    sort_desc = False
                    sort_key = sort_val[:-4]
                else:
                    sort_desc = True
                    sort_key = sort_val
            else:
                title_terms.append(term)

        search_term = " ".join(title_terms)
        if search_term.startswith("/"):
            search_term = search_term[1:]

        disp_items = []
        source = list(filtered_list) + ([item for item, _ in hidden] if show_all else [])
        for t in source:
            if search_term and search_term not in t.title.lower():
                continue

            if cat_filter:
                item_cat = getattr(t, "media_type", None)
                if cat_filter == "generic":
                    if getattr(t, "score", 1) != 0 and item_cat is not None:
                        continue
                else:
                    if not item_cat or item_cat.lower() != cat_filter:
                        continue

            disp_items.append(t)

        if sort_key == "size":
            disp_items.sort(key=lambda x: float(getattr(x, "size", 0) or 0), reverse=sort_desc)
        elif sort_key == "seeds":
            disp_items.sort(key=lambda x: int(getattr(x, "seeders", 0) or 0), reverse=sort_desc)

        if selected_index >= len(disp_items):
            selected_index = max(0, len(disp_items) - 1)

        limit = 40
        start_idx, stop_idx = _paginate(len(disp_items), selected_index, limit)

        window_items = disp_items[start_idx:stop_idx]

        if hidden and not show_all:
            hidden_note = f" {T.MUTED}[{len(hidden)} hidden by filters, type show:all]{T.RST}"
        else:
            hidden_note = ""
        buf.append(
            f"{T.BOLD}{T.TEXT}Found {len(disp_items)} results (showing {start_idx + 1}-{start_idx + len(window_items)}):{T.RST}{hidden_note}{T.EOL}\r\n"
        )
        buf.append(T.LINE + "━" * 80 + f"{T.RST}{T.EOL}\r\n")

        for i, t in enumerate(window_items):
            actual_i = start_idx + i
            title = t.title
            score = getattr(t, "score", 1)
            item_type = getattr(t, "media_type", None)
            is_generic = score == 0

            # Only show tags for generic items, or items that have a media type (non-generic)
            reason = hidden_reason.get(id(t))
            tag = "[HIDDEN]" if reason else f"[{item_type.upper()}]" if item_type else "[GENERIC]"
            has_tag = reason or is_generic or item_type

            prefix_len = len(tag) + 1 if has_tag else 0
            max_title_len = 38 - prefix_len

            title = _truncate_to_width(title, max_title_len)

            visible_len = prefix_len + get_display_width(title)
            title_pad = max(2, 40 - visible_len)

            seeds = getattr(t, "seeders", 0)
            leechs = getattr(t, "leechers", 0)
            size_str = format_size(getattr(t, "size", 0))
            date_str = getattr(t, "pubdate", "") or "????-??-??"
            indexer = getattr(t, "indexer", "Unknown")
            if reason:
                indexer = f"{indexer} | {reason}"

            info_str = f"[{size_str:>9}] [{date_str:>10}] [{seeds:>4}↑ {leechs:>3}↓] [{indexer}]"

            if has_tag:
                title = f"{T.WARNING}{tag}{T.LOGO} {title}"

            if actual_i == selected_index:
                buf.append(
                    f"{T.REVERSE}{T.BOLD}{T.LOGO} ❯ {title}{T.RST}{T.REVERSE}{' ' * title_pad}{T.TEXT}{info_str}{T.RST}{T.EOL}\r\n"
                )
            else:
                buf.append(
                    f"{T.BOLD}{T.ACCENT}   {T.RST}{T.LOGO}{title}{T.RST}{' ' * title_pad}{T.BOLD}{T.TEXT}{info_str}{T.RST}{T.EOL}\r\n"
                )

        _append_scroll_footer(buf, len(disp_items), start_idx, stop_idx, limit)

        buf.append(T.LINE + "━" * 80 + f"{T.RST}{T.EOL}\r\n")

        if lite_mode:
            prompt = f"{T.BOLD}{T.TEXT}❯ Filter (cat:video, sort:size, show:all | Esc=Back, Ctrl-C=Exit):{T.RST} {current_input}"
        else:
            prompt = f"{T.BOLD}{T.TEXT}❯ Filter (cat:video, sort:size, show:all | Esc=Back, Ctrl-C=Exit, Ctrl-E=Teardown):{T.RST} {current_input}"

        buf.append(prompt + T.CLEAR_DOWN)
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        return disp_items

    try:
        _enter_raw_input(fd)
        items_to_show = redraw()

        while True:
            ch = _read_key(fd)
            if ch == "\x03":
                raise KeyboardInterrupt
            elif ch == "ESC":
                return None, "BACK"
            elif ch == "UP":
                selected_index = max(0, selected_index - 1)
                items_to_show = redraw()
            elif ch == "DOWN":
                selected_index = min(len(items_to_show) - 1, selected_index + 1)
                items_to_show = redraw()
            elif ch == "\x05":
                if items_to_show:
                    return items_to_show[selected_index], True
            elif ch in ("\r", "\n"):
                if items_to_show:
                    return items_to_show[selected_index], False
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
        _exit_raw_input(fd, old_settings)


def interactive_checkbox_selector(options, title_msg="Select categories", default_checked=None):
    if default_checked is None:
        default_checked = [0]

    fd = sys.stdin.fileno() if not IS_WINDOWS else None
    old_settings = termios.tcgetattr(fd) if not IS_WINDOWS else None
    selected_index = 0
    checked = set(default_checked)

    def redraw():
        buf = []
        buf.append(T.HOME)

        logo_crlf = logo.replace("\n", f"{T.EOL}\r\n")
        buf.append(f"{T.LOGO}{logo_crlf}{T.RST}{T.EOL}\r\n")
        buf.append(f"{T.ACCENT}THE HELM - Torrent automation MVP{T.RST}{T.EOL}\r\n{T.EOL}\r\n")

        buf.append(f"{T.BOLD}{T.TEXT}? {title_msg}:{T.RST}{T.EOL}\r\n")
        buf.append(T.LINE + "━" * 80 + f"{T.RST}{T.EOL}\r\n")

        for i, opt in enumerate(options):
            check_char = "x" if i in checked else " "
            prefix = f"{T.REVERSE}{T.BOLD}{T.LOGO} ❯ " if i == selected_index else f"{T.BOLD}{T.ACCENT}   "
            # if selected index we want to invert the colors for the whole line, including the text
            suffix = T.RST
            buf.append(f"{prefix}[{check_char}] {opt}{suffix}{T.EOL}\r\n")

        buf.append(T.LINE + "━" * 80 + f"{T.RST}{T.EOL}\r\n")
        buf.append(
            f"{T.BOLD}{T.TEXT}❯ (Arrows=Move, Space=Toggle, Enter=Confirm, Esc=Go Back, Ctrl-C=Exit){T.RST}{T.CLEAR_DOWN}"
        )

        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    try:
        _enter_raw_input(fd)
        redraw()
        while True:
            ch = _read_key(fd)
            if ch == "\x03":
                raise KeyboardInterrupt
            elif ch == "ESC":
                return "BACK"
            elif ch == "UP":
                selected_index = max(0, selected_index - 1)
                redraw()
            elif ch == "DOWN":
                selected_index = min(len(options) - 1, selected_index + 1)
                redraw()
            elif ch == " ":
                if selected_index in checked:
                    checked.remove(selected_index)
                else:
                    checked.add(selected_index)
                redraw()
            elif ch in ("\r", "\n"):
                if not checked:
                    checked.add(selected_index)
                return [options[i] for i in sorted(checked)]
    finally:
        _exit_raw_input(fd, old_settings)
