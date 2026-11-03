"""helm-torrent banner, and the header the interactive UI prints."""

import json
import sys

from helm.cli import deps
from helm.ui.ansi import T
from helm.ui.banner import WORDMARK as logo


def banner_context(args):
    if args.lite:
        return {"mode": "lite"}
    if args.oneshot:
        return {"mode": "one-shot"}
    return {}


def run(args):
    from helm.ui.ansi import color_enabled, render_banner
    from helm.ui.banner import build_banner
    from helm.ui.theme import load_theme

    settings = deps.load_settings()
    banner = build_banner(settings, banner_context(args))
    if args.json:
        data = {
            "theme": settings.get("theme.preset"),
            "logo": ["".join(text for _color, text in line) for line in banner.logo],
            "modules": [entry.__dict__ for entry in banner.entries],
        }
        print(json.dumps(data, ensure_ascii=False))
        return
    sys.stdout.write(render_banner(banner, load_theme(settings), color=color_enabled(sys.stdout)))


def print_header(args, subtitle):
    """Print the banner at UI start, or the plain wordmark when banner.enabled is false."""
    settings = deps.load_settings()
    if settings.get("banner.enabled"):
        from helm.ui.ansi import color_enabled, render_banner
        from helm.ui.banner import build_banner
        from helm.ui.theme import load_theme

        banner = build_banner(settings, banner_context(args))
        sys.stdout.write("\n" + render_banner(banner, load_theme(settings), color=color_enabled(sys.stdout)) + "\n")
    else:
        print(f"{T.LOGO}{logo}{T.RST}")
    print(f"{T.ACCENT}{subtitle}{T.RST}\n")
