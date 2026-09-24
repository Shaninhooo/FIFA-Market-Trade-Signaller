import pandas as pd
import numpy as np
import os
import pytz
from datetime import datetime
from dotenv import load_dotenv
from src.database.db_utils import fetch_drop_candidates, fetch_fluctuation_sales, fetch_hero_icon_sales, fetch_upcoming_events, fetch_daily_price_history, fetch_current_listings
import asyncio

# ------------------- STRATEGIES -------------------

SHORT_HOURS = 2
LONG_HOURS = 8
SHORT_TRADES = 10
LONG_TRADES = 100
MIN_SHORT_SALES = 15
MIN_LONG_SALES = 40

# Hero/Icon cards trade at a fraction of gold-card volume - often single
# digits of sales an hour - so "very short term" here means catching a dip
# within a couple of hours, not the multi-hour windows drop_strategy needs
# to build up a trustworthy sample.
HERO_ICON_RECENT_HOURS = 2
# The baseline pool is this window MINUS the recent window (they're
# mutually exclusive - see the baseline-contamination fix), so this is set
# to 24 + HERO_ICON_RECENT_HOURS rather than a flat 24, to keep the actual
# baseline pool the same size (~24h) it was before that split.
HERO_ICON_BASELINE_HOURS = 26
MIN_BASELINE_SALES = 6
MIN_RECENT_SALES = 2
HERO_ICON_MIN_PRICE = 15_000

# Shared by drop_strategy and early_game_strategy's event gates.
# How far ahead to look for informational purposes - matches how far in
# advance the event scraper typically has a promo's start_datetime populated.
# Kept wide so "days until next event" is always shown, even when it's not
# close enough to distrust the signal over.
EVENT_LOOKAHEAD_HOURS = 24 * 7
# How close a known event has to be before a signal is considered
# untrustworthy (vs. just informational). A threshold this wide would flag
# almost every candidate given events recur roughly weekly, turning the
# warning into noise - see event_pending derivation in each strategy.
EVENT_PENDING_HOURS = 48

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


