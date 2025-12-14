from core.indexers_setup import setup_indexers
from core.rss_fetcher import search_jackett
from core.config_manager import load_config
from core.torrent_filter import filter_items
from core.qbittorrent_client import add_magnet

if __name__ == "__main__":
    print("THE HELM- Torrent automation MVP ")

    setup_indexers()
    wat_torrent = str(input("What would you like to search for: "))
    all_items = search_jackett(wat_torrent)

    config = load_config()
    filtered = filter_items(all_items, config)

    if not filtered:
        print("No torrent were found :( ")
        exit()

    print(f"\nFound {len(filtered)} filtered torrent:\n")
    print("-" * 69)
    for i, t in enumerate(filtered, 1):
        # shorten longer titles for readability
        title = t.title if len(t.title) <= 60 else t.title[:57] + "...."
        print(f"{i:>2}.{title}\n Link:{t.link}\n")
    print("-" * 67)

    while True:
        try:
            choice = int(
                input(
                    f"Enter the number of the torrent to send to qbittorrent (1-{len(filtered)}): "
                )
            )
            if 1 <= choice <= len(filtered):
                selected = filtered[choice - 1]
                add_magnet(selected.link)
                print("Torrent sent sucessfully :)")
                break
            else:
                print("Invalid choice try again.")
        except ValueError:
            print("!!! PLEASE ENTER A VALID NUMBER !!!")
