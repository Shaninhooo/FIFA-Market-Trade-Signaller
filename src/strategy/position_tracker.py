import discord
from src.database.db_utils import fetch_open_positions, fetch_card_trades


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
