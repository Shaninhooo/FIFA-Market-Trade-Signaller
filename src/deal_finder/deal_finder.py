import pandas as pd
import numpy as np
from discordwebhook import Discord
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
import discord
from discord.ext import commands, tasks
import asyncio

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True  # Required to read messages
bot = commands.Bot(command_prefix="!", intents=intents)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = 1421417739213471795

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    
    channel = bot.get_channel(CHANNEL_ID)
    
    if channel:
        # Only delete messages that are not pinned
        deleted = await channel.purge(limit=1000, check=lambda m: not m.pinned)
        print(f"Deleted {len(deleted)} messages (pinned messages preserved)")
    
    await bot.close()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
discord = Discord(url=DISCORD_WEBHOOK)
DB_URL = os.getenv("DB_URL")  # e.g., "mysql+pymysql://user:pass@host/dbname"
engine = create_engine(
    DB_URL,
    pool_recycle=280,
    pool_pre_ping=True
    )


def send_discord_message(message: str):
    if not DISCORD_WEBHOOK:
        print("⚠️ No Discord webhook set.")
        return
    discord.post(content=message)



# ------------------- DATA FETCHING -------------------

def fetch_drop_candidates(conn, platform="pc"):
    """Fetch raw sales in last 8 hours for dip detection"""
    query = f"""
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
          AND ms.platform = '{platform}'
          AND c.version NOT IN ('All Icons')
          AND ms.sale_time >= NOW() - INTERVAL 8 HOUR
    """
    return pd.read_sql(query, conn)


def fetch_icon_fluctuations(conn, platform="pc"):
    """Fetch raw Icon/Hero sales in last 6 hours for fluctuation detection"""
    query = f"""
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
          AND ms.platform = '{platform}'
          AND c.version IN ('All Icons')
          AND ms.sale_time >= NOW() - INTERVAL 6 HOUR
    """
    return pd.read_sql(query, conn)


# ------------------- STRATEGIES -------------------

SHORT_HOURS = 2
LONG_HOURS = 8
SHORT_TRADES = 10
LONG_TRADES = 100
MIN_SHORT_SALES = 15
MIN_LONG_SALES = 40

def get_thresholds(price):
    if price >= 200_000:       # elite cards
        return 3, 5            # MEDIUM=3%, HIGH=5%
    elif price >= 50_000:      # mid-tier cards
        return 5, 8
    else:                      # cheap fodder
        return 10, 14

def drop_strategy(conn):
    platforms = ["pc", "ps"]

    for plat in platforms:
        df = fetch_drop_candidates(conn, platform=plat)
        if df.empty:
            print(f"No Gold Rare drops on {plat}")
            continue

        df = df.sort_values("sale_time")

        cutoff_short = df['sale_time'].max() - pd.Timedelta(hours=SHORT_HOURS)
        cutoff_long = df['sale_time'].max() - pd.Timedelta(hours=LONG_HOURS)

        short_df = df[
            (df['sale_time'] > cutoff_short) &
            (df['platform'] == plat) &
            (df['sold_price'] > 0) 
        ]

        long_df = df[
            (df['sale_time'] > cutoff_long) &
            (df['platform'] == plat) &
            (df['sold_price'] > 0) 
        ]

        buy_candidates = []

        for card_id, group in short_df.groupby('card_id'):
            group = group.sort_values('sale_time', ascending=False)

            if len(group) >= MIN_SHORT_SALES:
                # Short-term avg
                last_short_avg = group.head(SHORT_TRADES)['sold_price'].mean()

                # Long-term avg
                long_group = long_df[long_df['card_id'] == card_id].sort_values('sale_time', ascending=False)
                if len(long_group) < MIN_LONG_SALES:
                    continue

                last_long_avg = long_group.head(LONG_TRADES)['sold_price'].mean()
                sales_volume = len(long_group)

                # Calculate drop %
                drop_pct = (last_long_avg - last_short_avg) / last_long_avg * 100
                if last_short_avg < 5000:  # skip unusable cards
                    continue

                # Dynamic thresholds
                low, high = get_thresholds(last_long_avg)

                if drop_pct >= high:
                    rating = "🔥 High"
                elif drop_pct >= low:
                    rating = "⚡ Medium"
                else:
                    continue  # skip if not a big enough dip

                # Suggested prices
                buy_price = round(last_short_avg * 0.97)   # buy a bit below short avg
                raw_sell_price = round(last_long_avg * 0.98)  # sell a bit below long avg

                # Apply EA 5% tax
                sell_price_after_tax = int(raw_sell_price * 0.95)

                potential_profit = sell_price_after_tax - buy_price
                profit_margin_pct = (potential_profit / buy_price) * 100

                if profit_margin_pct < 3:
                    continue

                buy_candidates.append({
                    "name": group.iloc[0]["name"],
                    "version": group.iloc[0]["version"],
                    "last_short_avg": round(last_short_avg, 2),
                    "last_long_avg": round(last_long_avg, 2),
                    "drop_%": round(drop_pct, 2),
                    "sales_volume": sales_volume,
                    "suggested_buy": buy_price,
                    "suggested_sell_raw": raw_sell_price,   # before tax
                    "suggested_sell_after_tax": sell_price_after_tax,
                    "potential_profit": potential_profit,
                    "profit_margin_%": round(profit_margin_pct, 2),
                    "investment_rating": rating
                })
        
        buy_df = pd.DataFrame(buy_candidates)


        if not buy_df.empty:
            rating_order = {"🔥 High": 2, "⚡ Medium": 1}
            buy_df["rating_priority"] = buy_df["investment_rating"].map(rating_order)
            buy_df = buy_df.sort_values(["rating_priority", "drop_%"], ascending=[False, False])
            for _, row in buy_df.head(5).iterrows():
                msg = (
                    f"📊 **{plat.upper()} Deal Alert!**\n"
                    f"🎴 Card: {row['name']} ({row['version']})\n"
                    f"📉 Drop: {row['drop_%']}%\n"
                    f"🟢 Buy ~ {row['suggested_buy']:,}\n"
                    f"🔴 Sell ~ {row['suggested_sell_raw']:,}\n"
                    f"💰 Profit: {row['potential_profit']:,} ({row['profit_margin_%']}%)\n"
                    f"🏷️ Rating: {row['investment_rating']}"
                )
                send_discord_message(msg)
        else:
            send_discord_message(f"No Dip Buy candidates found within current hour on {plat.upper()}.")




