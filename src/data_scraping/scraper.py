import asyncio
import requests
from bs4 import BeautifulSoup
import datetime
import random
from collections import defaultdict
from unidecode import unidecode
import re
import pytz
import aiohttp
from src.db_utils import insert_card_stats, insert_card, insert_card_playstyles, insert_card_roles, async_insert_sale_db, get_connection

BASE_URL = "https://www.futbin.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def extract_card_id(href: str) -> int | None:
    match = re.search(r"/player/(\d+)/", href)
    return int(match.group(1)) if match else None

def collect_all_hrefs(version):
    hrefs = set()
    new_hrefs = 0
    conn = get_connection()

    # Load existing hrefs from DB
    with conn.cursor() as cur:
        cur.execute("SELECT href FROM hrefs WHERE version=%s", (version,))
        for row in cur.fetchall():
            hrefs.add(row['href']) 

    page_num = 1

    while True:
        url = f"{BASE_URL}/27/players?page={page_num}&version={version}"
        print(f"[Page {page_num}] Fetching {url}")

        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print(f"Failed to fetch page {page_num}")
            break

        soup = BeautifulSoup(response.text, "html.parser")
        rows = soup.find_all("tr", class_="player-row")

        # Stop only when page has no rows at all
        if not rows:
            print(f"No player rows found, stopping at page {page_num}")
            break

        page_new_hrefs = 0
        new_entries = []

        for row in rows:
            name_tag = row.find("a", class_="table-player-name")
            if name_tag and "href" in name_tag.attrs:
                href = name_tag["href"]
                card_id = extract_card_id(href)
                version_detail = row.find("div", class_="table-player-revision")
                price = row.find("div", class_="price")
                if "SBC" in version_detail.get_text():
                    continue
                if price:
                    price_val = price.get_text(strip=True).replace(",", "")
                    if price_val == "0":
                        continue
                else:
                    continue

                if href not in hrefs:
                    hrefs.add(href)
                    page_new_hrefs += 1
                    new_hrefs += 1
                    new_entries.append((card_id, href, version))

        # Bulk insert new hrefs into DB
        if new_entries:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO hrefs (card_id, href, version)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE card_id=card_id;
                """, new_entries)
            conn.commit()

        print(f"Page {page_num}: collected {page_new_hrefs} new hrefs")
        page_num += 1

    print(f"Collected {new_hrefs} new hrefs in total.")
    conn.close()
    return list(hrefs)




def load_meta_hrefs(version, min_price=5000):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT c.href
                FROM hrefs c
                LEFT JOIN market_sales ms ON c.card_id = ms.card_id
                WHERE c.version = %s
                  AND (ms.sold_price > %s OR ms.sold_price IS NULL);
            """, (version, min_price))
            rows = cur.fetchall()
            return [row['href'] for row in rows]
    finally:
        conn.close()



async def scrape_players(version):

    # Load hrefs
    hrefs = load_meta_hrefs(version)
    print(f"Loaded {len(hrefs)} hrefs.")

    sem = asyncio.Semaphore(2)  # concurrency limit

    async def process_player(href):
        async with sem:
            await asyncio.sleep(random.uniform(0.5,2))
            try:
                # Extract card_id from href
                card_id = int(href.split("/")[3])

                # Check if metadata already exists in DB
                conn = get_connection()
                metadata_exists = False
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM cards WHERE card_id=%s LIMIT 1", (card_id,))
                        metadata_exists = cur.fetchone() is not None
                finally:
                    conn.close()

                if not metadata_exists:
                    # Scrape full metadata
                    metadata = await asyncio.to_thread(scrape_player, href)
                    if not metadata:
                        print(f"Skipped player {href} because metadata could not be scraped")
                        return None

                    # Insert metadata into DB
                    insert_card(card_id, metadata["details"], "27")
                    insert_card_stats(card_id, metadata["stats"])
                    insert_card_roles(card_id, metadata["roles"])
                    insert_card_playstyles(card_id, metadata["playstyles"])
                else:
                    print(f"Metadata already exists for player {card_id}, skipping scraping")
                    metadata = None  # we don't need metadata for printing

                # Always scrape market sales
                sales_href = href.replace("player", "sales")
                sales = await get_sales(sales_href)
                all_prices = []
                for platform, s in sales.items():
                    for sale in s:
                        sale["platform"] = platform
                        all_prices.append(sale)

                await async_insert_sale_db(card_id, all_prices)
                print(f"✅ Processed player {card_id} (metadata {'exists' if metadata_exists else 'added'})")

                return card_id

            except Exception as e:
                print(f"Error scraping {href}: {e}")
                return None

    tasks = [process_player(href) for href in hrefs]
    for coro in asyncio.as_completed(tasks):
        await coro

    return



