# -*- coding: utf-8 -*-
"""Midday execution layer.
Frozen V2 eligibility/price plan comes ONLY from the latest completed EOD.
KBS realtime is used ONLY to update current price, current R:R and action state.
"""
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
    raise RuntimeError(
        "Khong co TOP5_V2_TODAY.csv cua phien EOD hoan chinh. "
        "Midday KHONG duoc tu rebuild Frozen V2 bang du lieu nua phien."
    )


def build_eod_candidates():
    """Build and freeze eligibility from completed EOD only."""
    ensure_eod_top5()
    df, signal_date = pro.load_data()
    if df.empty:
        raise RuntimeError("Khong co TOP5 EOD de cap nhat giua phien.")

    records, setup_rank_map = pro.prepare_analyses(df)
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

    # IMPORTANT: eligibility is decided here, before any realtime price is applied.
    eligible = [c for c in candidates if decision.is_eligible_candidate(c)]
    return eligible, signal_date


def apply_realtime_execution(candidates):
    if not candidates:
        return candidates

    symbols = [str(c["symbol"]).strip().upper() for c in candidates]
    print("KBS realtime request:", ",".join(symbols))

    quotes = kbs_client.gia_hien_tai(symbols)
    if quotes is None or quotes.empty:
        raise RuntimeError(
            "KBS realtime price board khong tra du lieu. "
            "DUNG MIDDAY DECISION; khong fallback ve gia EOD."
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

    for c in candidates:
        symbol = str(c["symbol"]).strip().upper()
        eod_close = c["close"]
        live = quote_map[symbol]
        stop = c["stop"]
        t1 = c["target1"]
        t2 = c["target2"]

        risk_now = live - stop
        if risk_now > 0:
            c["current_rr1"] = (t1 - live) / risk_now
            c["current_rr2"] = (t2 - live) / risk_now
        else:
            c["current_rr1"] = 0.0
            c["current_rr2"] = 0.0

        c["close"] = live

        # Realtime changes execution state only; never EOD eligibility.
        tol = decision.BUY_ZONE_TOLERANCE
        c["in_buy_zone"] = c["buy_low"] <= live <= c["buy_high"] * tol
        c["above_buy_zone"] = live > c["buy_high"] * tol
        c["below_buy_zone"] = live < c["buy_low"]

        print(
            f"{symbol}: EOD {pro.fmt_price(eod_close)} -> "
            f"KBS REALTIME {pro.fmt_price(live)} | "
            f"RR now 1:{c['current_rr1']:.2f}"
        )

    return candidates


def build_midday_decision():
    candidates, signal_date = build_eod_candidates()
    print(f"Frozen V2 signal date: {signal_date}")
    candidates = apply_realtime_execution(candidates)
    return candidates, signal_date


def main():
    print("=" * 70)
    print("TOP5 AI - MIDDAY DECISION")
    print("Eligibility = completed EOD | Execution price = KBS realtime")
    print("=" * 70)

    candidates, signal_date = build_midday_decision()
    now = datetime.now(VN_TZ)
    if not candidates:
        message = (
            "🤖 CỔ PHIẾU ĐẠT CHUẨN GPT\n\n"
            f"📅 Nền EOD: {signal_date.strftime('%d/%m/%Y')}\n"
            f"🕒 Kiểm tra giữa phiên: {now.strftime('%H:%M %d/%m/%Y')}\n\n"
            "🚫 Danh sách EOD nền hiện không có mã đủ chuẩn để cập nhật điểm vào giữa phiên.\n"
            "ℹ️ Không dùng Market Score từ nến nửa phiên và không ép mã."
        )
    else:
        message = decision.build_message(candidates, signal_date)

    header = (
        "🤖 CỔ PHIẾU ĐẠT CHUẨN GPT\n"
        "🔒 Danh sách đạt chuẩn: EOD hoàn chỉnh\n"
        "💹 Giá/R:R thực thi: KBS realtime\n"
        f"🕒 Quote: {now.strftime('%H:%M %d/%m/%Y')}"
    )
    message = message.replace(
        "🤖 CỔ PHIẾU ĐẠT CHUẨN GPT",
        header,
        1,
    )

    print(message)
    token, chat_id = pro.load_config()
    pro.send_telegram(token=token, chat_id=chat_id, message=message)
    print("GUI MIDDAY DECISION LEN TELEGRAM THANH CONG")


if __name__ == "__main__":
    main()
