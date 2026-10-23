# -*- coding: utf-8 -*-
"""11:31 VN execution layer: Frozen V2 uses completed EOD; execution uses KBS live quote."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import ai_decision_top5 as decision
import kbs_client
import send_top5_telegram_pro as pro

VN_TZ = timezone(timedelta(hours=7))
BASE_DIR = Path(__file__).resolve().parent
TOP5_FILE = BASE_DIR / "TOP5_V2_TODAY.csv"

def ensure_eod_top5():
    if TOP5_FILE.exists():
        return
    print("TOP5_V2_TODAY.csv missing on fresh runner; rebuilding from completed EOD scanner data.")
    import daily_scanner_v2
    daily_scanner_v2.main()
    if not TOP5_FILE.exists():
        raise RuntimeError("Khong tao duoc TOP5_V2_TODAY.csv tu EOD data.")

def build_midday_decision():
    ensure_eod_top5()
    df, signal_date = pro.load_data()
    if df.empty:
        raise RuntimeError("Khong co TOP5 EOD de cap nhat giua phien.")

    updated = df.copy()
    symbols = [str(x).strip().upper() for x in updated.head(5)["symbol"].tolist()]
    print(f"Frozen V2 signal date: {signal_date}")
    print("KBS realtime request:", ",".join(symbols))

    quotes = kbs_client.gia_hien_tai(symbols)
    if quotes is None or quotes.empty:
        raise RuntimeError(
            "KBS realtime price board khong tra du lieu. "
            "DUNG MIDDAY DECISION de tranh dung nham gia EOD."
        )

    quote_map = {}
    for _, q in quotes.iterrows():
        symbol = str(q.get("symbol", "")).strip().upper()
        price = pro.safe_float(q.get("price"))
        if symbol and not pd.isna(price) and price > 0:
            quote_map[symbol] = price

    missing = [s for s in symbols if s not in quote_map]
    if missing:
        raise RuntimeError(
            "Thieu gia realtime KBS cho: " + ", ".join(missing) +
            ". DUNG MIDDAY DECISION; khong fallback ve gia EOD."
        )

    for idx in updated.head(5).index:
        symbol = str(updated.at[idx, "symbol"]).strip().upper()
        old_close = pro.safe_float(
            updated.at[idx, "close"] if "close" in updated.columns
            else updated.at[idx, "close_top5"]
        )
        live_close = quote_map[symbol]
        updated.at[idx, "close"] = live_close
        if "close_top5" in updated.columns:
            updated.at[idx, "close_top5"] = live_close
        print(
            f"{symbol}: EOD {pro.fmt_price(old_close)} "
            f"-> KBS REALTIME {pro.fmt_price(live_close)}"
        )

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
    print("Frozen V2 = completed EOD | Execution price = KBS realtime")
    print("=" * 70)

    candidates, signal_date = build_midday_decision()
    message = decision.build_message(candidates, signal_date)
    now = datetime.now(VN_TZ)
    message = message.replace(
        "🧠 TOP5 AI — DECISION LAYER",
        "🧠 TOP5 AI — DECISION LAYER 11:31\n"
        "🔒 Frozen V2: EOD hoàn chỉnh | 💹 Giá thực thi: KBS realtime\n"
        f"🕒 Quote: {now.strftime('%H:%M %d/%m/%Y')}",
        1,
    )
    print(message)
    token, chat_id = pro.load_config()
    pro.send_telegram(token=token, chat_id=chat_id, message=message)
    print("GUI MIDDAY DECISION LEN TELEGRAM THANH CONG")

if __name__ == "__main__":
    main()
