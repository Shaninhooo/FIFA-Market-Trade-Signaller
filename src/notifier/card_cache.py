"""
In-memory card name cache + fuzzy search, used by Discord autocomplete.

Card metadata rarely changes (weekly scraper), so this avoids hitting the
DB on every keystroke - autocomplete has a hard ~3s response window with
no defer() option, so a live query per character is too risky to rely on.
"""

import re
import threading
from rapidfuzz import process, fuzz

# Each row: (card_id, name, version, rating, display_label). Rating is
# included in the label so two cards that share a name and version but
# differ only by rating (e.g. a dynamic objective item, or the same
# special version appearing at different points in the season) still
# look distinct in the autocomplete dropdown.
_card_cache = []
_names = []
_versions = []
_ratings = []      # parallel list of str(rating), for filtering
_cache_lock = threading.Lock()

_NUMERIC_RE = re.compile(r"\d+")


def refresh_card_cache(conn):
    """
    Reload the in-memory card cache from the DB.

    Call once at bot startup (in on_ready) and again after the weekly
    metadata scraper finishes adding new cards. This does a blocking DB
    call, so from async code run it via asyncio.to_thread(refresh_card_cache, conn).
    """
    global _card_cache, _names, _versions, _ratings

    with conn.cursor() as cur:
        cur.execute("SELECT card_id, name, version, rating FROM cards")
        rows = cur.fetchall()

    new_cache = [
        (
            r["card_id"],
            r["name"],
            r["version"],
            r["rating"],
            f"{r['name']} ({r['version']}, {r['rating']})",
        )
        for r in rows
    ]

    with _cache_lock:
        _card_cache = new_cache
        _names = [c[1] for c in new_cache]
        _versions = [c[2] for c in new_cache]
        _ratings = [str(c[3]) for c in new_cache]

    print(f"Card cache refreshed: {len(_card_cache)} cards loaded")


def search_cards_fuzzy(query, limit=25, score_cutoff=50):
    """
    Search cached cards by name, version, and rating together.

    Rating is pulled out of the query as a separate numeric filter rather
    than fuzzy-matched - fuzzy scoring on short numbers is unreliable
    (e.g. "89" and "98" can score deceptively similar on shared digits).
    A numeric token in the query (e.g. "mbappe 91" or just "91") is
    treated as a prefix filter on rating, and any remaining text is
    fuzzy-matched against name and version as before, taking the best
    score per card across both fields.

    Returns a list of (card_id, label) tuples, best match first.
    """
    with _cache_lock:
        cache_snapshot = _card_cache
        names_snapshot = _names
        versions_snapshot = _versions
        ratings_snapshot = _ratings

    if not cache_snapshot:
        return []  # cache not loaded yet - fail quiet, don't crash autocomplete

    if not query:
        return [(c[0], c[4]) for c in cache_snapshot[:limit]]  # nothing typed - default set

    numeric_tokens = _NUMERIC_RE.findall(query)
    rating_filter = numeric_tokens[-1] if numeric_tokens else None
    text_query = _NUMERIC_RE.sub("", query).strip()

    if rating_filter:
        candidate_idx = {i for i, r in enumerate(ratings_snapshot) if r.startswith(rating_filter)}
        if not candidate_idx:
            return []  # rating filter matched nothing - no point fuzzy-scoring the rest
    else:
        candidate_idx = None  # no rating narrowing, consider every card

    if not text_query:
        # Pure rating query (e.g. "91") - nothing to rank by, just return matches
        idxs = sorted(candidate_idx) if candidate_idx is not None else range(len(cache_snapshot))
        return [(cache_snapshot[i][0], cache_snapshot[i][4]) for i in list(idxs)[:limit]]

    name_matches = process.extract(
        text_query, names_snapshot, scorer=fuzz.WRatio, limit=len(names_snapshot), score_cutoff=0
    )
    version_matches = process.extract(
        text_query, versions_snapshot, scorer=fuzz.WRatio, limit=len(versions_snapshot), score_cutoff=0
    )

    best_score = {}
    for _, score, idx in name_matches:
        if candidate_idx is None or idx in candidate_idx:
            best_score[idx] = max(best_score.get(idx, 0), score)
    for _, score, idx in version_matches:
        if candidate_idx is None or idx in candidate_idx:
            best_score[idx] = max(best_score.get(idx, 0), score)

    ranked = sorted(best_score.items(), key=lambda kv: kv[1], reverse=True)
    return [
        (cache_snapshot[idx][0], cache_snapshot[idx][4])
        for idx, score in ranked
        if score >= score_cutoff
    ][:limit]