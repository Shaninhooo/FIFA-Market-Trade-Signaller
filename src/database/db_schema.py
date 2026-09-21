from src.database.db_utils import get_connection

# ------------------- DATA INIT -------------------

def initcardTable():
    conn = get_connection()
    cur = conn.cursor()

    # scraped_hrefs table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS hrefs (
            card_id INT PRIMARY KEY,
            href VARCHAR(255),
            version VARCHAR(20)
        )
    """)

    # cards table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            card_id INT PRIMARY KEY,
            name VARCHAR(50) NOT NULL,
            game INT,
            version VARCHAR(20),
            nationality VARCHAR(50),
            league VARCHAR(50),
            club VARCHAR(50),
            position VARCHAR(3),
            rating INT,
            weak_foot INT,
            skill_move INT,
            height INT,
            accelerate VARCHAR(20)
        )
    """)

    # card_playstyles
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_playstyles (
            card_id INT,
            playstyle VARCHAR(50) NOT NULL,
            plus TINYINT(1) NOT NULL DEFAULT 0,
            PRIMARY KEY(card_id, playstyle),
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_roles
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_roles (
            card_id INT,
            role VARCHAR(50) NOT NULL,
            position VARCHAR(50) NOT NULL,
            plus SMALLINT DEFAULT 1,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_pace_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_pace_stats (
            id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            pace_overall INT,
            acceleration INT,
            sprint_speed INT,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_shooting_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_shooting_stats (
            id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            shooting_overall INT,
            att_position INT,
            finishing INT,
            shot_power INT,
            long_shots INT,
            volleys INT,
            penalties INT,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_passing_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_passing_stats (
            id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            passing_overall INT,
            vision INT,
            crossing INT,
            fk_acc INT,
            short_pass INT,
            long_pass INT,
            curve INT,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_dribbling_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_dribbling_stats (
            id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            dribbling_overall INT,
            agility INT,
            balance INT,
            reactions INT,
            ball_control INT,
            dribbling INT,
            composure INT,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_defending_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_defending_stats (
            id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            defending_overall INT,
            interceptions INT,
            heading_acc INT,
            def_aware INT,
            stand_tackle INT,
            slide_tackle INT,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # card_physical_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS card_physical_stats (
            id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            physical_overall INT,
            jumping INT,
            stamina INT,
            strength INT,
            aggression INT,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # market_sales
    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_sales (
            sale_id INT AUTO_INCREMENT PRIMARY KEY,
            card_id INT,
            platform VARCHAR(20),
            sale_type VARCHAR(10),
            sale_time DATETIME NOT NULL,
            listed_price INT NOT NULL,
            sold_price INT,
            was_sold TINYINT(1) AS (sold_price IS NOT NULL AND sold_price <> 0) STORED,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        )
    """)

    # recurring_events
    cur.execute("""
        CREATE TABLE IF NOT EXISTS recurring_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_name TEXT NOT NULL,
            frequency TEXT NOT NULL,
            day_of_week INT,
            time_of_day TIME,
            UNIQUE KEY uniq_event_name (event_name(191))
        )
    """)

    # Known recurring events. day_of_week follows Python's datetime.weekday()
    # (Monday=0 ... Sunday=6); time_of_day is UK time, matching the rest of
    # the event pipeline (scrape_events). INSERT IGNORE keeps this idempotent
    # across every startup, since initcardTable() runs on every boot.
    cur.execute("""
        INSERT IGNORE INTO recurring_events (event_name, frequency, day_of_week, time_of_day)
        VALUES ('Division Rivals Rewards', 'weekly', 3, '08:00:00')
    """)

    # unique_events
    cur.execute("""
        CREATE TABLE IF NOT EXISTS unique_events (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_name TEXT NOT NULL,
            version VARCHAR(20),
            start_datetime DATETIME NOT NULL,
            end_datetime DATETIME NOT NULL,
            UNIQUE KEY uniq_event_name_start (event_name(191), start_datetime)
        )
    """)

    # ------------------- POSITION TRACKER DATABASE -------------------
    # Assumes cards / market_sales already exist (shared, global data)

    # users
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INT AUTO_INCREMENT PRIMARY KEY,
            display_name VARCHAR(50) NOT NULL,
            discord_id VARCHAR(30) UNIQUE,
            notify_channel VARCHAR(20) NOT NULL,
            notify_target VARCHAR(255) NOT NULL,
            platform ENUM('pc', 'ps') DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # user_settings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INT PRIMARY KEY,
            risk_tolerance VARCHAR(10) DEFAULT 'medium',
            max_position_size INT,
            share_stats TINYINT(1) DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    # positions - the full buy -> sell -> profit lifecycle for a single trade
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            position_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NOT NULL,
            card_id INT NOT NULL,

            quantity INT NOT NULL DEFAULT 1,
            buy_price INT NOT NULL,
            buy_time DATETIME NOT NULL,

            target_price_low INT,
            target_price_high INT,
            expected_hold_hours INT,
            event_state_at_entry JSON,

            status ENUM('open','sold','stopped_out') NOT NULL DEFAULT 'open',
            sell_price INT,
            sell_time DATETIME,
            exit_reason VARCHAR(50),

            -- buy_price/sell_price are per-unit (each market_sales row is one card);
            -- realized_profit is the total across all units in this position.
            realized_profit INT AS (
                CASE WHEN sell_price IS NOT NULL
                     THEN (ROUND(sell_price * 0.95) - buy_price) * quantity
                     ELSE NULL END
            ) STORED,

            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE,

            INDEX idx_user_status (user_id, status)
        )
    """)

    # user_profit_summary - per-user summary / leaderboard view.
    # Query this directly rather than re-writing the aggregation in Python.
    cur.execute("""
        CREATE OR REPLACE VIEW user_profit_summary AS
        SELECT
            u.user_id,
            u.display_name,
            us.share_stats,
            COUNT(CASE WHEN p.status IN ('sold','stopped_out') THEN 1 END)   AS closed_trades,
            COUNT(CASE WHEN p.status = 'open' THEN 1 END)                    AS open_positions,
            COALESCE(SUM(p.realized_profit), 0)                              AS total_realized_profit,
            ROUND(AVG(CASE WHEN p.status = 'sold' THEN p.realized_profit END), 2) AS avg_profit_per_win,
            SUM(CASE WHEN p.status = 'stopped_out' THEN 1 ELSE 0 END)        AS stop_loss_count
        FROM users u
        LEFT JOIN user_settings us ON us.user_id = u.user_id
        LEFT JOIN positions p ON p.user_id = u.user_id
        GROUP BY u.user_id, u.display_name, us.share_stats
    """)

    print("Tables Initialized (MySQL)...")
    conn.commit()
    cur.close()
    conn.close()
