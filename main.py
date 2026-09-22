import discord
from src.scraper import main_scrape
from src.database.db_schema import initcardTable
from src.bot.discord import client, DISCORD_TOKEN, GUILD_ID
from src.database.db_utils import fetch_trackable_users
from src.bot.notify import notify_positions, notify_hero_deals, notify_icon_deals, notify_early_game_deals, notify_hero_fluctuations, notify_icon_fluctuations
import asyncio
import traceback

SCRAPE_INTERVAL_SECONDS = 1800


async def position_check_all():
    """Run the position tracker for every user who's signed up with a platform."""
    guild = client.get_guild(GUILD_ID.id)
    if guild is None:
        print("Position check skipped - guild not found (bot not ready yet, or wrong DISCORD_GUILD_ID)")
        return

    users = await asyncio.to_thread(fetch_trackable_users)

    for user in users:
        try:
            discord_id = int(user["discord_id"])
            member = guild.get_member(discord_id) or await guild.fetch_member(discord_id)
        except discord.NotFound:
            continue  # they've left the server

        try:
            await notify_positions(guild, member, user["user_id"], user["platform"])
        except Exception as e:
            print(f"Position check failed for user {user['user_id']}: {e}")


async def repeated_loop():
    await client.wait_until_ready()  # don't try to post before the bot's actually logged in
    while True:
        start = asyncio.get_event_loop().time()
        try:
            # Scrape Market Data Hourly
            await main_scrape()

            # Then run market strategies and send deals
            platforms = ["pc", "ps"]
            for platform in platforms:
                # buy_df = drop_strategy(platform)
                # await notify_drop_deals(buy_df, platform)
                await notify_early_game_deals(platform)

                # Dip and fluctuation share hero_{plat}/icon_{plat} - only the
                # first call in each pair clears the channel (clear=True,
                # the default), so both batches land in the same post
                # instead of the second one wiping the first's messages.
                await notify_hero_deals(platform)
                await notify_hero_fluctuations(platform, clear=False)

                await notify_icon_deals(platform)
                await notify_icon_fluctuations(platform, clear=False)

            # Then check everyone's open positions for sell opportunities
            # await position_check_all()

        except Exception as e:
            print(f"Hourly loop error: {e}")  # log and continue - see note below on why this matters
            traceback.print_exc()

        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0, SCRAPE_INTERVAL_SECONDS - elapsed))

async def main():
    initcardTable()
    await asyncio.gather(
        client.start(DISCORD_TOKEN),
        repeated_loop(),
    )

if __name__=="__main__":
    asyncio.run(main())


    