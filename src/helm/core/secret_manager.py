"""
core/secret_manager.py

Central, secure storage for Helm credentials.

Secrets (API keys, passwords) are never written to config.json. Instead they
live in a dedicated, gitignored, 0600-permission file:

    <config_dir>/secrets.env

Resolution order for every secret:
    1. Existing environment variable (Docker env_file, shell, one-shot, etc.)
    2. The secrets.env file
    3. Any value legacy-carried inside config.json (auto-migrated once)

Values set through the interactive wizard are written straight to secrets.env.
"""

import os

from helm.core.config_manager import SECRET_KEYS, get_config_dir

_SECRETS_CACHE = None


def get_secrets_file():
    return os.path.join(get_config_dir(), "secrets.env")


def _ensure_parent_dir():
    os.makedirs(get_config_dir(), exist_ok=True)


def load_secrets():
    """Return a dict of all stored secrets, reading the file fresh if not cached."""
    global _SECRETS_CACHE
    if _SECRETS_CACHE is not None:
        return dict(_SECRETS_CACHE)

    secrets_path = get_secrets_file()
    if not os.path.exists(secrets_path):
        _SECRETS_CACHE = {}
        return {}

    parsed = {}
    try:
        with open(secrets_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key:
                    parsed[key] = value
    except OSError:
        parsed = {}

    _SECRETS_CACHE = parsed
    return dict(parsed)


def _invalidate_cache():
    global _SECRETS_CACHE
    _SECRETS_CACHE = None


def get_secret(key, default=None):
    """
    Resolve a single secret. Priority: environment variable > secrets.env.
    """
    env_value = os.environ.get(key)
    if env_value:
        return env_value
    return load_secrets().get(key, default)


def set_secrets(mapping):
    """
    Merge *mapping* into secrets.env, preserving any existing secrets.
    Creates the file with 0600 permissions if it does not exist yet.
    """
    secrets = load_secrets()
    secrets.update({k: str(v) for k, v in mapping.items() if v is not None})

    _ensure_parent_dir()
    secrets_path = get_secrets_file()

    content = "\n".join(f"{k}={v}" for k, v in sorted(secrets.items()))
    if content:
        content += "\n"

    fd = os.open(secrets_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    finally:
        _invalidate_cache()


def migrate_config_secrets(config):
    """
    Pull known secret keys out of a loaded config dict into secrets.env (only if
    they are not already known), and return the config with those keys removed.

    This is the one-way bridge for users who previously had keys in config.json.
    """
    if not config:
        return config

    pending = {}
    for key in SECRET_KEYS:
        if key in config and not os.environ.get(key) and not load_secrets().get(key):
            pending[key] = config[key]

    if pending:
        set_secrets(pending)

    for key in SECRET_KEYS:
        config.pop(key, None)

    return config


def redact_config(config):
    """Return a copy of *config* with every secret value replaced."""
    redacted = dict(config)
    for key in SECRET_KEYS:
        if key in redacted:
            redacted[key] = "***REDACTED***"
    return redacted


def sanitize_link(link):
    """
    Strip the jackett_apikey query parameter from a `.../dl/...` link so secrets
    do not leak into JSON stdout output.
    """
    if not link:
        return link
    if "jackett_apikey=" not in link:
        return link
    head, _, tail = link.partition("?")
    params = [p for p in tail.split("&") if not p.startswith("jackett_apikey=")]
    if not params:
        return head
    return f"{head}?{'&'.join(params)}"
