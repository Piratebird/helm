"""Colour themes as plain hex values. Renderers turn them into ANSI or Textual styles.

Every preset defines the same names, so modules and renderers never depend on a
particular theme. The upstream sources were checked on 2026-11-02:

- tokyo-night: folke/tokyonight.nvim, lua/tokyonight/colors/storm.lua (night shares these)
- nord: nordtheme/nord, src/nord.css
- catppuccin-mocha: catppuccin/palette, palette.json
"""

from dataclasses import dataclass, field
from typing import Dict

from helm.core.settings import COLOR_NAMES, HELM_COLORS

PRESETS: Dict[str, Dict[str, str]] = {
    # The colours the current UI hardcodes in helm.ui.colors, so the default look stays the same.
    "helm": dict(HELM_COLORS),
    "tokyo-night": {
        "logo": "#7aa2f7",  # blue
        "accent": "#bb9af7",  # magenta
        "text": "#c0caf5",  # fg
        "line": "#ff9e64",  # orange
        "error": "#f7768e",  # red
        "muted": "#565f89",  # comment
    },
    "nord": {
        "logo": "#88c0d0",  # nord8
        "accent": "#b48ead",  # nord15
        "text": "#d8dee9",  # nord4
        "line": "#d08770",  # nord12
        "error": "#bf616a",  # nord11
        "muted": "#4c566a",  # nord3
    },
    "catppuccin-mocha": {
        "logo": "#89b4fa",  # blue
        "accent": "#cba6f7",  # mauve
        "text": "#cdd6f4",  # text
        "line": "#fab387",  # peach
        "error": "#f38ba8",  # red
        "muted": "#6c7086",  # overlay0
    },
}


@dataclass(frozen=True)
class Theme:
    name: str
    colors: Dict[str, str] = field(default_factory=dict)

    def get(self, name):
        return self.colors[name]


def load_theme(settings):
    """Build the theme from theme.preset plus any single-colour overrides."""
    name = settings.get("theme.preset")
    colors = dict(PRESETS[name])
    for color in COLOR_NAMES:
        override = settings.get(f"theme.{color}")
        if override:
            colors[color] = override
    return Theme(name, colors)
