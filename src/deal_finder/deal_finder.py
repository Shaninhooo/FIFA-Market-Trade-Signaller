import pandas as pd
import numpy as np
import os
from dotenv import load_dotenv
from src.database.db_utils import fetch_drop_candidates, fetch_icon_fluctuations
import asyncio

# ------------------- STRATEGIES -------------------

SHORT_HOURS = 2
LONG_HOURS = 8
SHORT_TRADES = 10
LONG_TRADES = 100
MIN_SHORT_SALES = 15
MIN_LONG_SALES = 40

def get_z_thresholds(price):
    """
    Returns (medium_z, high_z): how many standard deviations below the
    card's own long-term average counts as a dip worth flagging.
 
    This replaces the old fixed percentage thresholds. A flat % drop
    treats a volatile card and a stable card the same way; a z-score
    normalizes the drop against each card's own typical noise level,
    so you're comparing "how unusual is this move for THIS card" rather
    than "did it move more than an arbitrary %".
 
    Elite cards keep a slightly higher bar than before (not lower, like
    the old thresholds implied) since they're the most likely to be
    repricing for a real reason (event/meta shift) rather than noise.
    """
    if price >= 200_000:       # elite cards
        return 1.8, 2.8
    elif price >= 50_000:      # mid-tier cards
        return 1.5, 2.3
    else:                      # cheap fodder
        return 1.2, 1.8


def get_thresholds(price):
    if price >= 200_000:       # elite cards
        return 3, 5            # MEDIUM=3%, HIGH=5%
    elif price >= 50_000:      # mid-tier cards
        return 5, 8
    else:                      # cheap fodder
        return 10, 14

def drop_strategy(platform, apply_event_gate=True):

    df = fetch_drop_candidates(platform=platform)
    if df.empty:
        print(f"No Gold Rare drops on {platform}")
        return pd.DataFrame()
 
    df = df.sort_values("sale_time")
 
    cutoff_short = df['sale_time'].max() - pd.Timedelta(hours=SHORT_HOURS)
    cutoff_long = df['sale_time'].max() - pd.Timedelta(hours=LONG_HOURS)
 
    short_df = df[
        (df['sale_time'] > cutoff_short) &
        (df['platform'] == platform) &
        (df['sold_price'] > 0)
    ]
 
    long_df = df[
        (df['sale_time'] > cutoff_long) &
        (df['platform'] == platform) &
        (df['sold_price'] > 0)
    ]
 
    # Pull pending events ONCE per call, not once per card in the loop below.
    # See fetch_upcoming_events() note at the bottom of this file - it needs
    # to be added to db_utils.py, it doesn't exist there yet.
    # upcoming_events = (
    #     fetch_upcoming_events(lookahead_hours=EVENT_LOOKAHEAD_HOURS)
    #     if apply_event_gate else pd.DataFrame()
    # )
    # event_pending = not upcoming_events.empty
 
    buy_candidates = []
 
    for card_id, group in short_df.groupby('card_id'):
        group = group.sort_values('sale_time', ascending=False)
 
        if len(group) < MIN_SHORT_SALES:
            continue
 
        last_short_avg = group.head(SHORT_TRADES)['sold_price'].mean()
 
        long_group = long_df[long_df['card_id'] == card_id].sort_values('sale_time', ascending=False)
        if len(long_group) < MIN_LONG_SALES:
            continue
 
        long_prices = long_group.head(LONG_TRADES)['sold_price']
        last_long_avg = long_prices.mean()
        long_std = long_prices.std()
        sales_volume = len(long_group)
 
        if last_short_avg < 5000 or last_long_avg == 0:
            continue
 
        # Guard: a card with near-zero volatility produces a huge, unstable
        # z-score off tiny denominators. Skip rather than trust a fluke.
        if pd.isna(long_std) or long_std < last_long_avg * 0.01:
            continue
 
        # How many std devs below the long-term mean is the short-term avg
        z_score = (last_long_avg - last_short_avg) / long_std
        drop_pct = (last_long_avg - last_short_avg) / last_long_avg * 100  # kept for visibility/logging
 
        low_z, high_z = get_z_thresholds(last_long_avg)
 
        if z_score >= high_z:
            rating = "🔥 High"
        elif z_score >= low_z:
            rating = "⚡ Medium"
        else:
            continue  # not an unusual enough move for this card, skip
 
        # Event gate: only affects elite/meta cards, since that's where a
        # pending event is most likely to explain the dip rather than noise.
        # This flags rather than hard-skips - your call whether to act on
        # it, but it stops the tool from presenting it with full confidence.
        is_elite = last_long_avg >= 200_000
        # card_event_pending = apply_event_gate and event_pending and is_elite
        # if card_event_pending:
        #     rating = "⚠️ Pending event - verify before buying"
 
        buy_price = round(last_short_avg * 0.97)
        raw_sell_price = round(last_long_avg * 0.98)
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
            "z_score": round(z_score, 2),
            "sales_volume": sales_volume,
            "suggested_buy": buy_price,
            "suggested_sell_raw": raw_sell_price,
            "suggested_sell_after_tax": sell_price_after_tax,
            "potential_profit": potential_profit,
            "profit_margin_%": round(profit_margin_pct, 2),
            "investment_rating": rating,
            # "event_pending": card_event_pending,
        })
 
    buy_df = pd.DataFrame(buy_candidates)
 
    if not buy_df.empty:
        rating_order = {"🔥 High": 3, "⚡ Medium": 2, "⚠️ Pending event - verify before buying": 1}
        buy_df["rating_priority"] = buy_df["investment_rating"].map(rating_order).fillna(0)
        buy_df = buy_df.sort_values(["rating_priority", "z_score"], ascending=[False, False])
        buy_df = buy_df.drop(columns=["rating_priority"])
 
    return buy_df




def icon_fluctuation_strategy(platform):

    recent_df = fetch_icon_fluctuations(platform=platform)
    if recent_df.empty:
        print(f"No Icon fluctuations on {platform}")
        return pd.DataFrame()

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

    return fluctuation_df
