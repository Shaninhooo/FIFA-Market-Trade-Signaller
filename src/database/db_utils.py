import pymysql
import pandas as pd
import json
from dotenv import load_dotenv
import os
import asyncio
from dateutil import parser
import pytz

load_dotenv()

def get_connection():
    return pymysql.connect(
        host = os.getenv("MYSQLHOST"),
        user = os.getenv("MYSQLUSER"),
        password = os.getenv("MYSQLPASSWORD"),
        database = os.getenv("MYSQLDATABASE"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

# ------------------- DATA INSERTING -------------------


def insert_sale_db(card_id, sale_data):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Get current max sale_time per platform
            cur.execute(
                "SELECT platform, MAX(sale_time) as max_time FROM market_sales WHERE card_id = %s GROUP BY platform",
                (card_id,)
            )
            adelaide = pytz.timezone("Australia/Adelaide")
            max_times = {}
            for row in cur.fetchall():
                if row['max_time']:
                    # ensure max_time is timezone aware
                    if row['max_time'].tzinfo is None:
                        max_times[row['platform'].lower()] = adelaide.localize(row['max_time'])
                    else:
                        max_times[row['platform'].lower()] = row['max_time']

            values = []
            for point in sale_data:
                platform = point['platform'].lower()
                sale_time = point['sale_time']

                # Convert string to datetime if needed
                if isinstance(sale_time, str):
                    sale_time = parser.isoparse(sale_time)

                # Localize naive datetimes
                if sale_time.tzinfo is None:
                    sale_time = adelaide.localize(sale_time)

                # Skip older/duplicate entries
                if platform in max_times and sale_time <= max_times[platform]:
                    continue

                values.append((
                    card_id,
                    platform,
                    point['listed_price'],
                    point['sale_type'],
                    sale_time,
                    point['sold_price']
                ))

            if values:
                sql = """
                INSERT INTO market_sales (card_id, platform, listed_price, sale_type, sale_time, sold_price)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
                cur.executemany(sql, values)

        conn.commit()
    finally:
        conn.close()


async def async_insert_sale_db(card_id, sale_data):
    await asyncio.to_thread(insert_sale_db, card_id, sale_data)


def insert_card(card_id, card_details, game_num):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO cards (
                    card_id, name, game, version, nationality, league, club, position,
                    rating, weak_foot, skill_move, height, accelerate
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name=VALUES(name),
                    game=VALUES(game),
                    version=VALUES(version),
                    nationality=VALUES(nationality),
                    league=VALUES(league),
                    club=VALUES(club),
                    position=VALUES(position),
                    rating=VALUES(rating),
                    weak_foot=VALUES(weak_foot),
                    skill_move=VALUES(skill_move),
                    height=VALUES(height),
                    accelerate=VALUES(accelerate);
            """, (
                card_id,
                card_details.get("name"),
                game_num,
                card_details.get("version"),
                card_details.get("nation"),
                card_details.get("league"),
                card_details.get("club"),
                card_details.get("position"),
                card_details.get("rating"),
                card_details.get("weakfoot"),
                card_details.get("skills"),
                card_details.get("height"),
                card_details.get("accelerate")
            ))
        conn.commit()
    finally:
        conn.close()


def insert_card_playstyles(card_id, playstyles_list):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for ps in playstyles_list:
                cur.execute("""
                    INSERT INTO card_playstyles (card_id, playstyle, plus)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE plus=VALUES(plus);
                """, (
                    card_id,
                    ps.get("playstyle"),
                    ps.get("plus")
                ))
        conn.commit()
    finally:
        conn.close()


def insert_card_roles(card_id, roles):
    """
    Insert or update card roles.
    roles: list of dicts with keys: position, role, plus
    """
    if not roles:
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for r in roles:
                # MySQL ON DUPLICATE KEY requires a UNIQUE constraint
                # Assuming (card_id, position, role) is UNIQUE
                cur.execute("""
                    INSERT INTO card_roles (card_id, position, role, plus)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE plus = VALUES(plus)
                """, (
                    card_id,
                    r.get("position"),
                    r.get("role"),
                    r.get("plus")
                ))
        conn.commit()
    finally:
        conn.close()


def insert_card_stats(card_id, stats_list):
    """
    Insert or update card stats for each category.
    stats_list: dict with keys like 'pace', 'shooting', etc., each containing a dict of substats
    """
    if not stats_list:
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            stats_table_mapping = {
                "card_pace_stats": "pace",
                "card_shooting_stats": "shooting",
                "card_passing_stats": "passing",
                "card_dribbling_stats": "dribbling",
                "card_defending_stats": "defending",
                "card_physical_stats": "physical"
            }

            for table, category in stats_table_mapping.items():
                substats = stats_list.get(category, {})
                if not substats:
                    continue

                columns = list(substats.keys())
                values = list(substats.values())

                # Build MySQL INSERT ... ON DUPLICATE KEY UPDATE dynamically
                all_columns = ["card_id"] + columns
                placeholders = ", ".join(["%s"] * len(all_columns))
                update_clause = ", ".join([f"{col}=VALUES({col})" for col in columns])

                sql = f"""
                    INSERT INTO {table} ({', '.join(all_columns)})
                    VALUES ({placeholders})
                    ON DUPLICATE KEY UPDATE {update_clause}
                """

                cur.execute(sql, [card_id] + values)

        conn.commit()
    finally:
        conn.close()


def get_or_create_user(discord_id, display_name):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (display_name, discord_id, notify_channel, notify_target)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE user_id = LAST_INSERT_ID(user_id)
                """,
                (display_name, str(discord_id), "discord_dm", str(discord_id))
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()

def insert_position(user_id, card_id, buy_price, buy_time, quantity=1, target_price_low=None,
                     target_price_high=None, expected_hold_hours=None, event_state_at_entry=None):
    """Open a new position (a buy of `quantity` copies at `buy_price` each). Returns the new position_id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO positions (
                    user_id, card_id, quantity, buy_price, buy_time,
                    target_price_low, target_price_high, expected_hold_hours, event_state_at_entry
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id,
                card_id,
                quantity,
                buy_price,
                buy_time,
                target_price_low,
                target_price_high,
                expected_hold_hours,
                json.dumps(event_state_at_entry) if event_state_at_entry is not None else None
            ))
            position_id = cur.lastrowid
        conn.commit()
        return position_id
    finally:
        conn.close()


