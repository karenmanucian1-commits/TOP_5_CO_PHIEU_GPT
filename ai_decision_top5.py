# -*- coding: utf-8 -*-

"""
TOP5 AI - DECISION LAYER

Mục đích:
- Đọc chính kết quả phân tích từ send_top5_telegram_pro.py
- KHÔNG thay đổi Frozen V2
- KHÔNG retrain model
- KHÔNG thay đổi AI Rank
- Xếp hạng cơ hội mua thực tế dựa trên Setup + R:R + Volume +
  RSI + vị trí giá + Market Regime
- Gửi 1 tin kết luận cuối cùng sang Telegram

Đây là lớp hỗ trợ quyết định / paper scanner.
Không phải hệ thống đặt lệnh tự động.
"""

from datetime import datetime, timezone, timedelta
import math

import pandas as pd

import send_top5_telegram_pro as pro


# ============================================================
# CONFIG DECISION LAYER
# ============================================================

MIN_SETUP_SCORE = 55.0
MIN_RR1 = 1.30

# Volume < 0.30x: không loại hoàn toàn, nhưng bắt buộc chờ xác nhận.
VERY_WEAK_VOLUME = 0.30

# Volume >= 0.60x: mức tối thiểu tương đối chấp nhận được để ưu tiên hơn.
ACCEPTABLE_VOLUME = 0.60

# RSI từ 75 trở lên coi là quá nóng.
OVERHEATED_RSI = 75.0

# Nếu giá cao hơn Buy Zone trên 0.2% thì coi là chưa ở điểm mua.
BUY_ZONE_TOLERANCE = 1.002


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=float("nan")):
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return default
        return value
    except (TypeError, ValueError):
        return default


def fmt_price(value):
    value = safe_float(value)

    if math.isnan(value):
        return "N/A"

    if value >= 100:
        return f"{value:.1f}"

    if value >= 10:
        return f"{value:.2f}"

    return f"{value:.3f}"


def fmt_ai(value):
    value = safe_float(value)

    if math.isnan(value):
        return "N/A"

    return f"{value:.4f}"


def get_row_value(row, name, fallback_name=None, default=float("nan")):
    value = row.get(name, default)

    if pd.isna(value) and fallback_name:
        value = row.get(fallback_name, default)

    return safe_float(value, default)


# ============================================================
# DECISION LOGIC
# ============================================================

