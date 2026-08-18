def hex_to_ansi(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"\033[38;2;{r};{g};{b}m"

class Palette:
    # change color pallete or move to textual idk vro
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

logo = r"""
▗▖ ▗▖▗▄▄▄▖▗▖   ▗▖  ▗▖
▐▌ ▐▌▐▌   ▐▌   ▐▛▚▞▜▌
▐▛▀▜▌▐▛▀▀▘▐▌   ▐▌  ▐▌
▐▌ ▐▌▐▙▄▄▖▐▙▄▄▖▐▌  ▐▌
"""
