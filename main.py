from src.data_scraping.scraper import scrape_players, collect_all_hrefs
from src.db_utils import initcardTable
import asyncio

async def main():

    # Init Tables
    initcardTable()

    versions = ["gold_rare", "base_icon"]

    # Scraping Task

    for version in versions:
        collect_all_hrefs(version)  # synchronous
        # await scrape_players(version)  # async
    
    print("Finished Scraping Process!")


if __name__=="__main__":
    asyncio.run(main())