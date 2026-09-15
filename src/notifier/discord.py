import discord
import pymysql
from src.notifier.card_cache import search_cards_fuzzy, refresh_card_cache
from src.database.db_utils import insert_position, get_or_create_user, get_user_id, fetch_open_positions, fetch_closed_positions, close_position, fetch_total_profit
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

def send_message(message: str, version):
    # if not DISCORD_WEBHOOK:
    #     print("⚠️ No Discord webhook set.")
    #     return
    # webhook_client.post(content=message)
    return



async def get_or_create_tracker_channel(guild, user, category):
    existing = discord.utils.get(guild.text_channels, name=f"trades-{user.name}".lower())
    if existing:
        return existing
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    channel = await guild.create_text_channel(f"trades-{user.name}", overwrites=overwrites, category=category)
    return channel



# ------------------- USER COMMANDS -------------------
# Every slash command is just a function below, decorated with @tree.command.
# Add as many as you want by copying this pattern - each new function becomes
# a new "/" command the moment the bot restarts and re-syncs.
 
@tree.command(name="ping", description="Check the bot is alive", guild=GUILD_ID)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong", ephemeral=True)


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
async def positions(interaction: discord.Interaction):
    user_id = await asyncio.to_thread(get_or_create_user, interaction.user.id, interaction.user.display_name)
    profit = await asyncio.to_thread(fetch_total_profit, user_id)

    embed = discord.Embed(title="Your Total Profit", color=discord.Color.blurple())

    if profit < 0:
        name="😬 YIKES! Are you trying to lose coins?"
    elif 0 < profit < 50000:
        name="🥱 Not bad still room to improve though"
    else:
        name="🤯 Your a real trader keep going!"

    embed.add_field(
        name=f"{name}",
        value=(
            f"Realised Profit: {profit}\n"
        ),
        inline=False
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


    