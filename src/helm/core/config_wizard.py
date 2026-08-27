import os
import sys

import requests

from helm.core.config_manager import load_config, save_config
from helm.core.secret_manager import get_secret, set_secrets


def run_wizard():
    import sys

    try:
        print("\033[1mWelcome to Helm Interactive Configuration Wizard!\033[0m")
        print("It looks like some essential configurations are missing.\n")

        config = load_config()

        print("You can run Helm in two modes:")
        print("  1. Lite Mode (Native plugins only, no media server required)")
        print("  2. Full Automation (Requires Jackett & qBittorrent running)")

        choice = (
            input("\nDo you want to configure Jackett and qBittorrent for full automation? (y/N): ").strip().lower()
        )
        if choice not in ("y", "yes"):
            print("\n\033[32mOpting for Lite Mode. You can change this later.\033[0m\n")
            config["LITE_MODE_ONLY"] = True
            save_config(config)
            return

        config["LITE_MODE_ONLY"] = False

        # Jackett
        jackett_url = config.get("JACKETT_URL", os.getenv("JACKETT_URL", "http://localhost:9117"))
        jackett_api = get_secret("JACKETT_API_KEY") or ""
        jackett_pwd = get_secret("JACKETT_PASSWORD") or ""

        while True:
            jackett_url_input = input(f"Jackett URL [{jackett_url}]: ").strip()
            if jackett_url_input.lower() in ("exit", "quit", "q") or "\x03" in jackett_url_input:
                print("\n\033[33mConfiguration aborted. later bozo!\033[0m")
                sys.exit(0)
            if jackett_url_input:
                jackett_url = jackett_url_input

            # Obscure the API key if it exists
            masked_api = (
                f"{jackett_api[:4]}...{jackett_api[-4:]}" if len(jackett_api) > 8 else "***" if jackett_api else ""
            )
            jackett_api_input = input(f"Jackett API Key [{masked_api}]: ").strip()
            if jackett_api_input.lower() in ("exit", "quit", "q") or "\x03" in jackett_api_input:
                print("\n\033[33mConfiguration aborted. later bozo!\033[0m")
                sys.exit(0)
            if jackett_api_input:
                jackett_api = jackett_api_input

            jackett_pwd_input = input(
                f"Jackett Admin Password (leave blank if none) [{'***' if jackett_pwd else ''}]: "
            ).strip()
            if jackett_pwd_input.lower() in ("exit", "quit", "q") or "\x03" in jackett_pwd_input:
                print("\n\033[33mConfiguration aborted. later bozo!\033[0m")
                sys.exit(0)
            if jackett_pwd_input:
                jackett_pwd = jackett_pwd_input
            # To allow clearing the password if one was set, we could allow a special string, but for now we just take the input if truthy or keep it if they just pressed enter.

            if jackett_api:
                # Validate Jackett
                print("Validating Jackett connection...")
                try:
                    r = requests.get(
                        f"{jackett_url}/api/v2.0/indexers/all/results/torznab/api?apikey={jackett_api}&t=indexers",
                        timeout=5,
                    )
                    if r.status_code == 200:
                        # Also test if password works for UI APIs
                        session = requests.Session()
                        r_auth = session.post(f"{jackett_url}/UI/Dashboard", data={"password": jackett_pwd})
                        if r_auth.status_code in (200, 302):
                            # Try to fetch a config schema to ensure auth worked (or it returns HTML)
                            r_conf = session.get(f"{jackett_url}/api/v2.0/indexers/3dtorrents/config")
                            if r_conf.status_code == 200 and r_conf.text.startswith("["):
                                print("Jackett connection and authentication successful!\n")
                                break
                            else:
                                print("Jackett API key works, but Admin Password appears to be incorrect.\n")
                        else:
                            print(f"Jackett auth failed: HTTP {r_auth.status_code}\n")
                    else:
                        print(f"Jackett connection failed: HTTP {r.status_code}\n")
                except Exception as e:
                    print(f"Jackett connection failed: {e}\n")
            else:
                print("Jackett API key is required.\n")

        config["JACKETT_URL"] = jackett_url
        set_secrets({"JACKETT_API_KEY": jackett_api, "JACKETT_PASSWORD": jackett_pwd})

        # qBittorrent
        qb_webui = config.get("QB_WEBUI", os.getenv("QB_WEBUI", "http://localhost:18080"))
        qb_username = config.get("QB_USERNAME", os.getenv("QB_USERNAME", "admin"))
        qb_password = get_secret("QB_PASSWORD") or ""

        while True:
            qb_webui_input = input(f"qBittorrent WebUI URL [{qb_webui}]: ").strip()
            if qb_webui_input.lower() in ("exit", "quit", "q") or "\x03" in qb_webui_input:
                print("\n\033[33mConfiguration aborted. later bozo!\033[0m")
                sys.exit(0)
            if qb_webui_input:
                qb_webui = qb_webui_input

            qb_username_input = input(f"qBittorrent Username [{qb_username}]: ").strip()
            if qb_username_input.lower() in ("exit", "quit", "q") or "\x03" in qb_username_input:
                print("\n\033[33mConfiguration aborted. later bozo!\033[0m")
                sys.exit(0)
            if qb_username_input:
                qb_username = qb_username_input

            qb_password_input = input(f"qBittorrent Password [{'***' if qb_password else ''}]: ").strip()
            if qb_password_input.lower() in ("exit", "quit", "q") or "\x03" in qb_password_input:
                print("\n\033[33mConfiguration aborted. later bozo!\033[0m")
                sys.exit(0)
            if qb_password_input:
                qb_password = qb_password_input

            if qb_password:
                # Validate qBittorrent
                print("Validating qBittorrent connection...")
                try:
                    session = requests.Session()
                    r = session.post(
                        f"{qb_webui}/api/v2/auth/login",
                        data={"username": qb_username, "password": qb_password},
                        timeout=5,
                    )
                    if r.status_code in (200, 204):
                        try:
                            r_verify = session.get(f"{qb_webui}/api/v2/app/version", timeout=5)
                            if r_verify.status_code == 200:
                                print("qBittorrent connection successful!\n")
                                break
                        except Exception:
                            pass
                    print("qBittorrent connection failed or auth rejected.\n")
                except Exception as e:
                    print(f"qBittorrent connection failed: {e}\n")
            else:
                print("qBittorrent password is required.\n")

        config["QB_WEBUI"] = qb_webui
        config["QB_USERNAME"] = qb_username
        set_secrets({"QB_PASSWORD": qb_password})

        save_config(config)
        print("Configuration saved! (settings -> config.json, secrets -> secrets.env)\n")
    except KeyboardInterrupt:
        print("\n\n\033[33mConfiguration aborted. later bozo!\033[0m")
        sys.exit(0)
    except EOFError:
        print(
            "\n\n\033[31m[ERROR] Cannot read input. If you are running via Docker, make sure to use the '-it' flag for interactive mode, or mount your configuration files!\033[0m"
        )
        sys.exit(1)


