import re

with open("setup.sh", "r") as f:
    content = f.read()

# Replace mkdir -p docker_data...
content = re.sub(
    r"mkdir -p docker_data/jackett docker_data/qbittorrent docker_data/downloads\n(?:touch docker_data/config\.json\n)?",
    'HELM_STATE="$HOME/.local/state/helm"\nHELM_DL="$HOME/Downloads/helm"\nmkdir -p "$HELM_STATE/jackett" "$HELM_STATE/qbittorrent" "$HELM_STATE/gluetun" "$HELM_DL"\n',
    content,
)

# Replace ${HOST_PWD:-.}/docker_data/downloads:/downloads with ${HELM_DL}:/downloads
content = content.replace("${HOST_PWD:-.}/docker_data/downloads:/downloads", "${HOME}/Downloads/helm:/downloads")

# Replace ${HOST_PWD:-.}/docker_data/config.json with config in ~/.config/helm
content = content.replace(
    "${HOST_PWD:-.}/docker_data/config.json:/app/config.json", "${HOME}/.config/helm/config.json:/app/config.json"
)

# Replace ${HOST_PWD:-.}/docker_data with ${HOME}/.local/state/helm in docker-compose.yml
content = content.replace("${HOST_PWD:-.}/docker_data", "${HOME}/.local/state/helm")

# Replace all other ./docker_data with $HELM_STATE
content = content.replace("./docker_data", '"$HELM_STATE"')

with open("setup.sh", "w") as f:
    f.write(content)
print("patched!")
