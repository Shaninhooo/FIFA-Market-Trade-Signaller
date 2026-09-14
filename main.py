from src.data_scraping.scraper import hourly_scrape
from src.database.db_schema import initcardTable
from src.deal_finder.deal_finder import drop_strategy, icon_fluctuation_strategy
from src.notifier.discord import client, DISCORD_TOKEN, send_message
from src.notifier.notify import notify_drop_deals, notify_icon_fluctuations
import asyncio

SCRAPE_INTERVAL_SECONDS = 3600

async def hourly_loop():
    await client.wait_until_ready()  # don't try to post before the bot's actually logged in
    while True:
        start = asyncio.get_event_loop().time()
        try:
            # Scrape Market Data Hourly
            await hourly_scrape()

            # Then run market strategies and send deals
            platforms = ["pc", "ps"]
            for platform in platforms:
                buy_df = drop_strategy(platform)
                notify_drop_deals(buy_df, platform)
        
                fluctuation_df = icon_fluctuation_strategy(platform)
                notify_icon_fluctuations(fluctuation_df, platform)

        except Exception as e:
            print(f"Hourly loop error: {e}")  # log and continue - see note below on why this matters

        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0, SCRAPE_INTERVAL_SECONDS - elapsed))

async def main():
    await asyncio.gather(
        client.start(DISCORD_TOKEN),
        # hourly_loop(),
    )

if __name__=="__main__":
    asyncio.run(main())