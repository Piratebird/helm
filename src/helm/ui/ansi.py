"""ANSI renderer for banner data and theme colours. The only place that writes escapes."""

import os
import sys
import unicodedata

from helm.ui.banner import SGR

RESET = "\033[0m"
BOLD = "\033[1m"
GAP = "   "


# Terminal controls. They are not colours, so they stay on when colour is off.
EOL = "\033[K"  # Clear to the end of the line
HOME = "\033[H"
CLEAR = "\033[2J"
CLEAR_DOWN = "\033[J"
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"
ITALIC = "\033[3m"
REVERSE = "\033[7m"
CONTROLS = {
    "EOL": EOL,
    "HOME": HOME,
    "CLEAR": CLEAR,
    "CLEAR_DOWN": CLEAR_DOWN,
    "HIDE_CURSOR": HIDE_CURSOR,
    "SHOW_CURSOR": SHOW_CURSOR,
}
STYLES = {"RST": RESET, "BOLD": BOLD, "ITALIC": ITALIC, "REVERSE": REVERSE}


def sgr(color):
    """Return the foreground escape for "#RRGGBB" or a basic "ansi:NN" colour."""
    if color.startswith("ansi:"):
        return f"\033[{int(color[5:])}m"
    value = color.lstrip("#")
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


class Terminal:
    """Named escape codes for the current theme, resolved on first use.

    Use it as T.LOGO, T.ACCENT, T.BOLD, T.RST, T.EOL and so on. Colours and
    styles are empty when stdout is not a terminal or NO_COLOR is set. The theme
    is read lazily, after the CLI has applied --config-dir and similar flags.
    """

    def __init__(self):
        self._codes = None

    def _build(self):
        from helm.config.settings import COLOR_NAMES, ConfigError, Settings, load_settings
        from helm.ui.theme import load_theme

        try:
            theme = load_theme(load_settings())
        except ConfigError:
            # A broken config is reported elsewhere; colours must not crash.
            theme = load_theme(Settings())
        color = color_enabled(sys.stdout)
        codes = dict(CONTROLS)
        codes.update({name: code if color else "" for name, code in STYLES.items()})
        codes.update({name.upper(): sgr(theme.get(name)) if color else "" for name in COLOR_NAMES})
        return codes

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._codes is None:
            self._codes = self._build()
        try:
            return self._codes[name]
        except KeyError:
            raise AttributeError(name) from None

    def reset(self):
        """Forget the resolved theme, so the next use reads settings again."""
        self._codes = None


T = Terminal()
