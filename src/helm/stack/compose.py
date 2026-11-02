"""Render the one compose template that setup.sh and ``helm-torrent init`` share.

The rules match ``render_compose`` in setup.sh exactly, and a test checks that
both give the same output for every block combination:

- ``#@ if NAME`` ... ``#@ end`` keeps the lines between only when NAME is on.
  Blocks can nest.
- Any other line starting with ``#@`` is dropped.
- Each placeholder in ``PLACEHOLDERS`` is replaced literally.
"""

from pathlib import Path

TEMPLATE = Path(__file__).with_name("compose.template.yml")
BLOCKS = frozenset(["jackett", "prowlarr", "cli", "source", "vpn", "novpn"])
PLACEHOLDERS = ("HELM_STATE", "HELM_DL", "HELM_CONFIG", "ENGINE_SOCKET")


class TemplateError(ValueError):
    pass


def check_blocks(blocks):
    unknown = set(blocks) - BLOCKS
    if unknown:
        raise TemplateError(f"Unknown compose block(s): {', '.join(sorted(unknown))}")
    if ("vpn" in blocks) == ("novpn" in blocks):
        raise TemplateError("Choose exactly one of the vpn and novpn blocks")


def check_value(name, value):
    if any(char in value for char in '"\n\r'):
        raise TemplateError(f"{name} cannot contain quotes or line breaks: {value!r}")


def render(blocks, values, template=None):
    """Return the compose file for ``blocks`` with ``values`` filled in."""
    blocks = set(blocks)
    check_blocks(blocks)
    missing = [name for name in PLACEHOLDERS if name not in values]
    if missing:
        raise TemplateError(f"Missing compose value(s): {', '.join(missing)}")
    for name in PLACEHOLDERS:
        check_value(name, values[name])

    text = TEMPLATE.read_text(encoding="utf-8") if template is None else template
    stack = []
    out = []
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        marker = line.strip()
        if marker.startswith("#@ if "):
            stack.append(marker.split()[2] in blocks)
            continue
        if marker.startswith("#@ end"):
            if not stack:
                raise TemplateError(f"Unmatched '#@ end' on line {number}")
            stack.pop()
            continue
        if marker.startswith("#@"):
            continue
        if all(stack):
            for name in PLACEHOLDERS:
                line = line.replace(f"@{name}@", values[name])
            out.append(line)
    if stack:
        raise TemplateError("Unclosed '#@ if' block in the compose template")
    return "".join(out)