def _merge_live_price(df, platform):
    """
    Left-joins current_listings' live cheapest-listed price onto a
    candidates df by card_id, dropping card_id afterward since it isn't
    otherwise part of any strategy's output.

    A strategy's own suggested_buy/best_buy is an estimate derived from
    recent completed sales; a live listing is what's actually buyable
    right now, so callers should prefer it where present. It can be
    missing per-card even when current_listings has data (nothing
    currently listed, or the scrape just hasn't caught up yet) - callers
    must handle that via the added buy_price_source column rather than
    assuming live_price is always populated.

    Adds two columns: live_price (NaN where nothing's currently listed)
    and buy_price_source ("live" or "sold_avg").
    """
    if df.empty:
        return df

    listings = fetch_current_listings(platform)
    if listings.empty:
        df["live_price"] = np.nan
    else:
        df = df.merge(
            listings[["card_id", "cheapest_price"]].rename(columns={"cheapest_price": "live_price"}),
            on="card_id", how="left"
        )

    df["buy_price_source"] = np.where(df["live_price"].notna(), "live", "sold_avg")
    df = df.drop(columns=["card_id"])
    return df


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
 
    # Excludes the short window (sale_time <= cutoff_short) so the baseline
    # reflects pre-drop pricing instead of being diluted by the very drop
    # it's being compared against.
    long_df = df[
        (df['sale_time'] > cutoff_long) &
        (df['sale_time'] <= cutoff_short) &
        (df['platform'] == platform) &
        (df['sold_price'] > 0)
    ]
 
    # Pull pending events ONCE per call, not once per card in the loop below.
    # Same two-tier approach as early_game_strategy: a wide, informational
    # lookahead for "days until next event" plus a tight window for actually
    # distrusting the signal - see EVENT_LOOKAHEAD_HOURS/EVENT_PENDING_HOURS
    # above for the full reasoning on why the gate window has to stay narrow.
    next_event = None
    if apply_event_gate:
        now_uk = datetime.now(pytz.timezone("Europe/London")).replace(tzinfo=None)
        upcoming_events = fetch_upcoming_events(lookahead_hours=EVENT_LOOKAHEAD_HOURS)
        active_or_future = [
            e for e in upcoming_events if e["source"] == "unique" and e["end"] >= now_uk
        ]
        if active_or_future:
            next_event = min(active_or_future, key=lambda e: e["start"])

    if next_event is not None:
        hours_until_event = (next_event["start"] - now_uk).total_seconds() / 3600
        days_until_event = round(hours_until_event / 24, 1)
        event_pending = hours_until_event <= EVENT_PENDING_HOURS
        next_event_name = next_event["event_name"]
    else:
        days_until_event = None
        event_pending = False
        next_event_name = None

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
        sales_volume = len(long_prices)  # matches the sample the stats above are actually computed from
 
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
        card_event_pending = event_pending and is_elite
        if card_event_pending:
            rating = "⚠️ Pending event - verify before buying"
 
        buy_price = round(last_short_avg * 0.97)
        raw_sell_price = round(last_long_avg * 0.98)
        sell_price_after_tax = int(raw_sell_price * 0.95)
 
        potential_profit = sell_price_after_tax - buy_price
        profit_margin_pct = (potential_profit / buy_price) * 100
 
        if profit_margin_pct < 3:
            continue
 
        buy_candidates.append({
            "card_id": card_id,
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
            "event_pending": card_event_pending,
            "days_until_event": days_until_event,
            "next_event_name": next_event_name,
        })
 
    buy_df = pd.DataFrame(buy_candidates)
    buy_df = _merge_live_price(buy_df, platform)

    if not buy_df.empty:
        has_live = buy_df["live_price"].notna()
        buy_df.loc[has_live, "suggested_buy"] = buy_df.loc[has_live, "live_price"].round().astype(int)
        buy_df["potential_profit"] = buy_df["suggested_sell_after_tax"] - buy_df["suggested_buy"]
        buy_df["profit_margin_%"] = (buy_df["potential_profit"] / buy_df["suggested_buy"] * 100).round(2)
        # Re-apply this strategy's own margin floor - a live price can be
        # higher than the sold-avg estimate and push a candidate below the
        # bar it originally cleared using that estimate.
        buy_df = buy_df[buy_df["profit_margin_%"] >= 3]
        buy_df = buy_df.drop(columns=["live_price"])

    if not buy_df.empty:
        rating_order = {"🔥 High": 3, "⚡ Medium": 2, "⚠️ Pending event - verify before buying": 1}
        buy_df["rating_priority"] = buy_df["investment_rating"].map(rating_order).fillna(0)
        buy_df = buy_df.sort_values(["rating_priority", "z_score"], ascending=[False, False])
        buy_df = buy_df.drop(columns=["rating_priority"])

    return buy_df


