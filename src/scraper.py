import asyncio
import os
import requests
from bs4 import BeautifulSoup
import datetime
import random
import time
from collections import defaultdict
from unidecode import unidecode
import re
import pytz
import aiohttp
from src.flaresolverr import fast_get, async_fast_get
from src.database.db_utils import insert_card_stats, insert_card, insert_card_playstyles, insert_card_roles, async_insert_sale_db, get_connection, fetch_meta_hrefs, fetch_all_hrefs, fetch_ver_href, insert_unique_event

BASE_URL = "https://www.futbin.com"
FLARESOLVERR_MAX_TIMEOUT_MS = 60000

def extract_card_id(href: str) -> int | None:
    match = re.search(r"/player/(\d+)/", href)
    return int(match.group(1)) if match else None


def classify_version(revision_text: str) -> str:
    """Bucket a row's own revision text (e.g. "Gold Rare", "Icon", "TOTW")
    into one of the canonical version filters the rest of the pipeline
    (fetch_meta_hrefs, scrape_players, main_scrape) tracks - gold/icon/team_of_the_week."""
    text = revision_text.lower()
    if "icon" in text:
        return "icon"
    if "totw" in text or "team of the week" in text:
        return "team_of_the_week"
    return "gold"


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
        # version is a real Futbin filter (e.g. "gold", "icon", "team_of_the_week") -
        # without it this endpoint returns an unfiltered, mixed-type listing, and
        # every row on it would get mislabeled with whatever `version` was passed in.
        if version == "icon":
            url = f"{BASE_URL}/27/players?page={page_num}&league=2118"
        elif version == "hero":
            url = f"{BASE_URL}/27/players?page={page_num}&club=114605"
        else:
            url = f"{BASE_URL}/27/players?version={version}&page={page_num}"
        print(f"[Page {page_num}] Fetching {url}")

        html = fast_get(url)
        if html is None:
            print(f"Failed to fetch page {page_num}")
            break

        soup = BeautifulSoup(html, "html.parser")
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
                if version_detail is None or "SBC" in version_detail.get_text():
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

        # Bulk insert new hrefs into DB. Updates href/version on conflict rather
        # than a no-op, so a card mislabeled by a past bug self-heals the next
        # time it's correctly rediscovered under its real version filter.
        if new_entries:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO hrefs (card_id, href, version)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE href = VALUES(href), version = VALUES(version);
                """, new_entries)
            conn.commit()

        print(f"Page {page_num}: collected {page_new_hrefs} new hrefs")

        if page_num >= 100:
            print("Reached page limit of 100, stopping")
            break

        page_num += 1
        time.sleep(random.uniform(0.5, 1.5))  # don't hammer the listing pages

    print(f"Collected {new_hrefs} new hrefs in total.")
    conn.close()
    return list(hrefs)


def collect_all_hrefs_all_versions():
    """Same as collect_all_hrefs, but doesn't need a caller-supplied version -
    it crawls Futbin's unfiltered listing once and classifies each row's own
    revision text into a version bucket, so one pass covers every version
    instead of one crawl per version."""
    hrefs = set()
    new_hrefs = 0
    conn = get_connection()

    # Load existing hrefs from DB (not scoped to a version - a href is unique regardless of it)
    with conn.cursor() as cur:
        cur.execute("SELECT href FROM hrefs")
        for row in cur.fetchall():
            hrefs.add(row['href'])

    page_num = 1

    while True:
        url = f"{BASE_URL}/27/players?page={page_num}"
        print(f"[Page {page_num}] Fetching {url}")

        html = fast_get(url)
        if html is None:
            print(f"Failed to fetch page {page_num}")
            break

        soup = BeautifulSoup(html, "html.parser")
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
                if version_detail is None or "SBC" in version_detail.get_text():
                    continue
                if price:
                    price_val = price.get_text(strip=True).replace(",", "")
                    if price_val == "0":
                        continue
                else:
                    continue

                version = classify_version(version_detail.get_text(strip=True))

                if href not in hrefs:
                    hrefs.add(href)
                    page_new_hrefs += 1
                    new_hrefs += 1
                    new_entries.append((card_id, href, version))

        if new_entries:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO hrefs (card_id, href, version)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE href = VALUES(href), version = VALUES(version);
                """, new_entries)
            conn.commit()

        print(f"Page {page_num}: collected {page_new_hrefs} new hrefs")

        if page_num >= 100:
            print("Reached page limit of 100, stopping")
            break

        page_num += 1
        time.sleep(random.uniform(0.5, 1.5))  # don't hammer the listing pages

    print(f"Collected {new_hrefs} new hrefs in total.")
    conn.close()
    return list(hrefs)


