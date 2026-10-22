# -*- coding: utf-8 -*-
"""
11:31 VN midday execution layer.

- Keeps the official Frozen V2 TOP5 generated from completed EOD data.
- Does NOT rebuild scanner_dataset_v2.csv.
- Does NOT run daily_scanner_v2.py.
- Fetches only the current-day KBS price for the existing TOP5 symbols.
- Re-evaluates execution/Decision using current price while retaining EOD
  technical features and the morning Frozen V2 AI ranking.
"""

from datetime import datetime, timezone, timedelta

import pandas as pd

import ai_decision_top5 as decision
import kbs_client
import send_top5_telegram_pro as pro


VN_TZ = timezone(timedelta(hours=7))


def current_kbs_price(symbol):
    today = datetime.now(VN_TZ).date().isoformat()

    df = kbs_client.lich_su_gia(
        symbol=symbol,
        start=today,
        end=today,
    )

    if df is None or df.empty:
        return None

    row = df.sort_values("time").iloc[-1]
    close = pro.safe_float(row.get("close"))

    if pd.isna(close) or close <= 0:
        return None

    return close


def build_midday_decision():
    df, signal_date = pro.load_data()

    if df.empty:
        raise RuntimeError("Khong co TOP5 buoi sang de cap nhat 11:31.")

    # TOP5_V2_TODAY + scanner rows remain EOD.
    # Only current price is replaced for execution calculations.
    updated = df.copy()

    print(f"Official Frozen V2 signal date: {signal_date}")
    print("Refreshing current price for existing TOP5 only...")

    for idx in updated.head(5).index:
        symbol = str(updated.at[idx, "symbol"]).strip()
        old_close = pro.safe_float(
            updated.at[idx, "close"]
            if "close" in updated.columns
            else updated.at[idx, "close_top5"]
        )

        try:
            live_close = current_kbs_price(symbol)
        except Exception as exc:
            print(f"{symbol}: KBS error -> keep EOD close ({exc})")
            live_close = None

        if live_close is None:
            print(f"{symbol}: no current price -> keep EOD {pro.fmt_price(old_close)}")
            continue

        updated.at[idx, "close"] = live_close
        if "close_top5" in updated.columns:
            updated.at[idx, "close_top5"] = live_close

        print(
            f"{symbol}: EOD {pro.fmt_price(old_close)} "
            f"-> midday {pro.fmt_price(live_close)}"
        )

    # Reuse the existing post-model analysis/Decision Layer.
    # Frozen V2 score/rank is never recalculated here.
    records, setup_rank_map = pro.prepare_analyses(updated)

    candidates = []
    for idx, (row, analysis) in enumerate(records):
        candidates.append(
            decision.evaluate_candidate(
                ai_rank=idx + 1,
                setup_rank=setup_rank_map[idx],
                row=row,
                a=analysis,
            )
        )

    candidates.sort(
        key=lambda x: (
            not x["hard_no_buy"],
            x["decision_score"],
            x["setup_score"],
            x["rr1"],
            x["technical"],
        ),
        reverse=True,
    )

    for rank, candidate in enumerate(candidates, 1):
        candidate["decision_rank"] = rank

    return candidates, signal_date


def main():
    print("=" * 70)
    print("TOP5 AI - 11:31 MIDDAY DECISION")
    print("Frozen V2 TOP5 is kept from completed EOD data.")
    print("=" * 70)

    candidates, signal_date = build_midday_decision()
    message = decision.build_message(candidates, signal_date)

    # Make the session explicit in Telegram.
    message = message.replace(
        "🧠 TOP5 AI — DECISION LAYER",
        "🧠 TOP5 AI — DECISION LAYER 11:31\n"
        "🔒 Frozen V2 TOP5 giữ nguyên từ dữ liệu EOD",
        1,
    )

    print()
    print(message)
    print()

    token, chat_id = pro.load_config()
    pro.send_telegram(
        token=token,
        chat_id=chat_id,
        message=message,
    )

    print("=" * 70)
    print("GUI MIDDAY DECISION LEN TELEGRAM THANH CONG")
    print("=" * 70)


if __name__ == "__main__":
    main()
