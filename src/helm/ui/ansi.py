"""ANSI renderer for banner data and theme colours. The only place that writes escapes."""

import os
import unicodedata

from helm.ui.banner import SGR

RESET = "\033[0m"
BOLD = "\033[1m"
GAP = "   "


def sgr(hex_color):
    """Return the 24-bit foreground escape for a #RRGGBB colour."""
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return f"\033[38;2;{red};{green};{blue}m"


def color_enabled(stream):
    """Follow NO_COLOR and FORCE_COLOR, and otherwise colour only terminals."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def visible_width(text):
    """Terminal columns used by text, ignoring SGR codes and counting wide characters twice."""
    width = 0
    for character in SGR.sub("", text):
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
    return width


class Painter:
    def __init__(self, theme, color):
        self.theme = theme
        self.color = color

    def paint(self, name, text, bold=False):
        if not self.color or not text:
            return text
        return (BOLD if bold else "") + sgr(self.theme.get(name)) + text + RESET

    def raw(self, text):
        return text + RESET if self.color and SGR.search(text) else (text if self.color else SGR.sub("", text))


def _logo_lines(banner, painter):
    lines = []
    for segments in banner.logo:
        parts = [painter.raw(text) if name is None else painter.paint(name, text) for name, text in segments]
        lines.append("".join(parts))
    return lines


def _entry_lines(banner, painter):
    labelled = [entry for entry in banner.entries if entry.status != "separator"]
    label_width = max((visible_width(entry.label) for entry in labelled), default=0)
    text_width = max((label_width + 2 + visible_width(entry.value) for entry in labelled), default=0)
    lines = []
    for entry in banner.entries:
        if entry.status == "separator":
            lines.append(painter.paint("line", "─" * max(text_width, 8)))
            continue
        padding = " " * (label_width - visible_width(entry.label))
        value_color = {"timeout": "muted", "error": "error"}.get(entry.status, "text")
        lines.append(
            painter.paint("accent", entry.label, bold=True) + padding + "  " + painter.paint(value_color, entry.value)
        )
    return lines


def render_banner(banner, theme, color=True):
    """Return the banner as text, logo left of or above the module lines."""
    painter = Painter(theme, color)
    logo = _logo_lines(banner, painter)
    entries = _entry_lines(banner, painter)
    if banner.position == "top" or not logo or not entries:
        lines = logo + ([""] if logo and entries else []) + entries
    else:
        width = max(visible_width(line) for line in logo)
        height = max(len(logo), len(entries))
        lines = []
        for index in range(height):
            left = logo[index] if index < len(logo) else ""
            right = entries[index] if index < len(entries) else ""
            if right:
                left += " " * (width - visible_width(left)) + GAP
            lines.append(left + right)
    return "\n".join(lines) + "\n" if lines else ""