async def scrape_players(version):

    # Load hrefs

    hrefs = fetch_meta_hrefs(version, 3000)
    print(f"Loaded {len(hrefs)} {version} hrefs.")

    sem = asyncio.Semaphore(3)  # concurrency limit
    timeout = aiohttp.ClientTimeout(total=FLARESOLVERR_MAX_TIMEOUT_MS / 1000 + 10)  # FlareSolverr can take up to maxTimeout to solve a challenge

    async def process_player(href, session):
        async with sem:
            await asyncio.sleep(random.uniform(0.5,2))
            try:
                card_id = extract_card_id(href)

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
                    metadata = None  # we don't need metadata for printing

                # Always scrape market sales
                sales_href = href.replace("player", "sales")
                sales = await get_sales(sales_href, session)
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

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [process_player(href, session) for href in hrefs]
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
    html = fast_get(url)
    if html is None:
        print(f"Failed to fetch {href}")
        return None

    soup = BeautifulSoup(html, 'html.parser')

    player_info_box = soup.find("div", class_="player-header-info-box")
    player_card = soup.find("div", class_="playercard-l")

    card_id = extract_card_id(href)

    # Get Player Name
    name = None
    if player_card:
        # Try multiple ways to get name
        if player_card.has_attr("title") and player_card["title"]:
            name = unidecode(player_card["title"].strip())
        else:
            name_div = player_card.find("div", class_="player-name")
            if name_div and name_div.text:
                name = unidecode(name_div.text.strip())

    # Get Player Rating
    rating = None
    rating_tag = player_card.select_one("div.playercard-27-rating") if player_card else None
    if rating_tag:
        rating_text = rating_tag.get_text(strip=True)
        # extract only digits
        match = re.search(r"\d+", rating_text)
        rating = int(match.group()) if match else None

    # Get Player Position
    position_tag = player_card.select_one("div[class*='position']") if player_card else None
    position = position_tag.text.strip() if position_tag else None

    # Get Player Info

    club_tag = player_info_box.select_one("img[alt*='Club']") if player_info_box else None
    club = unidecode(club_tag['title']) if club_tag and club_tag.has_attr('title') else None

    nation_tag = player_info_box.select_one("img[alt*='Nation']") if player_info_box else None
    nation = unidecode(nation_tag['title']) if nation_tag and nation_tag.has_attr('title') else None

    league_tag = player_info_box.select_one("img[alt*='League']") if player_info_box else None
    league = unidecode(league_tag['title']) if league_tag and league_tag.has_attr('title') else None

    version_tag = soup.select_one("a[href*='version='] span.text-ellipsis")
    version = version_tag.get_text(strip=True) if version_tag else None

    def row_value(rows, index):
        """Second <div> of the Nth info row, or None if that row/div isn't there."""
        if index >= len(rows):
            return None
        divs = rows[index].find_all("div")
        return divs[1].get_text(strip=True) if len(divs) > 1 else None

    player_info_grid = soup.find("div", class_="player-info-box-player-info-grid")
    info_rows = player_info_grid.find_all("div", class_="xxs-row xs-font align-center") if player_info_grid else []

    weakfoot = row_value(info_rows, 0)
    skills = row_value(info_rows, 1)

    height_text = row_value(info_rows, 2)
    height_match = re.search(r'\d+', height_text) if height_text else None
    height = int(height_match.group()) if height_match else None

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
    """Fetch page content asynchronously, through FlareSolverr."""
    return await async_fast_get(session, url)