# ------------------- DATA FETCHING -------------------

def fetch_cards():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT card_id, name, version, rating FROM cards
            """)
            rows = cur.fetchall()
            return rows
    finally:
        conn.close()


def fetch_meta_hrefs(version, min_price=5000):
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

def fetch_drop_candidates(platform="pc"):
    """Fetch raw sales in last 8 hours for dip detection"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ms.card_id,
                    c.name,
                    c.version,
                    ms.sale_time,
                    ms.sold_price,
                    ms.platform
                FROM market_sales ms
                JOIN cards c ON ms.card_id = c.card_id
                WHERE ms.sold_price > 10000
                  AND ms.platform = %s
                  AND c.version NOT IN ('All Icons')
                  AND ms.sale_time >= NOW() - INTERVAL 8 HOUR
            """, (platform,))
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()


def fetch_icon_fluctuations(platform="pc"):
    """Fetch raw Icon/Hero sales in last 6 hours for fluctuation detection"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ms.card_id,
                    c.name,
                    c.version,
                    ms.sale_time,
                    ms.sold_price,
                    ms.platform
                FROM market_sales ms
                JOIN cards c ON ms.card_id = c.card_id
                WHERE ms.sold_price > 0
                  AND ms.platform = %s
                  AND c.version IN ('All Icons')
                  AND ms.sale_time >= NOW() - INTERVAL 6 HOUR
            """, (platform,))
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()

# fetch_upcoming_events(conn, lookahead_hours=EVENT_LOOKAHEAD_HOURS)


# ------------------- DATA DROPPING -------------------

def drop_all_tables():
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("DROP SCHEMA public CASCADE;")
    cur.execute("CREATE SCHEMA public;")
    
    conn.commit()
    cur.close()
    conn.close()
    print("All tables dropped.")