def evaluate_candidate(ai_rank, setup_rank, row, a):
    """
    Đánh giá khả năng mua thực tế.

    LƯU Ý:
    - Không thay đổi AI Score.
    - Không thay đổi Setup Score gốc.
    - Decision Score chỉ dùng để xếp thứ tự cơ hội giao dịch.
    """

    symbol = str(row.get("symbol", "")).strip()

    close = get_row_value(row, "close", "close_top5")
    ai_score = get_row_value(row, "SCORE", "SCORE_top5")

    rsi = get_row_value(row, "RSI14", default=50.0)
    volume = get_row_value(row, "VOL_RATIO", default=0.0)
    rel20 = get_row_value(row, "REL_RET20", "REL_RET20_top5", 0.0)
    dist20 = get_row_value(row, "DIST_MA20", default=0.0)

    setup_score = safe_float(a.get("setup_score"), 0.0)
    technical = safe_float(a.get("total"), 0.0)
    # Planned R:R uses the reference entry inside Buy Zone.
    rr1 = safe_float(a.get("rr1"), 0.0)
    rr2 = safe_float(a.get("rr2"), 0.0)

    # Current R:R uses the actual current close as entry.
    current_rr1 = safe_float(a.get("current_rr1"), rr1)
    current_rr2 = safe_float(a.get("current_rr2"), rr2)

    buy_low = safe_float(a.get("buy_low"))
    buy_high = safe_float(a.get("buy_high"))
    stop = safe_float(a.get("stop_loss"))
    target1 = safe_float(a.get("target1"))
    target2 = safe_float(a.get("target2"))

    support = safe_float(a.get("support"))
    resistance = safe_float(a.get("resistance1"))
    atr = safe_float(a.get("atr14"))

    market_score = safe_float(a.get("market_score"), 50.0)
    market_label = str(a.get("market_label", "N/A"))
    setup = str(a.get("setup", "N/A"))

    # --------------------------------------------------------
    # Decision Score
    # Bắt đầu từ Setup Score đã được PRO tính.
    # --------------------------------------------------------

    decision_score = setup_score
    reasons = []
    warnings = []
    blockers = []

    # 1. SETUP TYPE
    if "PULLBACK" in setup:
        decision_score += 8
        reasons.append("cấu trúc Pullback thuận lợi")

    elif "BREAKOUT XÁC NHẬN" in setup:
        decision_score += 7
        reasons.append("Breakout đã có xác nhận")

    elif "TREND FOLLOWING" in setup:
        decision_score += 3
        reasons.append("xu hướng tăng vẫn duy trì")

    elif "OVEREXTENDED" in setup:
        decision_score -= 25
        blockers.append("giá đang quá nóng/Overextended")

    elif "BREAKOUT CHƯA" in setup:
        decision_score -= 12
        warnings.append("Breakout chưa có volume xác nhận")

    elif "NEUTRAL" in setup:
        decision_score -= 5

    # 2. R:R TỚI TARGET 1
    if rr1 >= 2.0:
        decision_score += 10
        reasons.append("R:R Target 1 rất tốt")

    elif rr1 >= 1.5:
        decision_score += 7
        reasons.append("R:R Target 1 tốt")

    elif rr1 >= MIN_RR1:
        decision_score += 3
        reasons.append("R:R Target 1 đạt mức chấp nhận")

    elif rr1 >= 1.0:
        decision_score -= 8
        warnings.append("R:R Target 1 thấp")

    else:
        decision_score -= 25
        blockers.append("R:R tới kháng cự không đạt")

    # 3. VOLUME
    if volume >= 1.20:
        decision_score += 10
        reasons.append("dòng tiền xác nhận mạnh")

    elif volume >= 0.80:
        decision_score += 6
        reasons.append("dòng tiền tương đối tốt")

    elif volume >= ACCEPTABLE_VOLUME:
        decision_score += 2
        warnings.append("volume vẫn dưới trung bình")

    elif volume >= VERY_WEAK_VOLUME:
        decision_score -= 5
        warnings.append("dòng tiền chưa xác nhận")

    else:
        decision_score -= 15
        warnings.append("dòng tiền rất yếu")

    # 4. RSI
    if rsi >= OVERHEATED_RSI:
        decision_score -= 20
        blockers.append("RSI quá nóng")

    elif rsi >= 70:
        decision_score -= 6
        warnings.append("RSI khá cao")

    elif 50 <= rsi < 70:
        decision_score += 3

    # 5. RELATIVE STRENGTH
    if rel20 >= 0.05:
        decision_score += 6
        reasons.append("Relative Strength 20D mạnh")

    elif rel20 >= 0.02:
        decision_score += 3

    elif rel20 < 0:
        decision_score -= 5
        warnings.append("Relative Strength 20D yếu")

    # 6. KHOẢNG CÁCH MA20
    if dist20 >= 0.10:
        decision_score -= 10
        warnings.append("giá cách quá xa MA20")

    elif 0 <= dist20 <= 0.035:
        decision_score += 3

    # 7. MARKET REGIME
    if market_score < 30:
        decision_score -= 18
        warnings.append("thị trường chung rất tiêu cực")

    elif market_score < 45:
        decision_score -= 8
        warnings.append("thị trường chung tiêu cực")

    elif market_score >= 65:
        decision_score += 5
        reasons.append("thị trường chung thuận lợi")

    # --------------------------------------------------------
    # VỊ TRÍ GIÁ SO VỚI BUY ZONE
    # --------------------------------------------------------

    in_buy_zone = False
    above_buy_zone = False
    below_buy_zone = False

    if (
        not math.isnan(close)
        and not math.isnan(buy_low)
        and not math.isnan(buy_high)
    ):
        if buy_low <= close <= buy_high * BUY_ZONE_TOLERANCE:
            in_buy_zone = True
            decision_score += 7
            reasons.append("giá đang ở gần vùng mua")

        elif close > buy_high * BUY_ZONE_TOLERANCE:
            above_buy_zone = True
            decision_score -= 4
            warnings.append("giá chưa về vùng mua")

        elif close < buy_low:
            below_buy_zone = True
            decision_score -= 4
            warnings.append("giá đã xuống dưới vùng mua dự kiến")

    # --------------------------------------------------------
    # HARD RULES / ACTION
    # --------------------------------------------------------

    hard_no_buy = False

    if "OVEREXTENDED" in setup or rsi >= OVERHEATED_RSI:
        hard_no_buy = True

    if rr1 < 1.0:
        hard_no_buy = True

    if setup_score < 45:
        hard_no_buy = True

    # --------------------------------------------------------
    # CURRENT-PRICE EXECUTION GATE
    # Decision Score must describe BUY-NOW quality, not only setup quality.
    # A beautiful setup can still be a poor entry when current R:R is weak.
    # --------------------------------------------------------
    if current_rr1 < 0.75:
        decision_score -= 35
        warnings.append("R:R tại giá hiện tại rất thấp")
    elif current_rr1 < 1.0:
        decision_score -= 25
        warnings.append("R:R tại giá hiện tại dưới 1.0")
    elif current_rr1 < MIN_RR1:
        decision_score -= 15
        warnings.append("R:R tại giá hiện tại chưa đạt")
    elif current_rr1 >= 2.0:
        decision_score += 5
        reasons.append("R:R tại giá hiện tại tốt")

    # Hard cap: a candidate that cannot meet the minimum current-price R:R
    # must never display an excellent Decision Score.
    if current_rr1 < 1.0:
        decision_score = min(decision_score, 49.0)
    elif current_rr1 < MIN_RR1:
        decision_score = min(decision_score, 64.0)

    # Preserve the raw score for audit before displaying a 0-100 score.
    raw_decision_score = decision_score
    decision_score = max(0.0, min(100.0, decision_score))

    # --------------------------------------------------------
    # ACTION LABEL
    # --------------------------------------------------------

    if hard_no_buy:
        action = "⛔ KHÔNG MUA"

    elif market_score < 30:
        action = "🔴 ĐỨNG NGOÀI - THỊ TRƯỜNG XẤU"

    elif volume < VERY_WEAK_VOLUME:
        action = "🟡 CHỜ DÒNG TIỀN XÁC NHẬN"

    elif above_buy_zone:
        action = "🟠 CHỜ GIÁ VỀ VÙNG MUA"

    elif below_buy_zone:
        action = "🟠 CHỜ ỔN ĐỊNH LẠI"

    elif in_buy_zone and current_rr1 < MIN_RR1:
        action = "🟠 CHỜ ĐIỂM VÀO TỐT HƠN - R:R HIỆN TẠI CHƯA ĐẠT"

    elif (
        in_buy_zone
        and rr1 >= MIN_RR1
        and current_rr1 >= MIN_RR1
        and setup_score >= MIN_SETUP_SCORE
    ):
        if market_score < 45:
            action = "🟠 THEO DÕI MUA THĂM DÒ"
        else:
            action = "🟢 ĐỦ ĐIỀU KIỆN THEO DÕI MUA"

    else:
        action = "🟠 THEO DÕI - CHƯA VỘI MUA"

    return {
        "symbol": symbol,
        "ai_rank": ai_rank,
        "setup_rank": setup_rank,
        "ai_score": ai_score,
        "setup_score": setup_score,
        "raw_decision_score": round(raw_decision_score, 1),
        "decision_score": round(decision_score, 1),
        "technical": technical,
        "setup": setup,
        "action": action,

        "close": close,
        "buy_low": buy_low,
        "buy_high": buy_high,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "support": support,
        "resistance": resistance,
        "atr": atr,

        "rr1": rr1,
        "rr2": rr2,
        "current_rr1": current_rr1,
        "current_rr2": current_rr2,
        "rsi": rsi,
        "volume": volume,
        "rel20": rel20,
        "dist20": dist20,

        "market_score": market_score,
        "market_label": market_label,

        "in_buy_zone": in_buy_zone,
        "above_buy_zone": above_buy_zone,
        "hard_no_buy": hard_no_buy,

        "reasons": reasons,
        "warnings": warnings,
        "blockers": blockers,
    }


