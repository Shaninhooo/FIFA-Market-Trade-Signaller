"""
Market-wide index used to distinguish an isolated card dip (a real
mean-reversion opportunity) from a market-wide crash (everything dropping
together - which looks identical to a great buy signal on any single card,
but isn't a reversion play at all).

Core idea: compute a broad "index" return across many liquid cards, then
gate individual buy signals on how much a card's drop EXCEEDS the index's
drop - the same logic as isolating alpha from market beta. A severe
index-level move also flags an explicit crash-mode other parts of the
system (drop_strategy, position_tracker) can check and react to.
"""

from datetime import datetime, timezone
from src.database.db_utils import fetch_market_index_sample


def get_market_index_snapshot(platform="pc", short_hours=2, long_hours=8,
                               min_price=20000, sample_min_sales=10):
    """
    Builds a market-wide index from a broad basket of liquid, mid-to-high
    priced cards. Fodder is excluded via min_price - too noisy and not
    representative of the meta market you're actually trying to protect
    the signaller from misreading.

    Returns the index's median short-vs-long % move, or None if there
    isn't enough breadth of liquid cards sampled to trust it yet.
    """
    rows = fetch_market_index_sample(platform, short_hours, long_hours, min_price, sample_min_sales)

    if len(rows) < 20:
        return None  # too few liquid cards sampled to trust a market-wide read yet

    pct_changes = sorted(
        (r["short_avg"] - r["long_avg"]) / r["long_avg"] * 100
        for r in rows if r["long_avg"]
    )
    median_change_pct = pct_changes[len(pct_changes) // 2]  # median, not mean - resists a few extreme cards skewing it

    return {
        "median_change_pct": median_change_pct,
        "sample_size": len(pct_changes),
        "computed_at": datetime.now(timezone.utc),
    }


def is_crash_mode(index_snapshot, crash_threshold_pct=-6):
    """
    True if the broad market itself is down significantly, not just one
    card - i.e. this looks systemic, not idiosyncratic.

    crash_threshold_pct is a provisional placeholder, same caveat as every
    other hardcoded threshold in this codebase - it needs real-season data
    to calibrate properly. Fails open (returns False) if there's not
    enough data to call it - don't block trading on missing information.
    """
    if index_snapshot is None:
        return False
    return index_snapshot["median_change_pct"] <= crash_threshold_pct