import pymysql
import pandas as pd
import json
from dotenv import load_dotenv
import os
import asyncio
from datetime import datetime, timedelta
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


def get_user_id(discord_id):
    """Look up an existing user's id by their Discord id. Returns None if they don't exist yet."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE discord_id = %s", (str(discord_id),))
            row = cur.fetchone()
            return row["user_id"] if row else None
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


def set_user_platform(discord_id, platform):
    """Set the platform ('pc' or 'ps') a user trades on. Returns True if a user was updated."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET platform = %s WHERE discord_id = %s",
                (platform, str(discord_id))
            )
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def get_user_platform(discord_id):
    """Look up a user's registered platform ('pc'/'ps'). Returns None if they
    haven't set one via /create_tracker yet."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT platform FROM users WHERE discord_id = %s", (str(discord_id),))
            row = cur.fetchone()
            return row["platform"] if row else None
    finally:
        conn.close()


def fetch_trackable_users():
    """All users who have signed up with a platform, for the position tracker sweep."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, discord_id, platform
                FROM users
                WHERE platform IS NOT NULL
            """)
            return cur.fetchall()
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

def close_position(user_id, position_id, sell_price, sell_time, exit_reason='manual', status='sold'):
    """Close an open position (a sell). Returns True if it was closed, False if there was
    no matching open position (wrong user, wrong id, or already closed)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE positions
                SET sell_price = %s,
                    sell_time = %s,
                    exit_reason = %s,
                    status = %s
                WHERE position_id = %s
                  AND user_id = %s
                  AND status = 'open'
            """, (sell_price, sell_time, exit_reason, status, position_id, user_id))
            updated = cur.rowcount > 0
        conn.commit()
        return updated
    finally:
        conn.close()


def set_share_stats(user_id, share):
    """Opt a user in/out of leaderboard visibility. No user_settings row is
    created until this is called, so this upserts rather than updates."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_settings (user_id, share_stats)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE share_stats = VALUES(share_stats)
                """,
                (user_id, int(bool(share)))
            )
        conn.commit()
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


def fetch_total_profit(user_id):
    """Aggregate profit/trade stats for a user, via the user_profit_summary view."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    total_realized_profit,
                    closed_trades,
                    open_positions,
                    avg_profit_per_win,
                    stop_loss_count
                FROM user_profit_summary
                WHERE user_id = %s
            """, (user_id,))
            return cur.fetchone()
    finally:
        conn.close()


def fetch_leaderboard(limit=10):
    """Top users by total realized profit, among those who've opted in via share_stats."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT display_name, total_realized_profit, closed_trades, avg_profit_per_win
                FROM user_profit_summary
                WHERE share_stats = 1
                ORDER BY total_realized_profit DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()
    finally:
        conn.close()


def fetch_top_trades(days=7, limit=10):
    """Best individual closed trades (by realized_profit) in the last `days` days,
    among users who've opted in via share_stats. Ranks single trades, not per-user
    totals - a different shape than fetch_leaderboard."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    u.display_name,
                    c.name,
                    c.version,
                    p.quantity,
                    p.buy_price,
                    p.sell_price,
                    p.realized_profit,
                    p.sell_time
                FROM positions p
                JOIN users u ON u.user_id = p.user_id
                JOIN cards c ON c.card_id = p.card_id
                LEFT JOIN user_settings us ON us.user_id = p.user_id
                WHERE p.status IN ('sold', 'stopped_out')
                  AND p.sell_time >= NOW() - INTERVAL %s DAY
                  AND us.share_stats = 1
                ORDER BY p.realized_profit DESC
                LIMIT %s
            """, (days, limit))
            return cur.fetchall()
    finally:
        conn.close()


def fetch_open_positions(user_id, limit=10):
    """Most recent open positions for a user, newest first."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    p.position_id,
                    p.card_id,
                    c.name,
                    c.version,
                    p.quantity,
                    p.buy_price,
                    p.buy_time,
                    p.target_price_low,
                    p.target_price_high
                FROM positions p
                JOIN cards c ON c.card_id = p.card_id
                WHERE p.user_id = %s AND p.status = 'open'
                ORDER BY p.buy_time DESC
                LIMIT %s
            """, (user_id, limit))
            return cur.fetchall()
    finally:
        conn.close()

