"""
filter torrents functionality based on seeds and quality

core/torrent_filter.py
"""

import hashlib
import re


def _compile_words(words):
    if not words:
        return None
    # Sort by length descending to match longest phrases first
    sorted_words = sorted(words, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(w.lower()) for w in sorted_words) + r")\b")


def filter_items(items, category_profiles, negatives, min_score=0, min_seeds=0):
    results = []

    neg_pattern = _compile_words(negatives)
    cat_patterns = {cat: _compile_words(pos) for cat, pos in category_profiles.items()}

    for item in items:
        title = getattr(item, "title", "").lower()
        seeders = getattr(item, "seeders", 0)
        try:
            seeders = int(seeders)
        except (ValueError, TypeError):
            seeders = 0

        if seeders != -1 and seeders < min_seeds:
            continue

        if neg_pattern and neg_pattern.search(title):
            continue

        best_cat = None
        best_score = 0

        # Score against each category profile
        for cat, pattern in cat_patterns.items():
            if pattern:
                # Count unique keyword matches
                matches = set(pattern.findall(title))
                score = len(matches)
                if score > best_score:
                    best_score = score
                    best_cat = cat

        if best_score >= min_score or not any(cat_patterns.values()):
            item.score = best_score
            item.media_type = best_cat if best_score > 0 else None
            results.append((item, best_score))

    return [i[0] for i in sorted(results, key=lambda x: x[1], reverse=True)]


def dedupe(items):
    seen = set()
    unique = []
    for i in items:
        link = getattr(i, "link", "")
        if "btih:" in link.lower():
            hash_start = link.lower().find("btih:") + 5
            hash_value = link[hash_start : hash_start + 40].lower()
        else:
            hash_value = (
                hashlib.md5(link.encode("utf-8")).hexdigest()
                if link
                else hashlib.md5(getattr(i, "title", "").encode("utf-8")).hexdigest()
            )

        if hash_value not in seen:
            seen.add(hash_value)
            unique.append(i)
    return unique
