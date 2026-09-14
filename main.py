from src.data_scraping.scraper import scrape_fc26_players, collect_all_hrefs
from src.db_utils import initcardTable
import asyncio

async def main():

    # Init Tables
    initcardTable()

    versions = ["gold_rare", "base_icon"]


    # Scraping Task

    for version in versions:
        collect_all_hrefs(version)  # synchronous
        # await scrape_fc27_players(version)  # async
    
    print("Finished Scraping Process!")


# Using the special variable 
# __name__
if __name__=="__main__":
    asyncio.run(main())