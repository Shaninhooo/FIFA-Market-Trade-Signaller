import discord
import pymysql
from src.bot.card_cache import search_cards_fuzzy, refresh_card_cache
from src.database.db_utils import insert_position, get_or_create_user, get_user_id, set_user_platform, get_user_platform, fetch_open_positions, fetch_closed_positions, close_position, fetch_total_profit, set_share_stats, fetch_leaderboard, fetch_top_trades
from src.strategy.market_index import get_market_index_snapshot, is_crash_mode
from discord import app_commands
import os
import asyncio
from datetime import datetime, timezone

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID")))  # your server's ID
 
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    await asyncio.to_thread(refresh_card_cache)  # no conn argument anymore
    synced = await tree.sync(guild=GUILD_ID)
    print(f"Logged in as {client.user} - synced {len(synced)} command(s)")
    
    # channel = client.get_channel(CHANNEL_ID)
    
    # if channel:
    #     # Only delete messages that are not pinned
    #     deleted = await channel.purge(limit=1000, check=lambda m: not m.pinned)
    #     print(f"Deleted {len(deleted)} messages (pinned messages preserved)")
    

# ------------------- BOT FUNCTIONS -------------------

# Each deal feed (card type x platform) posts to its own fixed Discord
# channel rather than one auto-created/discovered by name - set the matching
# *_CHANNEL_ID in .env to the real channel's ID (Discord: enable Developer
# Mode, right-click the channel, "Copy Channel ID"). A key with no env var
# set, or one pointing at a channel the bot can't see, just logs and skips.
DEAL_CHANNEL_IDS = {
    "hero_pc": os.getenv("HERO_PC_CHANNEL_ID"),
    "hero_ps": os.getenv("HERO_PS_CHANNEL_ID"),
    "icon_pc": os.getenv("ICON_PC_CHANNEL_ID"),
    "icon_ps": os.getenv("ICON_PS_CHANNEL_ID"),
    "gold_pc": os.getenv("GOLD_PC_CHANNEL_ID"),
    "gold_ps": os.getenv("GOLD_PS_CHANNEL_ID"),
}


def _get_deal_channel(channel_key: str):
    channel_id = DEAL_CHANNEL_IDS.get(channel_key)
    if not channel_id:
        print(f"No channel configured for '{channel_key}' - set its *_CHANNEL_ID in .env")
        return None

    channel = client.get_channel(int(channel_id))
    if channel is None:
        print(f"Couldn't find channel {channel_id} for '{channel_key}' - check the ID and that the bot has access to it")
        return None

    return channel


async def clear_channel(channel_key: str):
    """Delete every message currently in the channel configured for
    `channel_key`, so a fresh batch of alerts replaces the previous cycle's
    instead of piling up underneath it. Only deletes messages <14 days old -
    Discord's bulk-delete API can't touch anything older, but a deal feed
    should never have anything that stale sitting in it anyway."""
    channel = _get_deal_channel(channel_key)
    if channel is None:
        return

    try:
        await channel.purge(limit=100)
    except discord.Forbidden:
        print(f"Missing permission to clear #{channel.name} - grant the bot Manage Messages.")


async def send_message(message: str, channel_key: str):
    """Post `message` into the Discord channel configured for `channel_key`
    (e.g. "hero_pc") - see DEAL_CHANNEL_IDS above."""
    channel = _get_deal_channel(channel_key)
    if channel is None:
        return

    await channel.send(message)



async def get_or_create_tracker_channel(guild, user, category):
    existing = discord.utils.get(guild.text_channels, name=f"position-tracker-{user.name}".lower())
    if existing:
        return existing
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(f"position-tracker-{user.name}", overwrites=overwrites, category=category)
    return channel



# ------------------- USER COMMANDS -------------------
# Every slash command is just a function below, decorated with @tree.command.
# Add as many as you want by copying this pattern - each new function becomes
# a new "/" command the moment the bot restarts and re-syncs.
 
@tree.command(name="ping", description="Check the bot is alive", guild=GUILD_ID)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong", ephemeral=True)


@tree.command(name="create_tracker", description="Sign up for position tracker that checks your positions and notifies you for sell opportunities", guild=GUILD_ID)
@app_commands.describe(platform="Which platform you trade on")
@app_commands.choices(platform=[
    app_commands.Choice(name="PC", value="pc"),
    app_commands.Choice(name="PlayStation", value="ps"),
])
async def create_tracker(interaction: discord.Interaction, platform: app_commands.Choice[str]):
    await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    await asyncio.to_thread(set_user_platform, interaction.user.id, platform.value)

    try:
        channel = await get_or_create_tracker_channel(interaction.guild, interaction.user, category=None)
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to create channels here - ask a server admin to grant me Manage Channels.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"You're all set - your tracker channel is {channel.mention}.", ephemeral=True
    )