def fetch_closed_positions(user_id, limit=10):
    """Most recent closed positions for a user, newest first."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    p.position_id,
                    p.card_id,
                    c.name,
                    c.version,
                    p.quantity,
                    p.buy_price,
                    p.sell_price,
                    p.sell_time,
                    p.realized_profit
                FROM positions p
                JOIN cards c ON c.card_id = p.card_id
                WHERE p.user_id = %s AND p.status IN ('sold', 'stopped_out')
                ORDER BY p.sell_time DESC
                LIMIT %s
            """, (user_id, limit))
            return cur.fetchall()
    finally:
        conn.close()


def fetch_meta_hrefs(version, min_price=3000):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.href
                FROM hrefs c
                LEFT JOIN market_sales ms 
                    ON c.card_id = ms.card_id 
                    AND ms.sale_time >= NOW() - INTERVAL 12 HOUR
                WHERE c.version = %s
                GROUP BY c.href
                HAVING AVG(ms.sold_price) > %s OR AVG(ms.sold_price) IS NULL;
            """, (version, min_price))
            rows = cur.fetchall()
        return [row['href'] for row in rows]
    finally:
        conn.close()

def fetch_all_hrefs(version):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT href
                FROM hrefs
                WHERE version = %s
            """, (version,))
            rows = cur.fetchall()
            return [row['href'] for row in rows]
    finally:
        conn.close()

def fetch_meta_hrefs_by_club(club, min_price=3000):
    # club lives on cards, not hrefs, so this needs the join fetch_meta_hrefs skips
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT h.href
                FROM hrefs h
                JOIN cards c ON c.card_id = h.card_id
                LEFT JOIN market_sales ms
                    ON h.card_id = ms.card_id
                    AND ms.sale_time >= NOW() - INTERVAL 12 HOUR
                WHERE c.club = %s
                GROUP BY h.href
                HAVING AVG(ms.sold_price) > %s OR AVG(ms.sold_price) IS NULL;
            """, (club, min_price))
            rows = cur.fetchall()
        return [row['href'] for row in rows]
    finally:
        conn.close()

def fetch_all_hrefs_by_club(club):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT h.href
                FROM hrefs h
                JOIN cards c ON c.card_id = h.card_id
                WHERE c.club = %s
            """, (club,))
            rows = cur.fetchall()
            return [row['href'] for row in rows]
    finally:
        conn.close()

def fetch_ver_href(version):
    """All hrefs for cards whose own scraped version text contains `version`
    as a substring (e.g. "Icon" matches "All Icons", "Hero" matches "Base
    Hero") - matches the free-text cards.version field directly with LIKE,
    instead of an exact match or joining through cards.club.

    Note this still joins through cards, same as fetch_all_hrefs_by_club -
    so a href only shows up here once that card's metadata has actually
    been scraped and cards.version populated. It doesn't help discover a
    brand-new card matching this version before its own page has been
    scraped once.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT h.href
                FROM hrefs h
                JOIN cards c ON c.card_id = h.card_id
                WHERE c.version LIKE %s
            """, (f"%{version}%",))
            rows = cur.fetchall()
            return [row['href'] for row in rows]
    finally:
        conn.close()

def insert_unique_event(event_name, start_datetime, end_datetime, version=None):
    """Insert a one-off event (e.g. a team release). Relies on the
    (event_name, start_datetime) unique key on unique_events - INSERT IGNORE
    silently skips it if that same event/date has already been recorded."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT IGNORE INTO unique_events (event_name, version, start_datetime, end_datetime)
                VALUES (%s, %s, %s, %s)
            """, (event_name, version, start_datetime, end_datetime))
            inserted = cur.rowcount > 0
        conn.commit()
        return inserted
    finally:
        conn.close()