def ensure_config():
    config = load_config()

    if config.get("LITE_MODE_ONLY"):
        return

    needs_wizard = False
    # Check required settings (secrets are resolved via the secret store)
    keys = ["JACKETT_URL", "QB_WEBUI", "QB_USERNAME"]
    for k in keys:
        if k not in config and not os.getenv(k):
            needs_wizard = True
            break
    for k in ["JACKETT_API_KEY", "QB_PASSWORD"]:
        if not needs_wizard and not get_secret(k):
            needs_wizard = True
            break

    # Dynamically check if JACKETT_PASSWORD is required
    if not needs_wizard and not get_secret("JACKETT_PASSWORD"):
        jackett_url = config.get("JACKETT_URL", os.getenv("JACKETT_URL", "http://localhost:9117"))
        try:
            session = requests.Session()
            r = session.get(f"{jackett_url}/UI/Dashboard", timeout=2)
            if "/UI/Login" in r.url:
                needs_wizard = True
        except Exception:
            pass

    if needs_wizard:
        if not sys.stdin.isatty():
            print(
                "\n\033[33m[WARN] Missing configuration keys, but running non-interactively (e.g. Docker). Skipping interactive wizard.\033[0m\n"
            )
            # We don't exit here so the rest of the app can try to run, or fail with a native API error
        else:
            run_wizard()
            config = load_config()

    for k in keys:
        if k in config:
            os.environ[k] = config[k]

    # Push all resolved secrets into the environment so native modules can use them.
    from helm.core.config_manager import SECRET_KEYS

    for k in SECRET_KEYS:
        value = get_secret(k)
        if value:
            os.environ[k] = value


if __name__ == "__main__":
    run_wizard()
