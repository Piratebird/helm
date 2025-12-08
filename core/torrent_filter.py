"""
filter torrents functionality based on seeds and quality

core/torrent_filter.py
"""


def match_quality(title, qualities):
    title = title.lower()
    return any(q.lower() in title for q in qualities)


def dedupe(items):
    pass
