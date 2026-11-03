"""
filter torrents functionality based on seeds and quality

core/torrent_filter.py
"""

import base64
import copy
import hashlib
import re


def _compile_words(words):
    if not words:
        return None
    # Sort by length descending to match longest phrases first
    sorted_words = sorted(words, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(w.lower()) for w in sorted_words) + r")\b")


def category_groups(item):
    """The standard Torznab groups (1000, 2000, ... 7000) a source put this item in."""
    groups = set()
    for value in getattr(item, "categories", None) or ():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1000 <= number < 8000 and number // 1000 != 6:
            groups.add(number // 1000 * 1000)
    return groups


def filter_items(
    items, category_profiles, negatives, min_score=0, min_seeds=0, weak_negatives=(), hidden=None, groups=None
):
    """Score and filter search results, best first.

    A result whose source category matches *groups* skips the keyword checks; one
    whose source category only matches other content types is hidden. Otherwise a
    title with a *negatives* word is hidden and each *weak_negatives* word only
    lowers its rank. Hidden items go to *hidden* as (item, reason) pairs, with
    reason "seeds", "category" or "keyword:<word>", so callers can still show them.
    """
    results = []

    neg_pattern = _compile_words(negatives)
    weak_pattern = _compile_words(weak_negatives)
    cat_patterns = {cat: _compile_words(pos) for cat, pos in category_profiles.items()}
    wanted = set(groups or ())

    def hide(item, reason):
        if hidden is not None:
            hidden.append((item, reason))

    for item in items:
        title = getattr(item, "title", "").lower()
        seeders = getattr(item, "seeders", 0)
        try:
            seeders = int(seeders)
        except (ValueError, TypeError):
            seeders = 0

        if seeders != -1 and seeders < min_seeds:
            hide(item, "seeds")
            continue

        source_groups = category_groups(item) if wanted else set()
        trusted = bool(source_groups & wanted)
        if source_groups and not trusted:
            hide(item, "category")
            continue

        if not trusted and neg_pattern:
            match = neg_pattern.search(title)
            if match:
                hide(item, f"keyword:{match.group(0)}")
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

        penalty = 0 if trusted or not weak_pattern else len(set(weak_pattern.findall(title)))

        if best_score >= min_score or not any(cat_patterns.values()):
            item.score = best_score
            item.media_type = best_cat if best_score > 0 else None
            results.append((item, best_score - penalty))
        else:
            hide(item, "score")

    return [i[0] for i in sorted(results, key=lambda x: x[1], reverse=True)]


HEX_HASH = re.compile(r"^[0-9a-f]{40}$")
BASE32_HASH = re.compile(r"^[a-z2-7]{32}$")
MAGNET_HASH = re.compile(r"xt=urn:btih:([0-9a-z]+)", re.IGNORECASE)
TORRENT_URL_HASH = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])[^/]*\.torrent(?:$|\?)", re.IGNORECASE)


def normalize_hash(value):
    """Return a BitTorrent v1 info hash as 40 lowercase hex characters, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if HEX_HASH.match(value):
        return value
    if BASE32_HASH.match(value):
        return base64.b32decode(value.upper()).hex()
    return None


def item_hash(item):
    """Info hash from the item's own field, its magnet link, or a .torrent URL named after the hash."""
    found = normalize_hash(getattr(item, "info_hash", None))
    if found:
        return found
    link = getattr(item, "link", "") or ""
    match = MAGNET_HASH.search(link) if link.lower().startswith("magnet:") else TORRENT_URL_HASH.search(link)
    return normalize_hash(match.group(1)) if match else None


def normalize_title(title):
    return " ".join(re.sub(r"[^0-9a-z]+", " ", (title or "").lower()).split())


def dedupe_key(item):
    found = item_hash(item)
    if found:
        return ("hash", found)
    size = getattr(item, "size", 0) or 0
    if size > 0:
        return ("title", normalize_title(getattr(item, "title", "")), size)
    link = getattr(item, "link", "") or ""
    return ("link", hashlib.md5((link or getattr(item, "title", "")).encode("utf-8")).hexdigest())


def _seeders(item):
    try:
        return int(getattr(item, "seeders", 0))
    except (TypeError, ValueError):
        return 0


def _known(value):
    return value not in (None, "", 0, "Unknown")


def _merge(group):
    """One item for a set of duplicates: the best-seeded copy, filled in from the others."""
    best = copy.copy(max(group, key=_seeders))
    for field in ("pubdate", "size"):
        if not _known(getattr(best, field, None)):
            value = next((getattr(i, field) for i in group if _known(getattr(i, field, None))), None)
            if value is not None:
                setattr(best, field, value)
    if not str(getattr(best, "link", "")).lower().startswith("magnet:"):
        magnet = next((i.link for i in group if str(getattr(i, "link", "")).lower().startswith("magnet:")), None)
        if magnet:
            best.link = magnet
    found = item_hash(best) or next((item_hash(i) for i in group if item_hash(i)), None)
    if found and hasattr(best, "info_hash"):
        best.info_hash = found
    winner = max(group, key=_seeders)
    sources = []
    for item in [winner] + [i for i in group if i is not winner]:
        for source in _sources(item):
            if source not in sources:
                sources.append(source)
    if hasattr(best, "sources"):
        best.sources = sources
    if len(sources) > 1 and hasattr(best, "indexer"):
        best.indexer = f"{sources[0]} +{len(sources) - 1}"
    return best


def _sources(item):
    """The sources behind an item; an already merged item keeps its list."""
    listed = getattr(item, "sources", None)
    if listed:
        return list(listed)
    indexer = getattr(item, "indexer", None)
    return [indexer] if indexer else []


def dedupe(items):
    """Collapse duplicates across sources, keeping first-seen order.

    Items match on info hash first, then on normalized title plus size, then on
    the link. Duplicates are merged rather than dropped: the copy with the most
    seeders wins, a missing date or size is taken from another copy, a magnet is
    preferred over an indexer download URL, and every source is kept.
    """
    groups = {}
    for item in items:
        groups.setdefault(dedupe_key(item), []).append(item)
    _join_rounded_sizes(groups)
    return [group[0] if len(group) == 1 else _merge(group) for group in groups.values()]


SIZE_TOLERANCE = 0.02


def _join_rounded_sizes(groups):
    """Fold hashless results into a hashed one with the same title and nearly the same size.

    Sites that only show "4.2 GB" give a rounded size and no hash, so the exact
    title-and-size key misses the copy another source reported precisely.
    """
    by_title = {}
    for key, group in groups.items():
        if key[0] == "hash":
            for item in group:
                by_title.setdefault(normalize_title(getattr(item, "title", "")), []).append((key, item))
    if not by_title:
        return
    for key in [k for k in groups if k[0] == "title"]:
        title, size = key[1], key[2]
        for target, item in by_title.get(title, ()):
            other = getattr(item, "size", 0) or 0
            if other and abs(other - size) <= SIZE_TOLERANCE * max(other, size):
                groups[target].extend(groups.pop(key))
                break
