"""
Captures the features potentially predictive of a trade's outcome at the
moment of purchase, so they can be stored alongside the position. Once
enough trades have closed, these (feature, realized_profit) pairs become
the training data for a real model predicting P(win | features) - a
genuine per-trade probability, rather than the coarse per-segment average
Kelly sizing currently falls back to.

Nothing here is used for sizing yet - it's purely data collection until
there's enough closed-trade volume to fit something on top of it.
"""

import json

from src.strategy.market_index import get_market_index_snapshot, is_crash_mode
from src.strategy.position_tracker import get_market_snapshot


def capture_trade_features(conn, card_id, platform="pc", as_of=None):
    """
    Best-effort snapshot - individual fields may be None if there isn't
    enough data to compute them at that point (e.g. very early season,
    thin liquidity). Storing None is fine; it just means that feature
    can't be used for rows where it's missing once you get to
    model-fitting time.

    as_of: defaults to now for live use (called from /buy). Passing a
    historical datetime lets this same function generate point-in-time
    features for backtesting - the underlying get_market_snapshot and
    get_market_index_snapshot calls are identical either way, so live and
    backtest features are guaranteed to be computed the same way.
    """
    snapshot = get_market_snapshot(conn, card_id, platform=platform, as_of=as_of)
    index_snapshot = get_market_index_snapshot(platform=platform, as_of=as_of)

    return {
        "current_price": snapshot["current_price"] if snapshot else None,
        "recent_volume": snapshot["volume"] if snapshot else None,
        "momentum_pct": snapshot["momentum_pct"] if snapshot else None,
        "market_median_change_pct": index_snapshot["median_change_pct"] if index_snapshot else None,
        "crash_mode": is_crash_mode(index_snapshot),
    }


def features_to_json(features_dict):
    return json.dumps(features_dict)