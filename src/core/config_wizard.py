import os
import requests
from core.config_manager import load_config, save_config

def run_wizard():
    import sys
    try:
        print("\033[1mWelcome to Helm Interactive Configuration Wizard!\033[0m")
        print("It looks like some essential configurations are missing.\n")
        
        config = load_config()
        
        # Jackett
        jackett_url = config.get("JACKETT_URL", os.getenv("JACKETT_URL", "http://localhost:9117"))
        jackett_api = config.get("JACKETT_API_KEY", os.getenv("JACKETT_API_KEY", ""))
        
        while True:
            jackett_url_input = input(f"Jackett URL [{jackett_url}]: ").strip()
            if jackett_url_input: jackett_url = jackett_url_input
            
            jackett_api_input = input(f"Jackett API Key [{jackett_api}]: ").strip()
            if jackett_api_input: jackett_api = jackett_api_input
            
            if jackett_api:
                # Validate Jackett
                print("Validating Jackett connection...")
                try:
                    r = requests.get(f"{jackett_url}/api/v2.0/indexers/all/results/torznab/api?apikey={jackett_api}&t=search&q=test", timeout=5)
                    if r.status_code == 200:
                        print("Jackett connection successful!\n")
                        break
                    else:
                        print(f"Jackett connection failed: HTTP {r.status_code}\n")
                except Exception as e:
                    print(f"Jackett connection failed: {e}\n")
            else:
                print("Jackett API key is required.\n")
                
        config["JACKETT_URL"] = jackett_url
        config["JACKETT_API_KEY"] = jackett_api
        
        # qBittorrent
        qb_webui = config.get("QB_WEBUI", os.getenv("QB_WEBUI", "http://localhost:18080"))
        qb_username = config.get("QB_USERNAME", os.getenv("QB_USERNAME", "admin"))
        qb_password = config.get("QB_PASSWORD", os.getenv("QB_PASSWORD", ""))
        
        while True:
            qb_webui_input = input(f"qBittorrent WebUI URL [{qb_webui}]: ").strip()
            if qb_webui_input: qb_webui = qb_webui_input
            
            qb_username_input = input(f"qBittorrent Username [{qb_username}]: ").strip()
            if qb_username_input: qb_username = qb_username_input
            
            qb_password_input = input(f"qBittorrent Password [{'***' if qb_password else ''}]: ").strip()
            if qb_password_input: qb_password = qb_password_input
            
            if qb_password:
                # Validate qBittorrent
                print("Validating qBittorrent connection...")
                try:
                    session = requests.Session()
                    r = session.post(f"{qb_webui}/api/v2/auth/login", data={"username": qb_username, "password": qb_password}, timeout=5)
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
        config["QB_PASSWORD"] = qb_password
        
        save_config(config)
        print("Configuration saved to config.json!\n")
    except KeyboardInterrupt:
        print("\n\n\033[33mConfiguration aborted. later bozo!\033[0m")
        sys.exit(0)
    except EOFError:
        print("\n\n\033[31m[ERROR] Cannot read input. If you are running via Docker, make sure to use the '-it' flag for interactive mode, or mount your configuration files!\033[0m")
        sys.exit(1)


def ensure_config():
    config = load_config()
    needs_wizard = False
    
    keys = ["JACKETT_URL", "JACKETT_API_KEY", "QB_WEBUI", "QB_USERNAME", "QB_PASSWORD"]
    for k in keys:
        if k not in config and not os.getenv(k):
            needs_wizard = True
            break
            
    if needs_wizard:
        run_wizard()
        config = load_config()
        
    for k in keys:
        if k in config:
            os.environ[k] = config[k]

if __name__ == "__main__":
    run_wizard()