def _hero_icon_dip_strategy(platform, card_type, label):
    """
    Very short-term dip-buy strategy shared by Hero and Icon cards.

    These cards have far fewer copies in circulation than gold cards, so one
    impatient seller undercutting the going rate can drag the "last sold"
    price down hard on almost no volume - and it tends to snap back fast
    once buyers notice, since there's nothing else to replace it with.
    drop_strategy's z-score approach needs a real sample to trust a standard
    deviation; Hero/Icon cards often don't trade enough for that, so this
    instead compares a short recent window straight to the median of a
    slightly longer baseline, using get_thresholds' price-tiered % gates
    rather than a flat cutoff.

    card_type: cards.club to scope to ("HERO" or "EA FC ICONS") - keeping
    Hero and Icon results separate lets each get routed to its own Discord
    channel instead of one mixed feed.
    """

    df = fetch_hero_icon_sales(platform=platform, hours=HERO_ICON_BASELINE_HOURS, card_type=card_type)
    if df.empty:
        print(f"No {label} sales on {platform}")
        return pd.DataFrame()

    df = df.sort_values("sale_time")
    cutoff_recent = df["sale_time"].max() - pd.Timedelta(hours=HERO_ICON_RECENT_HOURS)

    candidates = []

    for card_id, group in df.groupby("card_id"):
        group = group.sort_values("sale_time", ascending=False)
        recent = group[group["sale_time"] > cutoff_recent]
        # Excludes the recent window so the baseline reflects pre-dip pricing
        # instead of being pulled toward the very dip it's compared against.
        baseline = group[group["sale_time"] <= cutoff_recent]

        if len(baseline) < MIN_BASELINE_SALES or len(recent) < MIN_RECENT_SALES:
            continue

        baseline_median = baseline["sold_price"].median()
        if baseline_median < HERO_ICON_MIN_PRICE:
            continue

        # Smoothed over the last couple of recent sales rather than just the
        # single latest one - low supply means one troll listing can print a
        # single absurd sale that isn't representative of where the market
        # actually is right now.
        latest_price = recent.head(3)["sold_price"].median()

        if latest_price >= baseline_median:
            continue  # no dip, nothing to do

        drop_pct = (baseline_median - latest_price) / baseline_median * 100
        medium_pct, high_pct = get_thresholds(baseline_median)

        if drop_pct >= high_pct:
            rating = "🔥 High"
        elif drop_pct >= medium_pct:
            rating = "⚡ Medium"
        else:
            continue  # not an unusual enough move for this card, skip

        buy_price = round(latest_price * 1.02)
        raw_sell_price = round(baseline_median * 0.97)
        sell_price_after_tax = int(raw_sell_price * 0.95)

        potential_profit = sell_price_after_tax - buy_price
        profit_margin_pct = (potential_profit / buy_price) * 100

        if profit_margin_pct < 5:
            continue

        candidates.append({
            "card_id": card_id,
            "name": group.iloc[0]["name"],
            "version": group.iloc[0]["version"],
            "rating": group.iloc[0]["rating"],
            "latest_price": round(latest_price, 2),
            "baseline_median": round(baseline_median, 2),
            "drop_%": round(drop_pct, 2),
            "recent_sales": len(recent),
            "baseline_sales": len(baseline),
            "suggested_buy": buy_price,
            "suggested_sell_raw": raw_sell_price,
            "suggested_sell_after_tax": sell_price_after_tax,
            "potential_profit": potential_profit,
            "profit_margin_%": round(profit_margin_pct, 2),
            "investment_rating": rating,
        })

    candidates_df = pd.DataFrame(candidates)
    candidates_df = _merge_live_price(candidates_df, platform)

    if not candidates_df.empty:
        has_live = candidates_df["live_price"].notna()
        candidates_df.loc[has_live, "suggested_buy"] = candidates_df.loc[has_live, "live_price"].round().astype(int)
        candidates_df["potential_profit"] = candidates_df["suggested_sell_after_tax"] - candidates_df["suggested_buy"]
        candidates_df["profit_margin_%"] = (candidates_df["potential_profit"] / candidates_df["suggested_buy"] * 100).round(2)
        # Re-apply this strategy's own margin floor - see drop_strategy's
        # equivalent note.
        candidates_df = candidates_df[candidates_df["profit_margin_%"] >= 5]
        candidates_df = candidates_df.drop(columns=["live_price"])

    if not candidates_df.empty:
        rating_order = {"🔥 High": 2, "⚡ Medium": 1}
        candidates_df["rating_priority"] = candidates_df["investment_rating"].map(rating_order)
        candidates_df = candidates_df.sort_values(["rating_priority", "drop_%"], ascending=[False, False])
        candidates_df = candidates_df.drop(columns=["rating_priority"])

    return candidates_df


