"""Find a better spelling when a one-word name is usually written as two.

Search sources match words literally, and release names often split a name
that people type as one word: "spiderman" finds 6 results while "spider man"
finds hundreds of Spider-Man releases. When a search comes back thin, each long
word of the query is split at every position, each split is probed against two
fast sources, and a split wins only if its results contain the original word
once punctuation is removed (so "spider man" counts, "spi derman" does not)
and beat the query as typed.
"""

import concurrent.futures
import re

from helm.logger import get_logger

logger = get_logger(__name__)

THIN_RESULTS = 30  # fewer raw results than this triggers the variant probe
MIN_WORD = 6  # shorter words are rarely compounds and give too many splits
MIN_PART = 3
MAX_CANDIDATES = 12
MIN_GAIN = 2  # a variant must find at least twice as many matching results


def squash(text):
    return re.sub(r"[^0-9a-z]+", "", (text or "").lower())


def candidates(query):
    """(word, split query) pairs: each long alphabetic word split once with a space."""
    words = query.split()
    found = []
    for index, word in enumerate(words):
        if len(word) < MIN_WORD or not word.isalpha():
            continue
        for cut in range(MIN_PART, len(word) - MIN_PART + 1):
            split = f"{word[:cut]} {word[cut:]}"
            found.append((word, " ".join(words[:index] + [split] + words[index + 1 :])))
    return found[:MAX_CANDIDATES]


def _probe(query):
    from helm.search.lite_fetcher import _fetch_apibay, _fetch_torrents_csv

    items = []
    for fetch in (_fetch_apibay, _fetch_torrents_csv):
        try:
            items.extend(fetch(query))
        except Exception:
            logger.debug("Event: Variant probe failed for one source", exc_info=True)
    return items


def matching(items, word, split_query):
    """How many results contain *word* once punctuation is gone, and the rest of the query."""
    needed = [squash(word)] + [squash(w) for w in split_query.split() if squash(w) and squash(w) not in squash(word)]
    return sum(1 for item in items if all(part in squash(getattr(item, "title", "")) for part in needed))


def better_query(query, raw_count, probe=_probe, workers=6):
    """Return a split spelling that finds clearly more, or None."""
    if raw_count >= THIN_RESULTS:
        return None
    options = candidates(query)
    if not options:
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(options) + 1)) as pool:
        baseline = pool.submit(probe, query)
        probes = {variant: (word, pool.submit(probe, variant)) for word, variant in options}
        base = len(baseline.result())
        scores = {variant: matching(future.result(), word, variant) for variant, (word, future) in probes.items()}
    best = max(scores, key=lambda variant: scores[variant])
    if scores[best] >= max(MIN_GAIN * max(base, 1), 5):
        logger.info("Event: Query %r also searched as %r (%s vs %s probe results)", query, best, scores[best], base)
        return best
    return None