def _next_recurring_occurrence(day_of_week, time_of_day, now_uk):
    """Nearest occurrence (past or future) of a weekly recurring event to
    now_uk (a naive UK-wall-clock datetime), among last/this/next week's
    instance - lets the caller tell "just happened" from "about to happen"."""
    if day_of_week is None or time_of_day is None:
        return None

    seconds = int(time_of_day.total_seconds())  # pymysql returns TIME as timedelta
    hour, minute = seconds // 3600, (seconds % 3600) // 60

    days_ahead = (day_of_week - now_uk.weekday()) % 7
    this_week = now_uk.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)

    candidates = [this_week - timedelta(days=7), this_week, this_week + timedelta(days=7)]
    return min(candidates, key=lambda c: abs((c - now_uk).total_seconds()))

def fetch_upcoming_events(lookahead_hours=24, trailing_hours=6):
    """Return known FUT calendar events (team releases, TOTW, recurring
    events like Division Rivals Rewards) that are active now, start within
    lookahead_hours, or occurred within the last trailing_hours.

    All datetimes are naive UK wall-clock time, matching how scrape_events
    and the recurring_events seed data store them - kept naive throughout
    rather than mixed with timezone-aware values, to avoid tz-comparison bugs.
    Returns a list of {event_name, start, end, source} dicts; classification
    (active/starting soon/recently ended) and any guidance is left to the
    caller in the strategy layer, not decided here.
    """
    now_uk = datetime.now(pytz.timezone("Europe/London")).replace(tzinfo=None)
    window_start = now_uk - timedelta(hours=trailing_hours)
    window_end = now_uk + timedelta(hours=lookahead_hours)

    events = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT event_name, version, start_datetime, end_datetime
                FROM unique_events
                WHERE end_datetime >= %s AND start_datetime <= %s
            """, (window_start, window_end))
            for row in cur.fetchall():
                events.append({
                    "event_name": row["event_name"],
                    "start": row["start_datetime"],
                    "end": row["end_datetime"],
                    "source": "unique",
                })

            cur.execute("SELECT event_name, day_of_week, time_of_day FROM recurring_events")
            recurring_rows = cur.fetchall()
    finally:
        conn.close()

    for row in recurring_rows:
        occurrence = _next_recurring_occurrence(row["day_of_week"], row["time_of_day"], now_uk)
        if occurrence is not None and window_start <= occurrence <= window_end:
            events.append({
                "event_name": row["event_name"],
                "start": occurrence,
                "end": occurrence,  # instantaneous - recurring_events tracks no duration
                "source": "recurring",
            })

    return events

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
                  AND c.club NOT IN ('HERO', 'EA FC ICONS')
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
                  AND c.club IN ('HERO', 'EA FC ICONS')
                  AND ms.sale_time >= NOW() - INTERVAL 6 HOUR
            """, (platform,))
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()

def fetch_hero_icon_sales(platform="pc", hours=24, card_type=None):
    """Fetch raw Hero/Icon sales in the last `hours` hours, for the very
    short-term dip strategy in deal_finder.py's hero_strategy/icon_dip_strategy.
    Filtered via cards.club rather than cards.version - Icon/Hero cards
    aren't tied to a real-world club, so Futbin uses the club field itself to
    carry that special-version label ("HERO" / "EA FC ICONS"), same as
    fetch_all_hrefs_by_club/scrape_players already rely on for these two
    versions. cards.version is free-text scraped straight off each player's
    own page (e.g. "All Icons", "Base Hero") and isn't a reliable exact-match
    filter.

    card_type: cards.club to scope to - "HERO" or "EA FC ICONS". None
    (default) includes both.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT
                    ms.card_id,
                    c.name,
                    c.version,
                    c.rating,
                    ms.sale_time,
                    ms.sold_price,
                    ms.platform
                FROM market_sales ms
                JOIN cards c ON ms.card_id = c.card_id
                WHERE ms.sold_price > 0
                  AND ms.platform = %s
                  AND ms.sale_time >= NOW() - INTERVAL %s HOUR
                  AND c.club = %s
            """
            params = [platform, hours, card_type]

            cur.execute(query, params)
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()