# ============================================================
# BUILD DECISION
# ============================================================

def build_decision():
    df, signal_date = pro.load_data()

    if df.empty:
        raise RuntimeError("Khong co du lieu TOP5.")

    records, setup_rank_map = pro.prepare_analyses(df)

    if not records:
        raise RuntimeError("Khong co ket qua phan tich TOP5.")

    candidates = []

    for idx, (row, analysis) in enumerate(records):
        ai_rank = idx + 1
        setup_rank = setup_rank_map[idx]

        candidate = evaluate_candidate(
            ai_rank=ai_rank,
            setup_rank=setup_rank,
            row=row,
            a=analysis,
        )

        candidates.append(candidate)

    # Decision Rank:
    # 1. Không bị hard block
    # 2. Decision Score
    # 3. Setup Score
    # 4. R:R
    # 5. Technical Score
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

    for rank, c in enumerate(candidates, 1):
        c["decision_rank"] = rank

    return candidates, signal_date


# ============================================================
# FINAL RECOMMENDATION
# ============================================================

def choose_best(candidates):
    """
    Chọn tối đa 1 mã ưu tiên.
    Có quyền trả về None nếu không có mã đủ điều kiện.
    """

    for c in candidates:

        if c["hard_no_buy"]:
            continue

        if c["setup_score"] < MIN_SETUP_SCORE:
            continue

        if c["rr1"] < MIN_RR1:
            continue

        # If price is already in/near Buy Zone, only prioritize it when
        # the R:R at the actual current price is also acceptable.
        if c["in_buy_zone"] and c["current_rr1"] < MIN_RR1:
            continue

        if c["market_score"] < 30:
            continue

        # Cho phép volume yếu xuất hiện ở Watchlist,
        # nhưng mã volume cực yếu không được gọi là "mua ngay".
        return c

    return None