# Buy Command
@tree.command(name="buy", description="Log a buy you made on the market", guild=GUILD_ID)
@app_commands.describe(card="Card you bought", price="Price you paid", qty="How many copies")
async def buy(interaction: discord.Interaction, card: str, price: int, qty: int = 1):
    try:
        card_id = int(card)
    except ValueError:
        await interaction.response.send_message(
            "Please pick a card from the autocomplete list.", ephemeral=True
        )
        return

    if price <= 0 or qty <= 0:
        await interaction.response.send_message(
            "Price and quantity must both be positive.", ephemeral=True
        )
        return

    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    buy_time = datetime.now(timezone.utc)

    try:
        await asyncio.to_thread(insert_position, user_id, card_id, price, buy_time, qty)
    except pymysql.err.IntegrityError:
        await interaction.response.send_message(
            "That card doesn't look valid - please pick one from the autocomplete list.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Logged {qty}x buy for card {card_id} at {price:,} coins.", ephemeral=True
    )

@buy.autocomplete("card")
async def card_autocomplete(interaction: discord.Interaction, current: str):
    results = search_cards_fuzzy(current)
    return [app_commands.Choice(name=label[:100], value=str(card_id)) for card_id, label in results]
    
 
 
@tree.command(name="positions", description="List your open positions", guild=GUILD_ID)
async def list_positions(interaction: discord.Interaction):
    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    rows = await asyncio.to_thread(fetch_open_positions, user_id)

    if not rows:
        await interaction.response.send_message("You have no open positions yet.", ephemeral=True)
        return

    embed = discord.Embed(title="Your Open Positions", color=discord.Color.blurple())
    for row in rows:
        if row["target_price_low"] and row["target_price_high"]:
            target = f"{row['target_price_low']:,} - {row['target_price_high']:,}"
        else:
            target = "—"

        embed.add_field(
            name=f"{row['name']} ({row['version']})",
            value=(
                f"Qty: {row['quantity']} @ {row['buy_price']:,}\n"
                f"Target: {target}\n"
                f"Bought: {row['buy_time'].strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="sell", description="Sell one of your open positions", guild=GUILD_ID)
@app_commands.describe(position="Position to sell", sell_price="Price you sold for")
async def sell(interaction: discord.Interaction, position: str, sell_price: int):
    try:
        position_id = int(position)
    except ValueError:
        await interaction.response.send_message(
            "Please pick a position from the autocomplete list.", ephemeral=True
        )
        return

    if sell_price <= 0:
        await interaction.response.send_message("Sell price must be positive.", ephemeral=True)
        return

    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    sell_time = datetime.now(timezone.utc)

    closed = await asyncio.to_thread(close_position, user_id, position_id, sell_price, sell_time)
    if not closed:
        await interaction.response.send_message(
            "Couldn't find that open position - please pick one from the autocomplete list.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Closed position for {sell_price:,} coins.", ephemeral=True
    )

@sell.autocomplete("position")
async def position_autocomplete(interaction: discord.Interaction, current: str):
    user_id = await asyncio.to_thread(get_user_id, interaction.user.id)
    if user_id is None:
        return []

    rows = await asyncio.to_thread(fetch_open_positions, user_id, 25)
    current = current.lower()
    choices = []
    for row in rows:
        label = f"{row['name']} ({row['version']}) x{row['quantity']} @ {row['buy_price']:,}"
        if current and current not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=str(row["position_id"])))
    return choices

