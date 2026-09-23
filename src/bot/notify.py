from src.bot.discord import send_message, get_or_create_tracker_channel, clear_channel
from src.strategy.position_tracker import check_positions
from src.strategy.deal_finder import icon_fluctuation_strategy, hero_fluctuation_strategy, hero_strategy, icon_dip_strategy, early_game_strategy


def _buy_tag(row):
    """Labels a buy price as a real live listing vs. a sold-price estimate -
    see buy_price_source in deal_finder.py's _merge_live_price. current_listings
    can lag or simply not have a card yet, so this isn't always "live"."""
    return "live listing" if row.get("buy_price_source") == "live" else "est. from sales"


# Send Message on Discord of all the Best Found Drop Deals
async def notify_drop_deals(buy_df, plat):
    await clear_channel(f"gold_{plat}")
    if not buy_df.empty:
        for _, row in buy_df.head(5).iterrows():
            msg = (
                f"📊 **{plat.upper()} Deal Alert!**\n"
                f"🎴 Card: {row['name']} ({row['version']})\n"
                f"📉 Drop: {row['drop_%']}%\n"
                f"🟢 Buy ~ {row['suggested_buy']:,} ({_buy_tag(row)})\n"
                f"🔴 Sell ~ {row['suggested_sell_raw']:,}\n"
                f"💰 Profit: {row['potential_profit']:,} ({row['profit_margin_%']}%)\n"
                f"🏷️ Rating: {row['investment_rating']}"
            )
            await send_message(msg, f"gold_{plat}")
    else:
        await send_message(f"No Dip Buy candidates found within current hour on {plat.upper()}.", f"gold_{plat}")


# Send Message on Discord of all the Best Found Icon Fluctuations - shares the
# icon_{plat} channel with notify_icon_deals below, since both are icon
# signals. `clear` defaults to True for standalone use, but when both are
# called in the same cycle (see main.py), only one of them should actually
# clear the channel - otherwise whichever runs second wipes the first's
# messages instead of both batches landing together.
async def notify_icon_fluctuations(plat, clear=True):
    fluctuation_df = icon_fluctuation_strategy(plat)
    if clear:
        await clear_channel(f"icon_{plat}")
    if not fluctuation_df.empty:
        for _, row in fluctuation_df.head(5).iterrows():
            msg = (
                f"💎 **Icon Fluctuation on {plat.upper()}: {row['name']} ({row['version']})**\n"
                f"🟢 Buy ~ {int(row['best_buy']):,} ({_buy_tag(row)})\n"
                f"🔴 Sell ~ {int(row['best_sell']):,}\n"
                f"📊 Latest Sale ~ {int(row['latest_sale']):,}\n"
                f"📉 Spread ~ {row['spread_%']}%\n"
                f"💰 Margin: {row['profit_margin_%']}%"
            )
            await send_message(msg, f"icon_{plat}")
    else:
        await send_message(f"**No icon fluctuation candidates found this hour on {plat.upper()}.**", f"icon_{plat}")


# Same channel-sharing pattern as notify_icon_fluctuations above, for the
# hero_{plat} channel shared with notify_hero_deals.
async def notify_hero_fluctuations(plat, clear=True):
    fluctuation_df = hero_fluctuation_strategy(plat)
    if clear:
        await clear_channel(f"hero_{plat}")
    if not fluctuation_df.empty:
        for _, row in fluctuation_df.head(5).iterrows():
            msg = (
                f"👑 **Hero Fluctuation on {plat.upper()}: {row['name']} ({row['version']})**\n"
                f"🟢 Buy ~ {int(row['best_buy']):,} ({_buy_tag(row)})\n"
                f"🔴 Sell ~ {int(row['best_sell']):,}\n"
                f"📊 Latest Sale ~ {int(row['latest_sale']):,}\n"
                f"📉 Spread ~ {row['spread_%']}%\n"
                f"💰 Margin: {row['profit_margin_%']}%"
            )
            await send_message(msg, f"hero_{plat}")
    else:
        await send_message(f"**No hero fluctuation candidates found this hour on {plat.upper()}.**", f"hero_{plat}")