def parse_sales(html):
    sales_data = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        sales_table = soup.find("tbody")
        if sales_table is None:
            print(f"No sales table found")
            return []

        uk = pytz.timezone("Europe/London")
        adelaide = pytz.timezone("Australia/Adelaide")
        cutoff = adelaide.localize(datetime.datetime(2024, 1, 1))
        now = datetime.datetime.now()

        for row in sales_table.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 6:
                continue

            # Parse date/time
            date_span = cols[0].find("span", class_="sales-date-time")
            sale_time_str = date_span.get_text(strip=True) if date_span else None
            if sale_time_str:
                naive_dt = datetime.datetime.strptime(sale_time_str, "%b %d, %I:%M %p")
                # Sales scraped in January can still be dated in the prior December
                year = now.year - 1 if naive_dt.month == 12 and now.month == 1 else now.year
                naive_dt = naive_dt.replace(year=year)
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



async def get_sales(sales_href, session):
    platforms = ["pc", "ps"]
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


# Scrape Events
calendar_url = "https://fifauteam.com/fc-27-schedule/"


DEFAULT_TEAM_EVENT_DURATION_DAYS = 7  # fallback when a row's own duration span can't be parsed


def scrape_events(year=None):
    """Scrape fifauteam.com's FC 27 schedule for team-release events (the rows
    tagged class="team") and store each one in unique_events, skipping any
    (event_name, start_datetime) pair already recorded.

    The schedule table has no year in its "DD/MM" date headers, so `year`
    defaults to the current year - pass it explicitly if scraping a schedule
    that spans a year boundary.
    """
    if year is None:
        year = datetime.datetime.now().year

    html = fast_get(calendar_url)
    if html is None:
        print(f"Failed to fetch {calendar_url}")
        return

    soup = BeautifulSoup(html, "html.parser")
    uk = pytz.timezone("Europe/London")

    current_date = None  # "DD/MM" from the most recently seen header row
    new_events = 0

    for row in soup.find_all("tr"):
        if row.find("th") is not None:
            date_tag = row.find("strong")
            if date_tag:
                current_date = date_tag.get_text(strip=True)
            continue

        team_cell = row.find("td", class_="team")
        if team_cell is None or current_date is None:
            continue

        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        time_cell, content_cell = cells[1], cells[2]
        time_text = time_cell.get_text(" ", strip=True)

        time_match = re.match(r"(\d{1,2}:\d{2})", time_text)
        release_time = time_match.group(1) if time_match else "00:00"

        duration_match = re.search(r"(\d+)\s*day", time_text)
        duration_days = int(duration_match.group(1)) if duration_match else DEFAULT_TEAM_EVENT_DURATION_DAYS

        name_tag = content_cell.find("a")
        team_name = name_tag.get_text(strip=True) if name_tag else content_cell.get_text(strip=True)

        try:
            day, month = current_date.split("/")
            naive_dt = datetime.datetime.strptime(f"{day}/{month}/{year} {release_time}", "%d/%m/%Y %H:%M")
            start_datetime = uk.localize(naive_dt)
        except ValueError:
            print(f"Could not parse date '{current_date} {release_time}' for team '{team_name}'")
            continue

        end_datetime = start_datetime + datetime.timedelta(days=duration_days)

        if insert_unique_event(team_name, start_datetime, end_datetime):
            new_events += 1

    print(f"Collected {new_events} new team events.")




# Execute Hourly Scrape
async def main_scrape():
    # Solve once, up front, so all workers below start with a warm cookie
    # cache instead of racing to refresh it simultaneously on a cold start.
    warmup_ok = await asyncio.to_thread(fast_get, f"{BASE_URL}/27/players?version=gold&page=1")
    if warmup_ok is None:
        print("⚠️ Warmup solve failed — continuing anyway, workers will retry individually")
    
    # Collect Hrefs
    # collect_all_hrefs("icon")

    versions = ["Icon", "Hero", "Team of the  Week", "gold"]
    for version in versions:
        await scrape_players(version)
    
    print("✅ Scraping complete.")