def icon_fluctuation_strategy(conn):
    platforms = ["pc", "ps"]
    
    for plat in platforms:
        recent_df = fetch_icon_fluctuations(conn, platform=plat)
        if recent_df.empty:
            print(f"No Icon fluctuations on {plat}")
            continue

        fluctuation_candidates = []

        for card_id, group in recent_df.groupby("card_id"):
            if len(group) < 5:
                continue

            # Latest price: median of last 3–5 sales
            latest_price = group.sort_values('sale_time', ascending=False)['sold_price'].head(5).median()
            latest_name = group.sort_values('sale_time', ascending=False).iloc[0]['name']


            avg_price = group['sold_price'].mean()
            min_price = group['sold_price'].min()
            max_price = group['sold_price'].max()
            spread = (max_price - min_price) / avg_price * 100
            sales_volume = len(group)

            if spread >= 15 and sales_volume >= 3 and avg_price > 10000:
                buy_price = round(min_price * 1.02)
                sell_price = round(avg_price * 0.98)
                profit_margin = round((sell_price*0.95 - buy_price) / buy_price * 100, 2)

                # Only keep if latest price is at or below suggested buy price
                if profit_margin > 8 and latest_price < 500000:
                    fluctuation_candidates.append({
                        "name": latest_name,
                        "latest_sale": latest_price,
                        "avg_price": int(avg_price),
                        "min_price": int(min_price),
                        "max_price": int(max_price),
                        "spread_%": round(spread, 2),
                        "sales_volume": sales_volume,
                        "best_buy": buy_price,
                        "best_sell": sell_price,
                        "profit_margin_%": profit_margin,
                    })

        # Convert to DataFrame
        fluctuation_df = pd.DataFrame(fluctuation_candidates)

        if not fluctuation_df.empty:
            # Sort by how close latest price is to buy price
            fluctuation_df['buy_diff'] = abs(fluctuation_df['latest_sale'] - fluctuation_df['best_buy'])
            fluctuation_df = fluctuation_df.sort_values('buy_diff')

            display_cols = [
                "name", "latest_sale", "best_buy", "best_sell",
                "avg_price", "min_price", "max_price", "spread_%",
                "sales_volume", "profit_margin_%"
            ]
            fluctuation_df = fluctuation_df[display_cols]

            for _, row in fluctuation_df.head(5).iterrows():
                msg = (
                    f"💎 **Icon Fluctuation on {plat.upper()}: {row['name']}**\n"
                    f"🟢 Buy ~ {int(row['best_buy']):,}\n"
                    f"🔴 Sell ~ {int(row['best_sell']):,}\n"
                    f"📊 Latest Sale ~ {int(row['latest_sale']):,}\n"
                    f"📉 Spread ~ {row['spread_%']}%\n"
                    f"💰 Margin: {row['profit_margin_%']}%"
                )
                send_discord_message(msg)
        else:
            send_discord_message(f"**No icon fluctuation candidates found this hour on {plat.upper()}.**")

        


# ------------------- BOT RUNNER -------------------

if __name__ == "__main__":
    async def main():
        await bot.start(BOT_TOKEN)

    # Run Discord bot to clear messages first
    asyncio.run(main())

    # Then run market strategies
    with engine.connect() as conn:
        drop_strategy(conn)
        icon_fluctuation_strategy(conn)