"""
filter torrents functionality based on seeds and quality

core/torrent_filter.py
"""


def is_negative_match(title, negatives):
    title = title.lower()
    return any(n in title for n in negatives)


def score_item(title, positives):
    title = title.lower()
    return sum(1 for p in positives if p in title)


def match_keywords(title, keywords):
    title = title.lower()
    return any(k in title for k in keywords)


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


def filter_items(items, positives, negatives, min_score=1):
    results = []

    for item in items:
        title = item.title

        # if it containts a negative word(unrelated to the searched topic) kill the fucker
        if is_negative_match(title, negatives):
            continue

        score = score_item(title, positives)

        if score >= min_score or positives == []:
            results.append((item, score))

        # sort by score descending order
    return [i[0] for i in sorted(results, key=lambda x: x[1], reverse=True)]
