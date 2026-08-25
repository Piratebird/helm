import os
import sys
import time


def download_magnet(magnet_link, save_path="./downloads"):
    try:
        import libtorrent as lt
    except ImportError:
        print("\n\033[33m[!] libtorrent is not installed.\033[0m")
        print("To use the built-in lite downloader, please install it (e.g., 'pip install libtorrent').")
        print(f"\n\033[1mMagnet Link:\033[0m {magnet_link}\n")
        print("Copy the above link and paste it into your torrent client.")
        return False

    if not os.path.exists(save_path):
        os.makedirs(save_path)

    settings = {
        "listen_interfaces": "0.0.0.0:0",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
        "announce_to_all_trackers": True,
        "announce_to_all_tiers": True,
    }
    ses = lt.session(settings)

    # Add common DHT routers to bootstrap finding peers quickly
    ses.add_dht_router("router.bittorrent.com", 6881)
    ses.add_dht_router("router.utorrent.com", 6881)
    ses.add_dht_router("dht.transmissionbt.com", 6881)
    ses.add_dht_router("dht.aelitis.com", 6881)

    print("\n\033[1m\033[34mAdding magnet link to libtorrent session...\033[0m")

    # Parse the magnet URI properly for libtorrent 2.x
    params = lt.parse_magnet_uri(magnet_link)
    params.save_path = save_path
    handle = ses.add_torrent(params)

    sys.stdout.write("Downloading Metadata")

    try:
        timeout = 60
        elapsed = 0
        while not handle.status().has_metadata:
            time.sleep(1)
            sys.stdout.write(".")
            sys.stdout.flush()
            elapsed += 1
            if elapsed >= timeout:
                print("\n\n\033[31m[!] Timeout reaching metadata. The torrent might be dead or blocked.\033[0m")
                print(f"Fallback Magnet Link: {magnet_link}")
                ses.pause()
                return False

        print("\nMetadata downloaded. Starting torrent download...")
        print(f"Saving to: \033[1m{os.path.abspath(save_path)}\033[0m\n")

        while handle.status().state != lt.torrent_status.seeding:
            s = handle.status()

            state_str = [
                "queued",
                "checking",
                "downloading metadata",
                "downloading",
                "finished",
                "seeding",
                "allocating",
            ]

            sys.stdout.write(
                f"\r\033[K\033[1m{s.progress * 100:.2f}%\033[0m complete "
                f"(down: \033[32m{s.download_rate / 1000:.1f} kB/s\033[0m | "
                f"up: \033[31m{s.upload_rate / 1000:.1f} kB/s\033[0m | "
                f"peers: {s.num_peers}) [{state_str[s.state]}]"
            )
            sys.stdout.flush()

            alerts = ses.pop_alerts()
            for a in alerts:
                msg = a.message()
                if "Permission denied" in msg or "file_open error" in msg or "access denied" in msg.lower():
                    sys.stdout.write(f"\n\n\033[31m[DISK ERROR] {msg}\033[0m\n")
                    sys.stdout.write(
                        f"\033[33mEnsure you have write permissions to {os.path.abspath(save_path)}\033[0m\n"
                    )
                    ses.pause()
                    return False

            time.sleep(1)

        print("\n\n\033[32m\033[1mDownload complete!\033[0m")
    except KeyboardInterrupt:
        print("\n\n\033[33mDownload interrupted by user. Cleaning up session...\033[0m")
        ses.pause()
        return False

    return True