# Hero and Icon dip alerts are kept in separate per-platform channels
# (hero_pc/hero_ps, icon_pc/icon_ps - see DEAL_CHANNEL_IDS in discord.py)
# rather than one mixed feed, so each is easy to watch on its own. `clear`
# works the same way as notify_hero_fluctuations/notify_icon_fluctuations -
# see the note above notify_icon_fluctuations.
async def notify_hero_deals(plat, clear=True):
    deals_df = hero_strategy(plat)
    if clear:
        await clear_channel(f"hero_{plat}")
    if not deals_df.empty:
        for _, row in deals_df.head(5).iterrows():
            msg = (
                f"👑 **Hero Dip on {plat.upper()}: {row['name']} ({row['version']}, {row['rating']} OVR)**\n"
                f"📉 Drop: {row['drop_%']}% (last {row['recent_sales']} sale(s) vs {row['baseline_sales']}-sale baseline)\n"
                f"🟢 Buy ~ {row['suggested_buy']:,} ({_buy_tag(row)})\n"
                f"🔴 Sell ~ {row['suggested_sell_raw']:,}\n"
                f"💰 Profit: {row['potential_profit']:,} ({row['profit_margin_%']}%)\n"
                f"🏷️ Rating: {row['investment_rating']}"
            )
            await send_message(msg, f"hero_{plat}")
    else:
        await send_message(f"**No Hero dip candidates found this hour on {plat.upper()}.**", f"hero_{plat}")


async def notify_icon_deals(plat, clear=True):
    deals_df = icon_dip_strategy(plat)
    if clear:
        await clear_channel(f"icon_{plat}")
    if not deals_df.empty:
        for _, row in deals_df.head(5).iterrows():
            msg = (
                f"💎 **Icon Dip on {plat.upper()}: {row['name']} ({row['version']}, {row['rating']} OVR)**\n"
                f"📉 Drop: {row['drop_%']}% (last {row['recent_sales']} sale(s) vs {row['baseline_sales']}-sale baseline)\n"
                f"🟢 Buy ~ {row['suggested_buy']:,} ({_buy_tag(row)})\n"
                f"🔴 Sell ~ {row['suggested_sell_raw']:,}\n"
                f"💰 Profit: {row['potential_profit']:,} ({row['profit_margin_%']}%)\n"
                f"🏷️ Rating: {row['investment_rating']}"
            )
            await send_message(msg, f"icon_{plat}")
    else:
        await send_message(f"**No Icon dip candidates found this hour on {plat.upper()}.**", f"icon_{plat}")



async def notify_early_game_deals(plat):
    """Early-game deceleration signal - shares the gold_{plat} channel with
    notify_drop_deals, since both are gold-card buy signals. Same caveat as
    the icon channel sharing above: each clears the channel before posting
    its own batch, so don't enable both notify_drop_deals and this one for
    the same platform/cycle unless overwriting the other's batch is fine."""
    deals_df = early_game_strategy(plat)
    await clear_channel(f"gold_{plat}")
    if not deals_df.empty:
        for _, row in deals_df.head(5).iterrows():
            msg = (
                f"🌱 **Early-Game Signal on {plat.upper()}: {row['name']} ({row['version']})**\n"
                f"📉 Trend: {row['prior_daily_change_%']}% → {row['latest_daily_change_%']}% day-over-day (decelerating)\n"
                f"📅 Days live: {row['days_live']}\n"
                f"🟢 Suggested Buy ~ {row['suggested_buy']:,} ({_buy_tag(row)})"
            )
            await send_message(msg, f"gold_{plat}")
    else:
        await send_message(f"**No early-game buy candidates found this hour on {plat.upper()}.**", f"gold_{plat}")


async def notify_positions(guild, member, user_id, platform):
    """Check a user's open positions and, if any are worth selling, post one
    summary embed into their private tracker channel."""
    embed = check_positions(user_id, platform)
    if embed is None:
        return

    channel = await get_or_create_tracker_channel(guild, member, category=None)
    await channel.send(embed=embed)