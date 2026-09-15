from src.notifier.discord import send_message

# Send Message on Discord of all the Best Found Drop Deals
def notify_drop_deals(buy_df, plat):
    if not buy_df.empty:
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
            send_message(msg)
    else:
        send_message(f"No Dip Buy candidates found within current hour on {plat.upper()}.")


# Send Message on Discord of all the Best Found Icon Fluctuations
def notify_icon_fluctuations(fluctuation_df, plat):
    if not fluctuation_df.empty:
        for _, row in fluctuation_df.head(5).iterrows():
            msg = (
                f"💎 **Icon Fluctuation on {plat.upper()}: {row['name']}**\n"
                f"🟢 Buy ~ {int(row['best_buy']):,}\n"
                f"🔴 Sell ~ {int(row['best_sell']):,}\n"
                f"📊 Latest Sale ~ {int(row['latest_sale']):,}\n"
                f"📉 Spread ~ {row['spread_%']}%\n"
                f"💰 Margin: {row['profit_margin_%']}%"
            )
            send_message(msg)
    else:
        send_message(f"**No icon fluctuation candidates found this hour on {plat.upper()}.**")