def normalize_column(stat_name: str) -> str:
    """
    Normalize stat name:
    - Remove special characters
    - Replace spaces with underscores
    - Convert to lowercase
    """
    stat_name = re.sub(r'[^A-Za-z0-9 ]+', '', stat_name)
    stat_name = stat_name.replace(" ", "_").lower()
    return stat_name

# Scrapes Specific Futbin Player Metadata
def scrape_player(href):

    url = f"https://www.futbin.com{href}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Failed to fetch {href}")
        return None

    soup = BeautifulSoup(response.text, 'html.parser')

    player_info_box = soup.find("div", class_="player-header-info-box")
    player_card = soup.find("div", class_="playercard-l")

    card_id = int(href.split("/")[3])

    # Get Player Name
    if player_card:
        # Try multiple ways to get name
        if player_card.has_attr("title") and player_card["title"]:
            name = unidecode(player_card["title"].strip())
        else:
            name_div = player_card.find("div", class_="player-name")
            if name_div and name_div.text:
                name = unidecode(name_div.text.strip())

    # Get Player Rating
    rating_tag = player_card.select_one("div.playercard-27-rating")
    if rating_tag:
        rating_text = rating_tag.get_text(strip=True)
        # extract only digits
        match = re.search(r"\d+", rating_text)
        rating = int(match.group()) if match else None



    # Get Player Position
    position_tag = player_card.select_one("div[class*='position']")
    position = position_tag.text.strip() if position_tag else None

    # Get Player Info

    club_tag = player_info_box.select_one("img[alt*='Club']")
    club = unidecode(club_tag['title'])

    nation_tag = player_info_box.select_one("img[alt*='Nation']")
    nation = unidecode(nation_tag['title'])

    league_tag = player_info_box.select_one("img[alt*='League']")
    league = unidecode(league_tag['title'])

    version_tag = soup.select_one("a[href*='version='] span.text-ellipsis")
    version = version_tag.get_text(strip=True) if version_tag else None

    player_info_grid = soup.find("div", class_="player-info-box-player-info-grid")
    rows = player_info_grid.find_all("div", class_="xxs-row xs-font align-center")

    weakfoot = rows[0].find_all("div")[1].get_text(strip=True)

    skills =  rows[1].find_all("div")[1].get_text(strip=True)

    height_text =  rows[2].find_all("div")[1].get_text(strip=True)
    height = int(re.search(r'\d+', height_text).group())

    # Get Player PS and Roles
    playstyle_wrapper = soup.find("div", class_="player-abilities-wrapper")
    playstyles = []

    if playstyle_wrapper:
        # Only look inside this wrapper
        playstyle_tags = playstyle_wrapper.find_all("a", class_="playStyle-table-icon")

        for tag in playstyle_tags:
            if tag.find_parent(class_="hidden"):
                continue
            name_div = tag.find("div")
            playstyle_name = name_div.text.strip() if name_div else None
            
            classes = tag.get("class", [])
            is_plus = "psplus" in classes
            
            playstyles.append({
                "playstyle": playstyle_name,
                "plus": is_plus
            })
    
    roles = []

    role_boxes = soup.select(".player-roles-wrapper .xxs-row.align-center")
    for box in role_boxes:
        if box.find_parent(class_="hidden"):
            continue
        # position (ST, LW, etc.)
        position_tag = box.find("div", class_="xs-font uppercase text-faded")
        position = position_tag.text.strip() if position_tag else None

        # role name and plus strength
        role_tag = box.find("a")
        if role_tag:
            # Everything before the nested <div> is the role name
            role_name = role_tag.contents[0].strip()

            # The nested <div> contains +, ++, etc.
            plus_tag = role_tag.find("div")
            strength = plus_tag.text.count("+") if plus_tag else 0
        
            roles.append({
                "position": position,
                "role": role_name,
                "plus": strength
            })
    
    # Extract Accelerate
    accelerate_tag = soup.select_one("a.accelerate-bar:not(.hidden) .player-accelerate-text")

    if accelerate_tag:
        accelerate_text = accelerate_tag.get_text(" ", strip=True)
        match = re.search(r'\b(?:Explosive|Controlled|Lengthy)\b', accelerate_text, re.I)
        accelerate = match.group(0).capitalize() if match else None
    else:
        accelerate = None

    
    # Extract Player Stats
    stats_categories = {
        "pace": "1",
        "shooting": "2",
        "passing": "3",
        "dribbling": "4",
        "defending": "5",
        "physical": "6"
    }

    all_stats = {}

    for category, stat_id in stats_categories.items():
        wrapper = soup.find("div", {"data-base-stat-id": stat_id})
        values = {}
        if wrapper:
            for stat_div in wrapper.select(".player-stat-value"):
                stat_name_div = stat_div.find_previous("div", class_="player-stat-name")
                stat_name = stat_name_div.text.strip() if stat_name_div else "Unknown"

                stat_name = normalize_column(stat_name)
                normalized_category = normalize_column(category)
                if stat_name == normalized_category:
                    stat_name = f"{stat_name}_overall"
                stat_value = stat_div.get("data-stat-value", None)
                values[stat_name] = stat_value
        all_stats[category] = values

    player_details = {
        "name": name,
        "rating": rating,
        "position": position,
        "version": version,
        "club": club,
        "nation": nation,
        "league": league,
        "weakfoot": weakfoot,
        "skills": skills,
        "height": height,
        "accelerate": accelerate
    }
    
    return {
        "id": card_id,
        "details": player_details,
        "playstyles": playstyles,
        "roles": roles,
        "stats": all_stats
    }