# ============================================================
# EXPLANATION
# ============================================================

def short_reason(c):
    parts = []

    if "PULLBACK" in c["setup"]:
        parts.append("Pullback")

    elif "BREAKOUT XÁC NHẬN" in c["setup"]:
        parts.append("Breakout xác nhận")

    elif "OVEREXTENDED" in c["setup"]:
        parts.append("quá nóng")

    elif "NEUTRAL" in c["setup"]:
        parts.append("Neutral")

    else:
        clean_setup = (
            c["setup"]
            .replace("🟢", "")
            .replace("🔥", "")
            .replace("🚀", "")
            .replace("⚠️", "")
            .replace("📈", "")
            .replace("⚪", "")
            .strip()
        )
        parts.append(clean_setup)

    if c["volume"] < 0.30:
        parts.append("dòng tiền rất yếu")

    elif c["volume"] < 0.60:
        parts.append("volume chưa xác nhận")

    elif c["volume"] < 0.80:
        parts.append("volume dưới TB")

    elif c["volume"] >= 1.20:
        parts.append("dòng tiền mạnh")

    if c["rsi"] >= 75:
        parts.append("RSI quá nóng")

    if c["rr1"] < 1.0:
        parts.append("R:R không đạt")

    elif c["rr1"] < 1.5:
        parts.append("R:R trung bình")

    elif c["rr1"] >= 2.0:
        parts.append("R:R tốt")

    return ", ".join(parts)


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def is_eligible_candidate(c):
    """
    Chỉ loại các mã thực sự KHÔNG MUA / ĐỨNG NGOÀI.
    Các mã setup đạt nhưng đang CHỜ GIÁ / CHỜ ĐIỂM VÀO vẫn được giữ.
    """
    if c["hard_no_buy"]:
        return False
    if c["market_score"] < 30:
        return False
    if c["setup_score"] < MIN_SETUP_SCORE:
        return False
    if c["rr1"] < MIN_RR1:
        return False
    return True


