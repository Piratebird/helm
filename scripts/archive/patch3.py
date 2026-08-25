import re

with open("setup.sh", "r") as f:
    content = f.read()

os_detect_block = """OS="$(uname -s)"
case "${OS}" in
    Darwin*)
        DEFAULT_CONFIG="$HOME/Library/Application Support/helm"
        DEFAULT_STATE="$HOME/Library/Application Support/helm/state"
        ;;
    MINGW*|CYGWIN*|MSYS*)
        DEFAULT_CONFIG="${APPDATA:-$HOME/AppData/Roaming}/helm"
        DEFAULT_STATE="${LOCALAPPDATA:-$HOME/AppData/Local}/helm"
        ;;
    *)
        DEFAULT_CONFIG="$HOME/.config/helm"
        DEFAULT_STATE="$HOME/.local/state/helm"
        ;;
esac

HELM_CONFIG="${DEFAULT_CONFIG}"
HELM_STATE="${DEFAULT_STATE}"
HELM_DL="$HOME/Downloads/helm"
"""

content = re.sub(
    r'HELM_CONFIG="\$HOME/\.config/helm"\nHELM_STATE="\$HOME/\.local/state/helm"\nHELM_DL="\$HOME/Downloads/helm"\n',
    os_detect_block,
    content
)

with open("setup.sh", "w") as f:
    f.write(content)