def fetch_card_trades(card_id, platform):
    """Fetch card trade history"""
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
                WHERE ms.card_id = %s
                  AND ms.sold_price > 0
                  AND ms.platform = %s
                  AND ms.sale_time >= NOW() - INTERVAL 12 HOUR
            """, (card_id, platform))
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()

def fetch_price_series(card_id, platform, start_time, end_time):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                        SELECT sale_time, sold_price FROM market_sales
                        WHERE card_id=%s AND platform=%s AND sold_price > 0
                            AND sale_time > %s AND sale_time <= %s
                        ORDER BY sale_time ASC
                    """, (card_id, platform, start_time, end_time))
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()


def fetch_market_index_sample(platform, short_hours, long_hours, min_price, sample_min_sales,
                               card_type=None, as_of=None):
    """Per-card short/long average sale price and sample size, for the market-wide
    index in market_index.py. One row per card that clears the liquidity bar.

    card_type: optional cards.version to restrict the basket to (e.g. "Gold Rare").
    None (default) includes every version.

    as_of: point in time to anchor both windows to. Defaults to now (live use).
    Passing a historical datetime computes the index as it would have looked at
    that moment - critical for backtesting. Sales after `as_of` are excluded on
    purpose (sale_time <= anchor below), otherwise a historical snapshot would
    leak future data into what's supposed to be a point-in-time read.

    market_sales.sale_time is stored as naive Adelaide wall-clock time (see
    insert_sale_db), not UTC - so "now" has to be computed in that same
    naive-Adelaide frame, or every window silently anchors ~10 hours off
    from what the stored data considers "now".
    """
    anchor = as_of or datetime.now(pytz.timezone("Australia/Adelaide")).replace(tzinfo=None)
    short_cutoff = anchor - timedelta(hours=short_hours)
    long_cutoff = anchor - timedelta(hours=long_hours)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # One row per card, short/long averages computed in SQL rather than
            # pulling every raw sale into Python - stays cheap as card count grows.
            query = """
                SELECT ms.card_id,
                       AVG(CASE WHEN ms.sale_time > %s THEN ms.sold_price END) AS short_avg,
                       AVG(ms.sold_price) AS long_avg,
                       COUNT(CASE WHEN ms.sale_time > %s THEN 1 END) AS short_n,
                       COUNT(*) AS long_n
                FROM market_sales ms
                JOIN cards c ON c.card_id = ms.card_id
                WHERE ms.platform = %s AND ms.sold_price > %s
                  AND ms.sale_time > %s AND ms.sale_time <= %s
            """
            params = [short_cutoff, short_cutoff, platform, min_price, long_cutoff, anchor]

            if card_type is not None:
                query += " AND c.version = %s"
                params.append(card_type)

            query += " GROUP BY ms.card_id HAVING short_n >= %s AND long_n >= %s"
            params.extend([sample_min_sales, sample_min_sales * 2])

            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()

def fetch_daily_price_history(platform="pc", min_price=10000, min_daily_sales=5):
    """Per-card daily average sale price and volume, one row per (card_id, date)
    that clears the daily liquidity bar. For the early-game trend/deceleration
    strategy in deal_finder.py, which operates on day-over-day price movement
    rather than the multi-hour windows the reactive strategies use - early
    game's dominant force is a real declining trend (supply grows every day
    as packs get opened), not noise around a stable mean, so "day" rather
    than "hour" is the timescale that actually matters there.

    Hero/Icon cards are excluded (same club-based filter as
    fetch_drop_candidates) since they don't follow the same daily
    pack-supply dynamics as golds.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    ms.card_id,
                    c.name,
                    c.version,
                    DATE(ms.sale_time) AS sale_date,
                    AVG(ms.sold_price) AS avg_price,
                    COUNT(*) AS n
                FROM market_sales ms
                JOIN cards c ON c.card_id = ms.card_id
                WHERE ms.platform = %s
                  AND ms.sold_price > %s
                  AND c.club NOT IN ('HERO', 'EA FC ICONS')
                GROUP BY ms.card_id, c.name, c.version, DATE(ms.sale_time)
                HAVING n >= %s
                ORDER BY ms.card_id, sale_date
            """, (platform, min_price, min_daily_sales))
            return pd.DataFrame(cur.fetchall())
    finally:
        conn.close()


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
