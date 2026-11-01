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
import re
import tempfile
from urllib.parse import quote, quote_plus

from helm.core.config_manager import SECRET_KEYS, get_config_dir

_SECRETS_CACHE = None
_SECRETS_CACHE_PATH = None


def get_secrets_file():
    return os.path.join(get_config_dir(), "secrets.env")


def _ensure_parent_dir():
    os.makedirs(get_config_dir(), exist_ok=True)


def load_secrets():
    """Return a dict of all stored secrets, reading the file fresh if not cached."""
    global _SECRETS_CACHE, _SECRETS_CACHE_PATH
    secrets_path = get_secrets_file()
    if _SECRETS_CACHE is not None and _SECRETS_CACHE_PATH == secrets_path:
        return dict(_SECRETS_CACHE)

    _SECRETS_CACHE_PATH = secrets_path
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

    Writes atomically (temp file + os.replace) so a crash can never truncate the
    store, and enforces mode 0600 regardless of any pre-existing file permissions.
    """
    secrets = load_secrets()
    secrets.update({k: str(v) for k, v in mapping.items() if v is not None})

    _ensure_parent_dir()
    secrets_path = get_secrets_file()

    content = "\n".join(f"{k}={v}" for k, v in sorted(secrets.items()))
    if content:
        content += "\n"

    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(secrets_path), prefix=".secrets-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # mkstemp creates 0600, but chmod again in case umask interfered
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, secrets_path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
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


def redact_text(value):
    """Redact known secret values, URL credentials, query keys and auth headers."""
    text = str(value)
    secrets = list(load_secrets().values()) + [os.environ.get(key, "") for key in SECRET_KEYS]
    encoded = {
        variant for secret in secrets if secret for variant in (secret, quote(secret, safe=""), quote_plus(secret))
    }
    for secret in sorted(encoded, key=len, reverse=True):
        text = text.replace(secret, "***REDACTED***")
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1***REDACTED***@", text, flags=re.I)
    text = re.sub(r"(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+", r"\1***REDACTED***", text, flags=re.I)
    text = re.sub(
        r"((?:[\w-]*(?:api[_-]?key|password|passwd|secret|token))[\"']?\s*[:=]\s*[\"']?)([^\s&\"',;)}]+)",
        r"\1***REDACTED***",
        text,
        flags=re.I,
    )
    return text


def redact_config(config):
    """Redact nested settings and credential-bearing URLs without changing input."""

    def clean(value):
        if isinstance(value, dict):
            return {
                key: "***REDACTED***"
                if re.search(r"api.?key|password|passwd|secret|token", str(key), re.I)
                else clean(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return redact_text(value) if isinstance(value, str) else value

    return clean(config)


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
