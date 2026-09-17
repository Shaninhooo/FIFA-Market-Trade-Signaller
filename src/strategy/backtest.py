"""
Generates (features, outcome) training examples by replaying historical
market data - no real executed trades needed. Every sampled point in a
card's history becomes a hypothetical entry, simulated forward using the
same stop-loss/target logic as the live position tracker, producing a
label (won/lost) from what your actual exit rules would have captured -
not an idealized "sold at the exact peak" outcome.

This still needs real market_sales history to run against (it doesn't
remove the need for this season's real data), but since every card-hour
is a potential example rather than just the handful of trades someone
actually executes, it produces vastly more labeled data per day of real
market history than waiting on manual trades ever could.

Caveat worth keeping in mind: this assumes you could have bought and sold
at exactly the historical prices recorded - real execution has some
slippage/liquidity friction this won't capture. Good for training signal
quality; treat exact simulated profit amounts as directional, not precise.
"""

from datetime import timedelta

from src.strategy.position_tracker import RISK_SETTINGS
from src.strategy.trade_features import capture_trade_features
from src.database.db_utils import fetch_price_series

# Simulating trading outcome for a card

def simulate_trade_outcome(card_id, platform, entry_price, entry_time,
                            risk_tier="medium", horizon_hours=48):
    """
    Walks forward through the ACTUAL historical price series following a
    hypothetical entry, applying the same flat stop-loss/target thresholds
    as the live position tracker (simplified - this doesn't re-simulate
    momentum-based stop tightening or trailing at every historical tick,
    since that needs a momentum read at every point in the walk; flat
    thresholds are a reasonable approximation for generating labels).

    Returns (exit_price, exit_reason). exit_reason mirrors the position
    tracker's own values, plus 'no_exit_in_horizon' if the price series
    ran out before any exit condition triggered.
    """
    settings = RISK_SETTINGS[risk_tier]
    stop_loss_price = entry_price * (1 - settings["stop_loss_pct"])
    target_price = entry_price * (1 + settings["target_pct"])
    deadline = entry_time + timedelta(hours=settings["max_hold_hours"])
    horizon_end = entry_time + timedelta(hours=horizon_hours)

    series = fetch_price_series(card_id, platform, entry_time, horizon_end)

    for row in series.itertuples():
        sale_time, sale_price = row.sale_time, row.sold_price
        if sale_price <= stop_loss_price:
            return sale_price, "stop_loss"
        if sale_price >= target_price:
            return sale_price, "target_hit"
        if sale_time >= deadline:
            return sale_price, "max_hold_time"

    return None, "no_exit_in_horizon"


def generate_training_examples(conn, card_id, platform, start_date, end_date,
                                sample_interval_hours=4, risk_tier="medium"):
    """
    Scans a card's history at fixed intervals, treating each point as a
    hypothetical entry, and returns a list of (features, won, exit_reason)
    examples ready for model training.

    Sampling every N hours (rather than every single sale) keeps query
    volume manageable - each point needs several queries (features +
    forward price series). Worth refining later: only sample points where
    drop_strategy's own z-score criteria would historically have fired,
    so the training distribution matches what the live model will
    actually be asked to predict on, rather than arbitrary regular
    intervals that include a lot of "nothing interesting happening" points.
    """
    examples = []
    current = start_date

    while current < end_date:
        features = capture_trade_features(conn, card_id, platform, as_of=current)
        entry_price = features["current_price"]

        if entry_price is not None:
            exit_price, exit_reason = simulate_trade_outcome(
                card_id, platform, entry_price, current, risk_tier=risk_tier
            )
            if exit_price is not None:
                profit = round(exit_price * 0.95) - entry_price
                examples.append({
                    "features": features,
                    "won": profit > 0,
                    "exit_reason": exit_reason,
                    "entry_time": current,
                })

        current += timedelta(hours=sample_interval_hours)

    return examples