@tree.command(name="history", description="List your recent trade history", guild=GUILD_ID)
async def positions(interaction: discord.Interaction):
    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    rows = await asyncio.to_thread(fetch_closed_positions, user_id)

    if not rows:
        await interaction.response.send_message("You have no closed yet.", ephemeral=True)
        return

    embed = discord.Embed(title="Your Closed Positions", color=discord.Color.blurple())
    for row in rows:
        embed.add_field(
            name=f"{row['name']} ({row['version']})",
            value=(
                f"Qty: {row['quantity']}, {row['buy_price']:,} @ {row['sell_price']:,}\n"
                f"Realised Profit: {row["realized_profit"]}\n"
                f"Sold: {row['sell_time'].strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="flex", description="See your total realised profit", guild=GUILD_ID)
async def flex(interaction: discord.Interaction):
    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    stats = await asyncio.to_thread(fetch_total_profit, user_id)
    profit = stats["total_realized_profit"] if stats else 0

    embed = discord.Embed(title="Your Total Profit", color=discord.Color.blurple())

    if profit < 0:
        name = "😬 YIKES! Are you trying to lose coins?"
    elif profit == 0:
        name = "🌱 Nothing realised yet - go make a trade!"
    elif profit < 50000:
        name = "🥱 Not bad, still room to improve though"
    else:
        name = "🤯 You're a real trader, keep going!"

    embed.add_field(
        name=name,
        value=f"Total Realised Profit: {profit:,}\n",
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="leaderboard_optin", description="Choose whether to appear on /leaderboard", guild=GUILD_ID)
@app_commands.describe(share="Show your stats on the leaderboard?")
async def leaderboard_optin(interaction: discord.Interaction, share: bool):
    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    await asyncio.to_thread(set_share_stats, user_id, share)

    if share:
        await interaction.response.send_message("You're now visible on the leaderboard.", ephemeral=True)
    else:
        await interaction.response.send_message("You've been removed from the leaderboard.", ephemeral=True)


@tree.command(name="leaderboard", description="See the top traders by realised profit", guild=GUILD_ID)
async def leaderboard(interaction: discord.Interaction):
    rows = await asyncio.to_thread(fetch_leaderboard)

    if not rows:
        await interaction.response.send_message(
            "Nobody's opted in to the leaderboard yet - run /leaderboard_optin to be the first!",
            ephemeral=True
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title="🏆 Leaderboard - Top Traders", color=discord.Color.gold())
    for i, row in enumerate(rows):
        rank = medals[i] if i < len(medals) else f"#{i + 1}"
        avg_win = f"{row['avg_profit_per_win']:,}" if row["avg_profit_per_win"] is not None else "—"
        embed.add_field(
            name=f"{rank} {row['display_name']}",
            value=(
                f"Total Profit: {row['total_realized_profit']:,}\n"
                f"Closed Trades: {row['closed_trades']}\n"
                f"Avg Profit/Win: {avg_win}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=False)


@tree.command(name="top_trades", description="See the best individual trades made recently", guild=GUILD_ID)
@app_commands.describe(days="How many days back to look (default 7)")
async def top_trades(interaction: discord.Interaction, days: int = 7):
    if days <= 0:
        await interaction.response.send_message("Days must be positive.", ephemeral=True)
        return

    rows = await asyncio.to_thread(fetch_top_trades, days)

    if not rows:
        await interaction.response.send_message(
            "No qualifying trades in that window - either nobody's sold, or nobody's opted in via /leaderboard_optin.",
            ephemeral=True
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title=f"💎 Top Trades - Last {days} Days", color=discord.Color.gold())
    for i, row in enumerate(rows):
        rank = medals[i] if i < len(medals) else f"#{i + 1}"
        embed.add_field(
            name=f"{rank} {row['name']} ({row['version']}) — {row['display_name']}",
            value=(
                f"Qty: {row['quantity']}, {row['buy_price']:,} → {row['sell_price']:,}\n"
                f"Profit: {row['realized_profit']:,}\n"
                f"Sold: {row['sell_time'].strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=False)


@tree.command(name="market_index", description="See whether the market looks normal or is crashing right now", guild=GUILD_ID)
async def market_index(interaction: discord.Interaction):
    # Discord needs an ack within 3 seconds or the interaction times out
    # ("The application did not respond") - the two DB calls below (the
    # second one a real aggregate query over market_sales/cards) can easily
    # take longer than that, so defer immediately and send the real result
    # as a followup once it's ready.
    await interaction.response.defer(ephemeral=True)

    platform = await asyncio.to_thread(get_user_platform, interaction.user.id)
    if platform is None:
        await interaction.followup.send(
            "Run /create_tracker first so I know which platform to check.", ephemeral=True
        )
        return

    snapshot = await asyncio.to_thread(get_market_index_snapshot, platform)
    if snapshot is None:
        await interaction.followup.send(
            "Not enough liquid market data right now to read the index - try again later.", ephemeral=True
        )
        return

    pct = snapshot["median_change_pct"]
    crash = is_crash_mode(snapshot)

    if crash:
        title = "🔴 Market Crash Mode"
        description = "The broad market is down significantly right now - this looks systemic, not just one card."
        color = discord.Color.red()
    elif pct <= -2:
        title = "🟡 Market Softening"
        description = "The market's trending down a bit, but not crash territory."
        color = discord.Color.orange()
    elif pct >= 2:
        title = "🟢 Market Rising"
        description = "The market's trending up right now."
        color = discord.Color.green()
    else:
        title = "⚪ Market Normal"
        description = "Nothing unusual - the market's roughly flat."
        color = discord.Color.light_grey()

    embed = discord.Embed(title=title, description=description, color=color)
    embed.add_field(name="Median Move", value=f"{pct:+.2f}%", inline=True)
    embed.add_field(name="Sample Size", value=f"{snapshot['sample_size']} cards", inline=True)
    embed.add_field(name="Platform", value=platform.upper(), inline=True)

    await interaction.followup.send(embed=embed, ephemeral=True)