# ========== Prices ==========

# Scrape Live Hourly Prices
async def fetch_sales(session, url):
    """Fetch page content asynchronously."""
    async with session.get(url, headers=HEADERS) as resp:
        return await resp.text()



def parse_sales(html):
    try:
        soup = BeautifulSoup(html, "html.parser")
        sales_table = soup.find("tbody")
        if sales_table is None:
            print(f"No sales table found")
            return []

        sales_data = []
        uk = pytz.timezone("Europe/London")
        adelaide = pytz.timezone("Australia/Adelaide")
        cutoff = adelaide.localize(datetime.datetime(2024, 1, 1))

        for row in sales_table.find_all("tr"):
            cols = row.find_all("td")

            # Parse date/time
            date_span = cols[0].find("span", class_="sales-date-time")
            sale_time_str = date_span.get_text(strip=True) if date_span else None
            if sale_time_str:
                naive_dt = datetime.datetime.strptime(sale_time_str, "%b %d, %I:%M %p")
                naive_dt = naive_dt.replace(year=datetime.datetime.now().year)
                uk_dt = uk.localize(naive_dt)           # make it aware
                adelaide_dt = uk_dt.astimezone(adelaide)
            else:
                adelaide_dt = None

            # Parse prices
            price_text = cols[1].get_text(strip=True)
            price = int(price_text.replace(",", "")) if price_text else None

            sold_price_text = cols[2].get_text(strip=True)
            try:
                sold_price = int(sold_price_text.replace(",", "")) if sold_price_text else 0
            except ValueError:
                sold_price = 0

            # Sale type
            type_div = cols[5].find("div", class_="inline-popup-content")
            sale_type = type_div.get_text(strip=True) if type_div else None

            if adelaide_dt and adelaide_dt >= cutoff:
                sales_data.append({
                    "sale_time": adelaide_dt,  # now fully aware datetime
                    "listed_price": price,
                    "sold_price": sold_price,
                    "sale_type": sale_type
                })

    except Exception as e:
        print(f"Error parsing sales: {e}")

    return sales_data



async def get_sales(sales_href):
    platforms = ["pc", "ps"]
    async with aiohttp.ClientSession() as session:
        tasks = []
        for platform in platforms:
            url = f"{BASE_URL}{sales_href}?platform={platform}"
            tasks.append(fetch_sales(session, url))

        html_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Parse HTML for each platform
    sales_by_platform = {}
    for platform, html in zip(platforms, html_results):
        if isinstance(html, str):
            sales_by_platform[platform] = parse_sales(html)
        else:
            sales_by_platform[platform] = []

    return sales_by_platform

