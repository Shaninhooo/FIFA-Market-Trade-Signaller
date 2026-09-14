import discord
from src.notifier.card_cache import search_cards_fuzzy, refresh_card_cache
from discord import app_commands
import os

 
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID")))  # your server's ID
 
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    synced = await tree.sync(guild=GUILD_ID)
    print(f"Logged in as {client.user} - synced {len(synced)} command(s)")
    
    # channel = client.get_channel(CHANNEL_ID)
    
    # if channel:
    #     # Only delete messages that are not pinned
    #     deleted = await channel.purge(limit=1000, check=lambda m: not m.pinned)
    #     print(f"Deleted {len(deleted)} messages (pinned messages preserved)")
    

# ------------------- BOT FUNCTIONS -------------------

def send_message(message: str, version):
    if not DISCORD_WEBHOOK:
        print("⚠️ No Discord webhook set.")
        return
    webhook_client.post(content=message)



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
    card_id = int(card)  # guaranteed exact - it came from the dropdown, not typed
    # TODO: insert `qty` rows into positions with buy_price=price, buy_time=now(), card_id=card_id
    await interaction.response.send_message(
        f"Logged {qty}x buy for card {card_id} at {price:,} coins.", ephemeral=True
    )

@buy.autocomplete("card")
async def card_autocomplete(interaction: discord.Interaction, current: str):
    results = search_cards_fuzzy(current)
    return [app_commands.Choice(name=label[:100], value=str(card_id)) for card_id, label in results]
    
 
 
@tree.command(name="positions", description="List your open positions", guild=GUILD_ID)
async def positions(interaction: discord.Interaction):
    # TODO: query positions WHERE user_id = ... AND status = 'open'
    await interaction.response.send_message("You have no open positions yet.", ephemeral=True)

@tree.command(name="sell", description="Sell your positions", guild=GUILD_ID)
async def positions(interaction: discord.Interaction):
    # TODO: query positions WHERE user_id = ... AND status = 'open'
    await interaction.response.send_message("You have no open positions yet.", ephemeral=True)
 
@tree.command(name="history", description="List your recent trade history", guild=GUILD_ID)
async def positions(interaction: discord.Interaction):
    # TODO: query positions WHERE user_id = ... AND status = 'open'
    await interaction.response.send_message("You have no open positions yet.", ephemeral=True)
