from core.indexers_setup import setup_indexers
from core.rss_fetcher import search_jackett
from core.config_manager import CONTENT_PROFILES, NEGATIVE_KEYWORDS
from core.torrent_filter import filter_items
from core.qbittorrent_client import add_magnet

if __name__ == "__main__":
    print("THE HELM- Torrent automation MVP ")

    setup_indexers()
    query = input("What would you like to search for: ").strip()
    content_type = (
        input(
            "Content type [video / games / software / books / music] (default: video ): "
        )
        .strip()
        .lower()
        or "video"
    )
    keywords = CONTENT_PROFILES.get(content_type, CONTENT_PROFILES["video"])
    negatives = NEGATIVE_KEYWORDS.get(content_type, [])
    all_items = search_jackett(query)
    print(f"Jackett returned {len(all_items)} raw results")

    # config = load_config()
    filtered = filter_items(all_items, keywords, negatives)

    if not filtered:
        print("No torrent were found :( ")
        exit()

    print(f"\nFound {len(filtered)} results:\n")
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

        except KeyboardInterrupt:
            print("\nlater bozo!")
            exit()
