"""
filter torrents functionality based on seeds and quality

core/torrent_filter.py
"""


import re

def is_negative_match(title, negatives):
    title = title.lower()
    for n in negatives:
        if re.search(r'\b' + re.escape(n.lower()) + r'\b', title):
            return True
    return False


def score_item(title, positives):
    title = title.lower()
    score = 0
    for p in positives:
        if re.search(r'\b' + re.escape(p.lower()) + r'\b', title):
            score += 1
    return score


def match_keywords(title, keywords):
    title = title.lower()
    for k in keywords:
        if re.search(r'\b' + re.escape(k.lower()) + r'\b', title):
            return True
    return False


def match_quality(title, qualities):
    title = title.lower()
    for q in qualities:
        if re.search(r'\b' + re.escape(q.lower()) + r'\b', title):
            return True
    return False


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
