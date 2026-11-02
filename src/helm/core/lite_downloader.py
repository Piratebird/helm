import os
import sys
import time

from helm.ui.ansi import T


def download_magnet(magnet_link, save_path=None, on_complete=None):
    try:
        import libtorrent as lt
    except ImportError:
        print(f"\n{T.WARNING}[!] libtorrent is not installed.{T.RST}")
        print("To use the built-in lite downloader, install 'helm-torrent[download]' where supported.")
        print(f"\n{T.BOLD}Magnet Link:{T.RST} {magnet_link}\n")
        print("Copy the above link and paste it into your torrent client.")
        return False

    if save_path is None:
        from helm.core.config_manager import get_dl_dir

        save_path = get_dl_dir()
    save_path = os.path.abspath(os.path.expanduser(os.fspath(save_path)))
    os.makedirs(save_path, exist_ok=True)

    settings = {
        "listen_interfaces": "0.0.0.0:0",
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
        "announce_to_all_trackers": True,
        "announce_to_all_tiers": True,
        # Configure bootstrap nodes before starting DHT. Some libtorrent builds
        # (including Termux's) omit the deprecated add_dht_router binding.
        "dht_bootstrap_nodes": (
            "router.bittorrent.com:6881,router.utorrent.com:6881,dht.transmissionbt.com:6881,dht.aelitis.com:6881"
        ),
    }
    from helm.core.torrent_links import resolve_torrent

    source = resolve_torrent(magnet_link)
    ses = lt.session(settings)

    print(f"\n{T.BOLD}{T.INFO}Adding magnet link to libtorrent session...{T.RST}")

    try:
        # Parse the magnet URI properly for libtorrent 2.x
        if source.data:
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(lt.bdecode(source.data))
        else:
            params = lt.parse_magnet_uri(source.magnet)
        params.save_path = save_path
        handle = ses.add_torrent(params)

        sys.stdout.write("Downloading Metadata")

        timeout = 60
        elapsed = 0
        while not handle.status().has_metadata:
            time.sleep(1)
            sys.stdout.write(".")
            sys.stdout.flush()
            elapsed += 1
            if elapsed >= timeout:
                print(f"\n\n{T.ERROR}[!] Timeout reaching metadata. The torrent might be dead or blocked.{T.RST}")
                print(f"Fallback Magnet Link: {magnet_link}")
                return False

        print("\nMetadata downloaded. Starting torrent download...")
        print(f"Saving to: {T.BOLD}{os.path.abspath(save_path)}{T.RST}\n")

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
                f"\r{T.EOL}{T.BOLD}{s.progress * 100:.2f}%{T.RST} complete "
                f"(down: {T.SUCCESS}{s.download_rate / 1000:.1f} kB/s{T.RST} | "
                f"up: {T.ERROR}{s.upload_rate / 1000:.1f} kB/s{T.RST} | "
                f"peers: {s.num_peers}) [{state_str[s.state]}]"
            )
            sys.stdout.flush()

            alerts = ses.pop_alerts()
            for a in alerts:
                msg = a.message()
                if "Permission denied" in msg or "file_open error" in msg or "access denied" in msg.lower():
                    sys.stdout.write(f"\n\n{T.ERROR}[DISK ERROR] {msg}{T.RST}\n")
                    sys.stdout.write(
                        f"{T.WARNING}Ensure you have write permissions to {os.path.abspath(save_path)}{T.RST}\n"
                    )
                    return False

            time.sleep(1)

        if on_complete:
            info = handle.torrent_file()
            files = info.files()
            on_complete([os.path.join(save_path, files.file_path(index)) for index in range(files.num_files())])
        print(f"\n\n{T.SUCCESS}{T.BOLD}Download complete!{T.RST}")
    except KeyboardInterrupt:
        print(f"\n\n{T.WARNING}Download interrupted by user. Cleaning up session...{T.RST}")
        return False
    finally:
        ses.pause()

    return True
