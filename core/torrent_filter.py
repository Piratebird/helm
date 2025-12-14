"""
filter torrents functionality based on seeds and quality

core/torrent_filter.py
"""


def match_quality(title, qualities):
    title = title.lower()
    return any(q.lower() in title for q in qualities)


def dedupe(items):
    seen = set()
    unique = []
    for i in items:
        link = i.link
        hash_start = link.find("btih:") + 5
        hash_value = link[hash_start : hash_start + 40].lower()
        if hash_value not in seen:
            seen.add(hash_value)
            unique.append(i)
    return unique


def filter_items(items, config):
    items = dedupe(items)
    items = [i for i in items if match_quality(i.title, config["qualities"])]
    return sorted(items, key=lambda x: x.published, reverse=True)
