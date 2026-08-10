import os
import subprocess
import time
import sys

def spin_up_oneshot():
    print("\n\033[1m\033[36mInitializing One-Shot Ephemeral Stack...\033[0m")
    print("\033[3mBringing up Jackett, Flaresolverr, and qBittorrent via docker compose...\033[0m")
    
    try:
        subprocess.run(["docker", "compose", "up", "-d"], check=True)
    except subprocess.CalledProcessError:
        print("\033[31mFailed to start docker-compose stack. Make sure Docker is running.\033[0m")
        sys.exit(1)
        
    def check_container_status(service_name, timeout=45):
        print(f"\033[3mWaiting for {service_name} to stabilize...\033[0m")
        elapsed = 0
        while elapsed < timeout:
            try:
                # Get container ID
                cid = subprocess.check_output(["docker", "compose", "ps", "-q", service_name], text=True).strip()
                if cid:
                    # Check status
                    status = subprocess.check_output(["docker", "inspect", "-f", "{{.State.Status}}", cid], text=True).strip()
                    if status == "running":
                        return True
                    elif status == "exited":
                        print(f"\033[31m[ERROR] Container {service_name} crashed immediately. Check 'docker compose logs {service_name}'\033[0m")
                        subprocess.run(["docker", "compose", "down"])
                        sys.exit(1)
            except Exception:
                pass
            time.sleep(5)
            elapsed += 5
        print(f"\033[31m[WARNING] Timeout waiting for {service_name} to stabilize.\033[0m")
        return False
        
    check_container_status("jackett")
    check_container_status("qbittorrent")
        
    print("\033[33mWaiting for Jackett to generate API Key...\033[0m")
    jackett_api = None
    config_path = "./docker_data/jackett/Jackett/ServerConfig.json"
    for _ in range(15):
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    for line in f:
                        if '"APIKey"' in line:
                            jackett_api = line.split('"')[3]
                            break
            except Exception:
                pass
        if jackett_api:
            break
        time.sleep(3)
        
    if not jackett_api:
        print("\033[31mCould not extract Jackett API Key.\033[0m")
        subprocess.run(["docker", "compose", "down"])
        sys.exit(1)
        
    os.environ["JACKETT_API_KEY"] = jackett_api
    os.environ["JACKETT_URL"] = "http://localhost:19117"
    
    import core.rss_fetcher
    core.rss_fetcher.API_KEY = jackett_api
    core.rss_fetcher.JACKETT_URL = "http://localhost:19117"
    
    print("\033[32mJackett API Key extracted and injected.\033[0m")
    
    print("\033[33mWaiting for qBittorrent password...\033[0m")
    qb_pass = None
    for _ in range(15):
        try:
            logs = subprocess.check_output(["docker", "compose", "logs", "qbittorrent"], text=True)
            for line in logs.split('\n'):
                if "temporary password is provided for this session" in line.lower():
                    qb_pass = line.split("session: ")[-1].strip()
                    break
        except Exception:
            pass
        if qb_pass:
            break
        time.sleep(3)
        
    if not qb_pass:
        qb_pass = "adminadmin"
        
    os.environ["QB_PASSWORD"] = qb_pass
    os.environ["QB_USERNAME"] = "admin"
    os.environ["QB_WEBUI"] = "http://localhost:18080"
    
    # Patch module variables in case they were already loaded by __main__
    import core.qbittorrent_client
    core.qbittorrent_client.QB_PASSWORD = qb_pass
    core.qbittorrent_client.QB_USERNAME = "admin"
    core.qbittorrent_client.QB_WEBUI = "http://localhost:18080"
    
    print("\033[32mqBittorrent credentials secured.\033[0m\n")


def teardown_oneshot():
    print("\n\033[1m\033[33mTearing down the ephemeral stack to save processing power...\033[0m")
    try:
        subprocess.run(["docker", "compose", "down"], check=True)
        print("\033[1m\033[32mAll containers destroyed cleanly. Your RAM is free!\033[0m")
    except Exception as e:
        print(f"\033[31mCould not tear down docker-compose stack: {e}\033[0m")


def wait_for_download():
    print("\n\033[36mWaiting for download to complete before tearing down the stack...\033[0m")
    
    import core.qbittorrent_client as qbc
    session = qbc.login_qbittorrent()
    time.sleep(5)  # give qbittorrent time to parse the magnet metadata
    
    while True:
        try:
            r = session.get(f"{qbc.QB_WEBUI}/api/v2/torrents/info")
            if r.status_code == 200:
                torrents = r.json()
                if not torrents:
                    time.sleep(3)
                    continue
                    
                all_done = True
                for t in torrents:
                    progress = t.get("progress", 0.0)
                    name = t.get("name", "Unknown")
                    if progress < 1.0:
                        all_done = False
                        print(f"\r\033[33mDownloading '{name[:40]}...' ({progress*100:.1f}%) \033[0m", end="")
                        sys.stdout.flush()
                
                if all_done:
                    print("\n\033[1m\033[32mDownload(s) 100% complete!\033[0m")
                    break
        except Exception:
            pass
            
        time.sleep(3)