def hero_strategy(platform):
    """Very short-term dip-buy strategy for Hero cards. See _hero_icon_dip_strategy."""
    return _hero_icon_dip_strategy(platform, card_type="HERO", label="Hero")


def icon_dip_strategy(platform):
    """Very short-term dip-buy strategy for Icon cards. See _hero_icon_dip_strategy.

    Distinct from icon_fluctuation_strategy below, which flags spread within
    a 6-hour window rather than a dip against a rolling baseline.
    """
    return _hero_icon_dip_strategy(platform, card_type="EA FC ICONS", label="Icon")


def _fluctuation_strategy(platform, card_type, label):
    """
    Spread-arbitrage strategy shared by Hero and Icon cards: flags a card
    whose completed sales disagree with each other more than usual within a
    short window, rather than comparing against a rolling baseline like the
    dip strategies do. Betting on buyers/sellers momentarily disagreeing on
    price for the same card, not on the price itself trending anywhere.

    card_type: cards.club to scope to ("HERO" or "EA FC ICONS") - keeping
    Hero and Icon results separate, same reasoning as _hero_icon_dip_strategy.
    """
    recent_df = fetch_fluctuation_sales(platform=platform, card_type=card_type)
    if recent_df.empty:
        print(f"No {label} fluctuations on {platform}")
        return pd.DataFrame()

    fluctuation_candidates = []

    for card_id, group in recent_df.groupby("card_id"):
        if len(group) < 5:
            continue

        # Latest price: median of last 3–5 sales
        latest_price = group.sort_values('sale_time', ascending=False)['sold_price'].head(5).median()
        latest_row = group.sort_values('sale_time', ascending=False).iloc[0]
        latest_name = latest_row['name']
        latest_version = latest_row['version']


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
                    "card_id": card_id,
                    "name": latest_name,
                    "version": latest_version,
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
    fluctuation_df = _merge_live_price(fluctuation_df, platform)

    if not fluctuation_df.empty:
        has_live = fluctuation_df["live_price"].notna()
        fluctuation_df.loc[has_live, "best_buy"] = fluctuation_df.loc[has_live, "live_price"].round().astype(int)
        # best_sell is pre-tax (see best_sell computation above) - tax is
        # applied inline here rather than stored as a separate column, same
        # as the original profit_margin calculation this mirrors.
        fluctuation_df["profit_margin_%"] = (
            (fluctuation_df["best_sell"] * 0.95 - fluctuation_df["best_buy"]) / fluctuation_df["best_buy"] * 100
        ).round(2)
        # Re-apply this strategy's own margin floor - see drop_strategy's
        # equivalent note.
        fluctuation_df = fluctuation_df[fluctuation_df["profit_margin_%"] > 8]
        fluctuation_df = fluctuation_df.drop(columns=["live_price"])

    if not fluctuation_df.empty:
        # Sort by how close latest price is to buy price
        fluctuation_df['buy_diff'] = abs(fluctuation_df['latest_sale'] - fluctuation_df['best_buy'])
        fluctuation_df = fluctuation_df.sort_values('buy_diff')

        display_cols = [
            "name", "version", "latest_sale", "best_buy", "best_sell",
            "avg_price", "min_price", "max_price", "spread_%",
            "sales_volume", "profit_margin_%", "buy_price_source"
        ]
        fluctuation_df = fluctuation_df[display_cols]

    return fluctuation_df


def hero_fluctuation_strategy(platform):
    """Spread-arbitrage strategy for Hero cards. See _fluctuation_strategy."""
    return _fluctuation_strategy(platform, card_type="HERO", label="Hero")


def icon_fluctuation_strategy(platform):
    """Spread-arbitrage strategy for Icon cards. See _fluctuation_strategy."""
    return _fluctuation_strategy(platform, card_type="EA FC ICONS", label="Icon")