def build_message(candidates, signal_date):
    now = datetime.now(timezone(timedelta(hours=7)))
    first = candidates[0]
    eligible = [c for c in candidates if is_eligible_candidate(c)]

    lines = [
        "🧠 CỔ PHIẾU ĐỦ ĐIỀU KIỆN — FROZEN V2",
        "",
        f"📅 Dữ liệu: {signal_date.strftime('%d/%m/%Y')}",
        f"🕒 Đánh giá lúc: {now.strftime('%H:%M %d/%m/%Y')}",
        f"🌐 Thị trường: {first['market_label']} ({first['market_score']:.1f}/100)",
        "",
    ]

    if not eligible:
        lines += [
            "🚫 HÔM NAY CHƯA CÓ MÃ ĐỦ ĐIỀU KIỆN.",
            "",
            "Các mã bị kết luận KHÔNG MUA / không đạt chuẩn đã được loại khỏi bản tin.",
            "",
            "ℹ️ Frozen V2 và Decision Layer vẫn tính toán như cũ; chỉ thay đổi phần hiển thị Telegram.",
        ]
        return "\n".join(lines)

    lines.append(f"✅ Có {len(eligible)} mã đạt điều kiện:")
    lines.append("")

    for c in eligible:
        if c["above_buy_zone"]:
            status = "🟠 CHỜ GIÁ VỀ VÙNG MUA"
        elif c["below_buy_zone"]:
            status = "🟠 CHỜ ỔN ĐỊNH LẠI"
        elif c["in_buy_zone"] and c["current_rr1"] < MIN_RR1:
            status = "🟠 CHỜ ĐIỂM VÀO TỐT HƠN"
        elif c["volume"] < VERY_WEAK_VOLUME:
            status = "🟡 CHỜ DÒNG TIỀN XÁC NHẬN"
        elif c["in_buy_zone"]:
            status = "🟢 ĐỦ ĐIỀU KIỆN THEO DÕI MUA"
        else:
            status = "🟠 THEO DÕI ĐIỂM VÀO"

        lines += [
            f"📌 {c['symbol']} | {status}",
            f"💵 Giá: {fmt_price(c['close'])} | Buy: {fmt_price(c['buy_low'])} – {fmt_price(c['buy_high'])}",
            f"🛑 Stop: {fmt_price(c['stop'])}",
            f"🎯 T1: {fmt_price(c['target1'])} | 🚀 T2: {fmt_price(c['target2'])}",
            f"⚖️ R:R kế hoạch: 1:{c['rr1']:.2f} → 1:{c['rr2']:.2f}",
            f"⚖️ R:R nếu mua ngay: 1:{c['current_rr1']:.2f} → 1:{c['current_rr2']:.2f}",
            f"⭐ Technical {c['technical']:.1f} | Setup {c['setup_score']:.1f} | Decision {c['decision_score']:.1f}",
            f"📈 RSI {c['rsi']:.1f} | 💧 Volume {c['volume']:.2f}x | 💪 Relative20D {c['rel20']:+.2%}",
            "",
        ]

    lines += [
        "📌 Chỉ hiển thị mã còn đủ điều kiện; các mã KHÔNG MUA đã được bỏ.",
        "ℹ️ Frozen V2 không thay đổi và hệ thống không tự đặt lệnh.",
    ]
    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 70)
    print("TOP5 AI - AUTOMATIC DECISION LAYER")
    print("=" * 70)

    candidates, signal_date = build_decision()

    print()
    print("DECISION RANK:")
    print()

    for c in candidates:
        print(
            f"#{c['decision_rank']} {c['symbol']} | "
            f"Decision={c['decision_score']:.1f} | "
            f"Setup={c['setup_score']:.1f} | "
            f"AI Rank={c['ai_rank']} | "
            f"{c['action']}"
        )

    message = build_message(candidates, signal_date)

    print()
    print("-" * 70)
    print(message)
    print("-" * 70)

    # Dùng chính config Telegram của PRO.
    # Trên GitHub: lấy BOT_TOKEN + CHAT_ID từ Secrets.
    # Trên Windows: fallback telegram_config.json.
    token, chat_id = pro.load_config()

    pro.send_telegram(
        token=token,
        chat_id=chat_id,
        message=message,
    )

    print()
    print("=" * 70)
    print("GUI AI DECISION LEN TELEGRAM THANH CONG")
    print("=" * 70)


if __name__ == "__main__":
    main()