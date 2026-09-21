import discord
import pytz
from src.database.db_utils import fetch_open_positions, fetch_card_trades
import asyncio
from datetime import datetime, timedelta

# Placeholder risk tiers - same caveat as every other hardcoded threshold in
# this codebase: needs real season data to calibrate properly. Shared by the
# live position tracker (once analyse_position uses it) and the backtester,
# so a change here affects training and live decisions identically.
RISK_SETTINGS = {
    "low": {"stop_loss_pct": 0.05, "target_pct": 0.06, "max_hold_hours": 72},
    "medium": {"stop_loss_pct": 0.08, "target_pct": 0.10, "max_hold_hours": 48},
    "high": {"stop_loss_pct": 0.12, "target_pct": 0.18, "max_hold_hours": 24},
}


# Analyse trade history and returns [boolean, sell_price, profit, momentum/rise?, reason]
# if you should sell now or not for one card. `reason` is a short human-readable
# explanation either way, e.g. "steady climb" or "hasn't moved much".
def analyse_position(position, platform):
    trades = fetch_card_trades(position["card_id"], platform)
    # TODO: actual sell/no-sell analysis against `trades`
    return False, None, None, None, "Not enough data yet"


def check_positions(user_id, platform):
    """Check all of a user's open positions and build one embed covering both the
    ones worth selling now and the ones still worth holding, each with a reason.
    Returns None if the user has no open positions at all."""
    open_positions = fetch_open_positions(user_id)
    if not open_positions:
        return None

    sell_candidates = []
    hold_candidates = []
    for position in open_positions:
        should_sell, sell_price, profit, momentum, reason = analyse_position(position, platform)
        if should_sell:
            sell_candidates.append((position, sell_price, profit, momentum, reason))
        else:
            hold_candidates.append((position, reason))

    embed = discord.Embed(title="Your Position Check", color=discord.Color.gold())

    for position, sell_price, profit, momentum, reason in sell_candidates:
        lines = [
            f"Qty: {position['quantity']} @ {position['buy_price']:,}",
            f"Suggested Sell: {sell_price:,}",
            f"Est. Profit: {profit:,}",
        ]
        if momentum is not None:
            lines.append(f"Momentum: {'📈 Rising' if momentum else '📉 Falling'}")
        lines.append(f"Reason: {reason}")
        lines.append(f"Bought: {position['buy_time'].strftime('%Y-%m-%d %H:%M UTC')}")

        embed.add_field(
            name=f"🟢 SELL — {position['name']} ({position['version']})",
            value="\n".join(lines),
            inline=False
        )

    for position, reason in hold_candidates:
        embed.add_field(
            name=f"⏳ HOLD — {position['name']} ({position['version']})",
            value=(
                f"Qty: {position['quantity']} @ {position['buy_price']:,}\n"
                f"Reason: {reason}\n"
                f"Bought: {position['buy_time'].strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            inline=False
        )

    return embed


def get_market_snapshot(conn, card_id, platform="pc", window_hours=1, min_sales=3, as_of=None):
    """
    Returns current price, liquidity (sales volume), and momentum for a
    card - momentum is the % change between the current window and the
    prior window of the same length, so positive means price is rising,
    negative means it's still falling.
 
    as_of: point in time to compute the snapshot for. Defaults to now
    (live use, called from the position tracker). Passing a historical
    datetime lets this exact same function be used to compute
    point-in-time features for backtesting - critical so backtest-trained
    features match live-computed features exactly, rather than a separate
    reimplementation that could subtly compute things differently
    (train/serve skew).
 
    Returns None entirely if there isn't even enough volume to trust a
    price at that point - don't act on a couple of noisy sales. momentum_pct
    specifically is None (separate from the whole snapshot being None) if
    the prior window doesn't have enough volume to compare against - it's
    fine to still act on price/stop-loss without knowing the trend, just
    not to use momentum-based adjustments in that case.

    market_sales.sale_time is stored as naive Adelaide wall-clock time (see
    insert_sale_db in db_utils.py), not UTC - so "now" has to be computed in
    that same naive-Adelaide frame, or every window silently anchors ~10
    hours off from what the stored data considers "now".
    """
    anchor = as_of or datetime.now(pytz.timezone("Australia/Adelaide")).replace(tzinfo=None)
 
    def window_stats(start_hours_ago, end_hours_ago):
        window_start = anchor - timedelta(hours=start_hours_ago)
        window_end = anchor - timedelta(hours=end_hours_ago)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT AVG(sold_price) AS avg_price, COUNT(*) AS n
                FROM market_sales
                WHERE card_id=%s AND platform=%s AND sold_price > 0
                  AND sale_time BETWEEN %s AND %s
            """, (card_id, platform, window_start, window_end))
            return cur.fetchone()
 
    recent = window_stats(window_hours, 0)
    if not recent or not recent["avg_price"] or recent["n"] < min_sales:
        return None  # not enough recent liquidity to trust a price at all this cycle
 
    current_price = float(recent["avg_price"])
    volume = recent["n"]
 
    prior = window_stats(window_hours * 2, window_hours)
    momentum_pct = None
    if prior and prior["avg_price"] and prior["n"] >= min_sales:
        momentum_pct = (current_price - float(prior["avg_price"])) / float(prior["avg_price"]) * 100
 
    return {"current_price": current_price, "volume": volume, "momentum_pct": momentum_pct}
 