def scheduled_event_strategy(lookahead_hours=24, trailing_hours=6):
    """
    Calendar-triggered strategy - unlike everything else in this module, it
    never looks at market_sales. It flags known FUT calendar events (team
    releases, TOTW, Division Rivals rewards) that predictably flood
    gold-card supply and depress prices market-wide for their duration, so
    a genuine mean-reversion dip can be told apart from a temporary,
    explainable supply shock BEFORE the price even moves - the lead time
    drop_strategy's purely reactive z-score can't offer on its own.

    This is intentionally market-wide, not per-card: today's event data
    doesn't track which specific cards an event impacts, only that gold
    supply broadly shifts around it. Treat this as an advisory gate on
    drop_strategy / position sizing, not a standalone buy signal.
    """
    now_uk = datetime.now(pytz.timezone("Europe/London")).replace(tzinfo=None)
    events = fetch_upcoming_events(lookahead_hours=lookahead_hours, trailing_hours=trailing_hours)

    advisories = []

    for event in events:
        start, end = event["start"], event["end"]

        if start <= now_uk <= end:
            status = "active"
            guidance = (
                f"Gold-card supply likely elevated until {end:%a %H:%M} UK - "
                "treat dips as supply-driven, not pure mean-reversion, until then."
            )
        elif start > now_uk:
            status = "starting_soon"
            guidance = (
                f"Starts in {start - now_uk}. Expect gold prices to soften over its run - "
                f"consider waiting to buy until near {end:%a %H:%M} UK."
            )
        else:
            status = "recently_ended"
            guidance = f"Ended {now_uk - end} ago - prices may still be recovering from elevated supply."

        advisories.append({
            "event_name": event["event_name"],
            "status": status,
            "starts": start,
            "ends": end,
            "guidance": guidance,
        })

    return advisories


# Don't evaluate a card until it's had this many qualifying trading days -
# the first few days after release are almost guaranteed to keep falling as
# scarcity resolves, so there's no dip worth acting on yet at any price.
EARLY_GAME_MIN_DAYS_LIVE = 5
EARLY_GAME_MIN_DAILY_SALES = 5

# Today's % decline must shrink to at most this fraction of yesterday's to
# count as "bottoming out" rather than "still falling about as fast as before".
EARLY_GAME_DECELERATION_RATIO = 0.7


def early_game_strategy(platform, min_days_live=EARLY_GAME_MIN_DAYS_LIVE, apply_event_gate=True):
    """
    Early-game buy strategy for meta gold cards - for the launch-window
    period when drop_strategy's mean-reversion assumption doesn't hold.
    Gold-card supply increases every day post-launch as more packs get
    opened, so prices trend down for real rather than noisily wobbling
    around a stable mean; a "dip below the recent average" isn't a signal
    worth trusting yet; a genuine change in the trend itself is.

    Instead of flagging a price below some baseline, this looks for
    deceleration: the day-over-day decline shrinking, which is the closest
    thing to a "bottoming out" signal available without assuming the
    market has already stabilized. Deliberately simple (a two-day
    comparison) rather than fitting a curve - early game means there isn't
    much history to fit one against yet anyway.

    That deceleration premise assumes supply grows smoothly day over day.
    A scheduled event (TOTW/promo, tracked in unique_events by the event
    scraper) breaks that assumption outright - it's a discontinuous supply
    shock, not a continuation of the decay curve "today's drop is smaller
    than yesterday's" is trying to read. Same flag-not-skip approach as
    drop_strategy's event gate: still surface the candidate (the
    deceleration read may well be right), but mark it so the caller knows
    not to trust it blindly going into a known supply shock.

    min_days_live: cards with less history than this are skipped entirely.
    """
    df = fetch_daily_price_history(platform=platform, min_daily_sales=EARLY_GAME_MIN_DAILY_SALES)
    if df.empty:
        print(f"No daily price history on {platform}")
        return pd.DataFrame()

    # Pulled once per call, not once per card - see drop_strategy's identical note.
    # Wide lookahead so a promo a few days out still shows up informationally;
    # event_pending below narrows that down to the "distrust the signal" case.
    next_event = None
    if apply_event_gate:
        now_uk = datetime.now(pytz.timezone("Europe/London")).replace(tzinfo=None)
        upcoming_events = fetch_upcoming_events(lookahead_hours=EVENT_LOOKAHEAD_HOURS)
        # Only scraped one-off events (TOTW/promos) - recurring ones (e.g.
        # Division Rivals Rewards, weekly) fire every week regardless and
        # would otherwise dominate "next event" with something that's
        # already priced into normal weekly patterns rather than the actual
        # supply shocks this is meant to catch.
        # Only events not yet over - an event that started is still relevant
        # (active_or_future includes "active now") until it actually ends.
        active_or_future = [
            e for e in upcoming_events if e["source"] == "unique" and e["end"] >= now_uk
        ]
        if active_or_future:
            next_event = min(active_or_future, key=lambda e: e["start"])

    if next_event is not None:
        hours_until_event = (next_event["start"] - now_uk).total_seconds() / 3600
        days_until_event = round(hours_until_event / 24, 1)
        event_pending = hours_until_event <= EVENT_PENDING_HOURS
        next_event_name = next_event["event_name"]
    else:
        days_until_event = None
        event_pending = False
        next_event_name = None

    candidates = []

    for card_id, group in df.groupby("card_id"):
        group = group.sort_values("sale_date")

        if len(group) < min_days_live:
            continue  # not enough trading days yet - still too early to trust any signal

        # MySQL's AVG() always returns DECIMAL regardless of the underlying
        # column type, and pymysql maps that to decimal.Decimal - cast to
        # float here so the arithmetic below (e.g. * 1.01) doesn't blow up
        # mixing Decimal with plain Python floats.
        prices = group["avg_price"].astype(float).to_numpy()
        pct_changes = (prices[1:] - prices[:-1]) / prices[:-1] * 100  # day-over-day % change

        if len(pct_changes) < 2:
            continue  # need at least two changes to compare today's decline against yesterday's

        latest_change, prior_change = pct_changes[-1], pct_changes[-2]

        if latest_change >= 0 or prior_change >= 0:
            continue  # not in a decline right now - not what this strategy targets

        if abs(latest_change) >= abs(prior_change) * EARLY_GAME_DECELERATION_RATIO:
            continue  # still falling about as fast as before - not bottoming yet

        latest_price = prices[-1]

        candidates.append({
            "card_id": card_id,
            "name": group.iloc[-1]["name"],
            "version": group.iloc[-1]["version"],
            "days_live": len(group),
            "latest_price": round(latest_price, 2),
            "latest_daily_change_%": round(latest_change, 2),
            "prior_daily_change_%": round(prior_change, 2),
            # A small premium over the last average, not a discount - the
            # signal here is "the fall is easing", not "it's momentarily cheap",
            # so waiting for that confirmation costs a bit of edge on purpose.
            "suggested_buy": round(latest_price * 1.01),
            "event_pending": event_pending,
            "days_until_event": days_until_event,
            "next_event_name": next_event_name,
        })

    candidates_df = pd.DataFrame(candidates)
    candidates_df = _merge_live_price(candidates_df, platform)

    if not candidates_df.empty:
        # No sell target/margin here (see docstring - this is a buy-only
        # signal), so unlike the other strategies there's no profit floor
        # to re-check after swapping in the live price.
        has_live = candidates_df["live_price"].notna()
        candidates_df.loc[has_live, "suggested_buy"] = candidates_df.loc[has_live, "live_price"].round().astype(int)
        candidates_df = candidates_df.drop(columns=["live_price"])
        # Clean (no pending event) candidates first, biggest deceleration
        # within each group second - same "surface it but deprioritize it"
        # treatment drop_strategy gives its pending-event flag.
        candidates_df = candidates_df.sort_values(["event_pending", "latest_daily_change_%"])

    return candidates_df
