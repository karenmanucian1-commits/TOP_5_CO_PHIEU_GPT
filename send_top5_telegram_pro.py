import json
import os
import ssl
import urllib.request
import urllib.parse
from datetime import datetime

import certifi
import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

TOP5_FILE = "TOP5_V2_TODAY.csv"
SCANNER_FILE = "scanner_dataset_v2.csv"
CONFIG_FILE = "telegram_config.json"


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=np.nan):
    try:
        value = float(value)
        if np.isfinite(value):
            return value
    except Exception:
        pass
    return default


def clamp(value, low, high):
    return max(low, min(high, value))


def fmt_price(price):
    if pd.isna(price):
        return "N/A"

    if price >= 100:
        return f"{price:.1f}"

    return f"{price:.2f}"


def fmt_pct(value):
    if pd.isna(value):
        return "N/A"

    return f"{value:+.2%}"


# ============================================================
# 1. TREND SCORE - MAX 25
# ============================================================

def score_trend(row):
    score = 0.0

    close = safe_float(row.get("close"))
    ma20 = safe_float(row.get("MA20"))
    ma50 = safe_float(row.get("MA50"))

    dist20 = safe_float(row.get("DIST_MA20"), 0)
    slope20 = safe_float(row.get("MA20_SLOPE5"), 0)
    slope50 = safe_float(row.get("MA50_SLOPE10"), 0)

    if close > ma20:
        score += 5

    if close > ma50:
        score += 5

    if ma20 > ma50:
        score += 5

    if slope20 > 0:
        score += 4

    if slope50 > 0:
        score += 3

    # Giá trên MA20 nhưng chưa quá xa
    if 0 <= dist20 <= 0.08:
        score += 3
    elif 0.08 < dist20 <= 0.12:
        score += 1

    return round(clamp(score, 0, 25), 1)


# ============================================================
# 2. MOMENTUM SCORE - MAX 20
# ============================================================

def score_momentum(row):
    score = 0.0

    ret5 = safe_float(row.get("RET5"), 0)
    ret10 = safe_float(row.get("RET10"), 0)
    ret20 = safe_float(row.get("RET20"), 0)
    rsi = safe_float(row.get("RSI14"), 50)

    if ret5 > 0:
        score += 4

    if ret5 > 0.03:
        score += 1

    if ret10 > 0:
        score += 4

    if ret20 > 0:
        score += 4

    # RSI đẹp cho momentum
    if 50 <= rsi <= 65:
        score += 6

    elif 45 <= rsi < 50:
        score += 4

    elif 65 < rsi <= 72:
        score += 4

    elif 40 <= rsi < 45:
        score += 2

    elif 72 < rsi <= 78:
        score += 2

    # RSI > 78 không cộng thêm vì quá nóng

    return round(clamp(score, 0, 20), 1)


# ============================================================
# 3. RELATIVE STRENGTH - MAX 25
# ============================================================

def score_relative_strength(row):
    score = 0.0

    rel5 = safe_float(row.get("REL_RET5"), 0)
    rel20 = safe_float(row.get("REL_RET20"), 0)
    rel60 = safe_float(row.get("REL_RET60"), 0)

    rank20 = safe_float(
        row.get("CS_RET20_RANK"),
        0.5
    )

    rank60 = safe_float(
        row.get("CS_RET60_RANK"),
        0.5
    )

    if rel5 > 0:
        score += 4

    if rel20 > 0:
        score += 5

    if rel60 > 0:
        score += 4

    score += clamp(rank20, 0, 1) * 7
    score += clamp(rank60, 0, 1) * 5

    return round(clamp(score, 0, 25), 1)


# ============================================================
# 4. LIQUIDITY / MONEY FLOW - MAX 15
# ============================================================

def score_liquidity(row):
    score = 0.0

    vol_ratio = safe_float(
        row.get("VOL_RATIO"),
        0
    )

    value20 = safe_float(
        row.get("VALUE20"),
        0
    )

    # --------------------------
    # Volume confirmation
    # --------------------------

    if vol_ratio >= 1.50:
        score += 8

    elif vol_ratio >= 1.20:
        score += 7

    elif vol_ratio >= 1.00:
        score += 6

    elif vol_ratio >= 0.80:
        score += 5

    elif vol_ratio >= 0.60:
        score += 3

    elif vol_ratio >= 0.40:
        score += 1

    # --------------------------
    # Average traded value
    # --------------------------

    if value20 >= 50_000_000:
        score += 7

    elif value20 >= 20_000_000:
        score += 6

    elif value20 >= 10_000_000:
        score += 5

    elif value20 >= 5_000_000:
        score += 4

    elif value20 >= 1_000_000:
        score += 2

    return round(clamp(score, 0, 15), 1)


# ============================================================
# 5. RISK QUALITY - MAX 15
# Điểm càng cao = setup càng dễ kiểm soát rủi ro
# ============================================================

def score_risk(row):
    score = 15.0

    volatility = safe_float(
        row.get("VOLATILITY20"),
        0
    )

    rsi = safe_float(
        row.get("RSI14"),
        50
    )

    dist20 = safe_float(
        row.get("DIST_MA20"),
        0
    )

    range20 = safe_float(
        row.get("RANGE_POS20"),
        0.5
    )

    # --------------------------
    # Volatility penalty
    # --------------------------

    if volatility > 0.05:
        score -= 6

    elif volatility > 0.04:
        score -= 4

    elif volatility > 0.03:
        score -= 2

    # --------------------------
    # Extended from MA20
    # --------------------------

    if dist20 > 0.15:
        score -= 6

    elif dist20 > 0.10:
        score -= 4

    elif dist20 > 0.07:
        score -= 2

    # --------------------------
    # RSI overheating
    # --------------------------

    if rsi > 80:
        score -= 6

    elif rsi > 75:
        score -= 4

    elif rsi > 70:
        score -= 2

    # --------------------------
    # Near range high + hot RSI
    # --------------------------

    if range20 > 0.95 and rsi > 70:
        score -= 2

    return round(clamp(score, 0, 15), 1)


# ============================================================
# MARKET SCORE
# ============================================================

def market_score(row):
    score = 50.0

    trend = safe_float(
        row.get("MKT_TREND_SCORE"),
        0
    )

    breadth20 = safe_float(
        row.get("BREADTH20"),
        0.5
    )

    ret20 = safe_float(
        row.get("MKT_RET20"),
        0
    )

    score += clamp(trend, -1, 1) * 20

    score += (
        clamp(breadth20, 0, 1) - 0.5
    ) * 30

    if ret20 > 0.05:
        score += 10

    elif ret20 > 0:
        score += 5

    elif ret20 < -0.05:
        score -= 10

    elif ret20 < 0:
        score -= 5

    return round(
        clamp(score, 0, 100),
        1
    )


def market_label(score):

    if score >= 70:
        return "TÍCH CỰC"

    elif score >= 55:
        return "KHÁ TÍCH CỰC"

    elif score >= 45:
        return "TRUNG TÍNH"

    elif score >= 30:
        return "TIÊU CỰC"

    return "RẤT TIÊU CỰC"


# ============================================================
# TECHNICAL RATING
# ============================================================

def get_rating(total_score):

    if total_score >= 85:
        return "RẤT MẠNH"

    elif total_score >= 75:
        return "MẠNH"

    elif total_score >= 65:
        return "KHÁ"

    elif total_score >= 55:
        return "TRUNG BÌNH"

    return "YẾU"


# ============================================================
# ACTION
# ============================================================

def get_action(
    row,
    total_score,
    market_score_value
):

    rsi = safe_float(
        row.get("RSI14"),
        50
    )

    vol_ratio = safe_float(
        row.get("VOL_RATIO"),
        1
    )

    dist20 = safe_float(
        row.get("DIST_MA20"),
        0
    )

    rel20 = safe_float(
        row.get("REL_RET20"),
        0
    )

    # --------------------------
    # Overheated
    # --------------------------

    if rsi >= 75 or dist20 >= 0.10:
        return (
            "⛔ KHÔNG MUA ĐUỔI - "
            "CHỜ PULLBACK"
        )

    # --------------------------
    # Very bad market
    # --------------------------

    if market_score_value < 30:
        return (
            "🔴 ĐỨNG NGOÀI / QUAN SÁT"
        )

    # --------------------------
    # Extremely weak volume
    # --------------------------

    if vol_ratio < 0.30:
        return (
            "🟡 CHỜ DÒNG TIỀN XÁC NHẬN"
        )

    # --------------------------
    # Negative market
    # --------------------------

    if market_score_value < 45:

        if (
            total_score >= 75
            and rel20 > 0
            and vol_ratio >= 0.60
        ):
            return (
                "🟢 CANH MUA THĂM DÒ "
                "TẠI VÙNG MUA"
            )

        return (
            "🟠 THEO DÕI - "
            "CHỜ ĐIỂM VÀO TỐT"
        )

    # --------------------------
    # Normal / positive market
    # --------------------------

    if (
        total_score >= 80
        and vol_ratio >= 0.80
    ):
        return (
            "🟢 SETUP TỐT - "
            "CANH MUA TẠI VÙNG MUA"
        )

    if (
        total_score >= 70
        and rel20 > 0
    ):
        return (
            "🟢 CANH MUA TẠI VÙNG MUA"
        )

    if total_score >= 60:
        return "🟡 THEO DÕI"

    return "🔴 CHƯA ĐẠT ĐIỀU KIỆN"


# ============================================================
# PRICE STRUCTURE PRO
# ============================================================

def add_price_structure(scanner):
    scanner = scanner.copy()
    scanner["date"] = pd.to_datetime(scanner["date"])
    scanner = scanner.sort_values(["symbol", "date"]).reset_index(drop=True)

    g = scanner.groupby("symbol", group_keys=False)

    prev_close = g["close"].shift(1)
    tr = pd.concat([
        scanner["high"] - scanner["low"],
        (scanner["high"] - prev_close).abs(),
        (scanner["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)

    scanner["TR"] = tr
    scanner["ATR14"] = tr.groupby(scanner["symbol"]).transform(
        lambda s: s.rolling(14, min_periods=5).mean()
    )

    # Previous ranges exclude today's bar so breakout tests are meaningful.
    scanner["PREV_HIGH20"] = g["high"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=5).max()
    )
    scanner["PREV_LOW20"] = g["low"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=5).min()
    )
    scanner["PREV_HIGH60"] = g["high"].transform(
        lambda s: s.shift(1).rolling(60, min_periods=10).max()
    )
    scanner["PREV_LOW60"] = g["low"].transform(
        lambda s: s.shift(1).rolling(60, min_periods=10).min()
    )

    # Recent swing references.
    # A pivot at day t is only known after two later bars exist.
    # Therefore shift(2) is applied after detecting the local pivot.
    # transform() preserves the original dataframe and the symbol column.
    def confirmed_swing_high(h):
        pivot = h.where(
            (h > h.shift(1)) &
            (h >= h.shift(2)) &
            (h >= h.shift(-1)) &
            (h >= h.shift(-2))
        )
        return pivot.shift(2).ffill()

    def confirmed_swing_low(l):
        pivot = l.where(
            (l < l.shift(1)) &
            (l <= l.shift(2)) &
            (l <= l.shift(-1)) &
            (l <= l.shift(-2))
        )
        return pivot.shift(2).ffill()

    scanner["SWING_HIGH"] = g["high"].transform(confirmed_swing_high)
    scanner["SWING_LOW"] = g["low"].transform(confirmed_swing_low)

    return scanner


def volume_status(vol_ratio):
    if vol_ratio < 0.30:
        return "🔴 RẤT YẾU"
    if vol_ratio < 0.60:
        return "🟡 CHƯA XÁC NHẬN"
    if vol_ratio < 0.80:
        return "🟡 DƯỚI TRUNG BÌNH"
    if vol_ratio < 1.20:
        return "⚪ BÌNH THƯỜNG"
    if vol_ratio < 1.50:
        return "🟢 DÒNG TIỀN TĂNG"
    return "🔥 DÒNG TIỀN MẠNH"


def classify_setup(row):
    close = safe_float(row.get("close"))
    ma20 = safe_float(row.get("MA20"), close)
    ma50 = safe_float(row.get("MA50"), close)
    rsi = safe_float(row.get("RSI14"), 50)
    dist20 = safe_float(row.get("DIST_MA20"), 0)
    vol_ratio = safe_float(row.get("VOL_RATIO"), 0)
    prev_high20 = safe_float(row.get("PREV_HIGH20"))
    atr = safe_float(row.get("ATR14"), close * 0.025)

    if rsi >= 75 or dist20 >= 0.10:
        return "🔥 OVEREXTENDED"

    if not pd.isna(prev_high20) and close > prev_high20:
        if vol_ratio >= 1.20:
            return "🚀 BREAKOUT XÁC NHẬN"
        return "⚠️ BREAKOUT CHƯA CÓ VOLUME"

    near_ma20 = abs(close - ma20) <= max(atr, close * 0.025)
    if close >= ma50 and near_ma20 and ma20 >= ma50:
        return "🟢 PULLBACK"

    if close > ma20 > ma50:
        return "📈 TREND FOLLOWING"

    return "⚪ NEUTRAL"


def nearest_support(row):
    close = safe_float(row.get("close"))
    candidates = [
        safe_float(row.get("MA20")),
        safe_float(row.get("MA50")),
        safe_float(row.get("SWING_LOW")),
        safe_float(row.get("PREV_LOW20")),
        safe_float(row.get("PREV_LOW60")),
    ]
    valid = [x for x in candidates if not pd.isna(x) and 0 < x < close]
    if not valid:
        return np.nan
    return max(valid)


def resistance_levels(row):
    close = safe_float(row.get("close"))
    candidates = [
        safe_float(row.get("SWING_HIGH")),
        safe_float(row.get("PREV_HIGH20")),
        safe_float(row.get("PREV_HIGH60")),
    ]
    valid = sorted(set(
        round(x, 8) for x in candidates
        if not pd.isna(x) and x > close * 1.002
    ))
    near = valid[0] if valid else np.nan
    far = valid[1] if len(valid) > 1 else np.nan
    return near, far


def calculate_price_plan(row, total_score, mkt_score):
    close = safe_float(row.get("close"))
    ma20 = safe_float(row.get("MA20"), close)
    rsi = safe_float(row.get("RSI14"), 50)
    atr = safe_float(row.get("ATR14"), close * 0.025)
    volatility = safe_float(row.get("VOLATILITY20"), 0.025)
    support = nearest_support(row)
    resistance1, resistance2 = resistance_levels(row)
    setup = classify_setup(row)

    if pd.isna(close) or close <= 0:
        raise ValueError("Gia close khong hop le.")

    if pd.isna(atr) or atr <= 0:
        atr = close * clamp(volatility, 0.015, 0.06)

    # BUY ZONE
    if "OVEREXTENDED" in setup:
        anchor = ma20 if not pd.isna(ma20) and ma20 > 0 else close - atr
        buy_high = min(close * 0.975, anchor + 0.50 * atr)
        buy_low = max(0.01, anchor - 0.35 * atr)
    elif "BREAKOUT" in setup:
        breakout_level = safe_float(row.get("PREV_HIGH20"), close)
        buy_low = max(0.01, breakout_level - 0.25 * atr)
        buy_high = min(close * 1.005, breakout_level + 0.45 * atr)
    elif "PULLBACK" in setup:
        anchor = support if not pd.isna(support) else ma20
        buy_low = max(0.01, anchor - 0.20 * atr)
        buy_high = min(close * 1.005, anchor + 0.45 * atr)
    else:
        anchor = support if not pd.isna(support) and support >= close - 1.5 * atr else close
        buy_low = max(0.01, min(close, anchor) - 0.35 * atr)
        buy_high = close

    if buy_low > buy_high:
        buy_low, buy_high = buy_high, buy_low
    reference_buy = (buy_low + buy_high) / 2

    # STOP: combine ATR and actual support. Keep a safety cap against absurdly wide stops.
    stop_atr = reference_buy - 1.60 * atr
    if not pd.isna(support) and support < reference_buy:
        stop_structure = support - 0.35 * atr
        stop_loss = min(stop_atr, stop_structure)
    else:
        stop_loss = stop_atr

    stop_floor = reference_buy * 0.90
    stop_loss = max(stop_loss, stop_floor)
    stop_loss = min(stop_loss, reference_buy * 0.985)
    risk_amount = max(reference_buy - stop_loss, reference_buy * 0.015)

    # TARGETS: prefer actual resistance. If unavailable, use conservative risk targets.
    fallback1 = reference_buy + risk_amount * 1.50
    fallback2 = reference_buy + risk_amount * 2.30

    target1 = resistance1 if not pd.isna(resistance1) else fallback1

    if not pd.isna(resistance2) and resistance2 > target1:
        target2 = resistance2
        target2_type = "STRUCTURE"
    elif not pd.isna(resistance1):
        # No second structural resistance is available:
        # show an expansion target, but label it explicitly.
        target2 = max(resistance1 + 0.80 * atr, fallback2)
        target2_type = "EXTENDED"
    else:
        target2 = fallback2
        target2_type = "EXTENDED"

    # Do not present targets below the planned entry.
    target1 = max(target1, reference_buy + 0.50 * risk_amount)
    target2 = max(target2, target1 + 0.50 * atr)

    rr1 = (target1 - reference_buy) / risk_amount
    rr2 = (target2 - reference_buy) / risk_amount

    return {
        "setup": setup,
        "support": support,
        "resistance1": resistance1,
        "resistance2": resistance2,
        "atr14": atr,
        "buy_low": buy_low,
        "buy_high": buy_high,
        "reference_buy": reference_buy,
        "stop_loss": stop_loss,
        "stop_pct": stop_loss / reference_buy - 1,
        "target1": target1,
        "target1_pct": target1 / reference_buy - 1,
        "target2": target2,
        "target2_pct": target2 / reference_buy - 1,
        "rr1": rr1,
        "rr2": rr2,
        "target2_type": target2_type,
    }


# ============================================================
# ANALYZE STOCK
# ============================================================

def analyze_stock(row):
    trend = score_trend(row)
    momentum = score_momentum(row)
    relative = score_relative_strength(row)
    liquidity = score_liquidity(row)
    risk = score_risk(row)
    total = round(clamp(trend + momentum + relative + liquidity + risk, 0, 100), 1)
    mkt_score = market_score(row)

    price_plan = calculate_price_plan(row, total, mkt_score)
    action = get_action(row, total, mkt_score)

    # Structure-aware overrides. These are post-model rules only.
    vol_ratio = safe_float(row.get("VOL_RATIO"), 0)
    rr_near = price_plan["rr1"]

    if "OVEREXTENDED" in price_plan["setup"]:
        action = "⛔ KHÔNG MUA ĐUỔI - CHỜ PULLBACK"
    elif rr_near < 1.0:
        action = "🔴 KHÔNG MUA - R:R TỚI KHÁNG CỰ KHÔNG ĐẠT"
    elif vol_ratio < 0.30:
        action = "🟡 CHỜ DÒNG TIỀN XÁC NHẬN"
    elif "BREAKOUT CHƯA" in price_plan["setup"]:
        action = "🟡 CHỜ VOLUME XÁC NHẬN BREAKOUT"
    elif mkt_score < 30:
        action = "🔴 ĐỨNG NGOÀI / QUAN SÁT"
    elif mkt_score < 45:
        # Negative market: downgrade any green buy action by one level.
        if action.startswith("🟢"):
            if rr_near >= 1.5 and vol_ratio >= 0.60:
                action = "🟡 CANH THĂM DÒ - CHỜ XÁC NHẬN"
            else:
                action = "🟠 THEO DÕI - CHỜ ĐIỂM VÀO TỐT"

    return {
        "trend": trend,
        "momentum": momentum,
        "relative": relative,
        "liquidity": liquidity,
        "risk": risk,
        "total": total,
        "rating": get_rating(total),
        "market_score": mkt_score,
        "market_label": market_label(mkt_score),
        "action": action,
        **price_plan,
    }


# ============================================================
# LOAD DATA
# ============================================================

def load_data():
    top5 = pd.read_csv(TOP5_FILE)
    scanner = pd.read_csv(SCANNER_FILE)

    top5["date"] = pd.to_datetime(top5["date"]).dt.date

    scanner = add_price_structure(scanner)
    scanner["date"] = pd.to_datetime(scanner["date"]).dt.date

    signal_date = top5["date"].max()
    scanner_today = scanner[scanner["date"] == signal_date].copy()

    merged = top5.merge(
        scanner_today,
        on=["date", "symbol"],
        how="left",
        suffixes=("_top5", ""),
    )

    if "RANK" in merged.columns:
        merged = merged.sort_values("RANK")

    return merged, signal_date


def prepare_analyses(df):
    records = []
    for _, row in df.head(5).iterrows():
        a = analyze_stock(row)

        # SETUP SCORE: post-model entry quality only.
        # Frozen V2 AI Score is NOT changed or fed back into the model.
        vol_ratio = safe_float(row.get("VOL_RATIO"), 0)
        rsi = safe_float(row.get("RSI14"), 50)
        rr1 = a["rr1"]

        setup_score = a["total"]

        # Reward usable room to the nearest resistance.
        if rr1 >= 2.0:
            setup_score += 8
        elif rr1 >= 1.5:
            setup_score += 5
        elif rr1 >= 1.0:
            setup_score += 1
        else:
            setup_score -= 15

        # Volume confirmation.
        if vol_ratio >= 1.20:
            setup_score += 5
        elif vol_ratio >= 0.80:
            setup_score += 2
        elif vol_ratio < 0.30:
            setup_score -= 10
        elif vol_ratio < 0.60:
            setup_score -= 5

        # Entry-state penalties.
        if "OVEREXTENDED" in a["setup"] or rsi >= 75:
            setup_score -= 15
        if "BREAKOUT CHƯA" in a["setup"]:
            setup_score -= 5

        # Market regime affects execution quality, not the Frozen V2 AI rank.
        if a["market_score"] < 30:
            setup_score -= 12
        elif a["market_score"] < 45:
            setup_score -= 6
        elif a["market_score"] >= 65:
            setup_score += 3

        a["setup_score"] = round(clamp(setup_score, 0, 100), 1)
        records.append((row, a))

    order = sorted(
        range(len(records)),
        key=lambda i: (
            records[i][1]["setup_score"],
            records[i][1]["rr1"],
            records[i][1]["total"],
        ),
        reverse=True,
    )
    setup_rank = {idx: rank + 1 for rank, idx in enumerate(order)}
    return records, setup_rank


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_messages(df, signal_date):
    now = datetime.now()
    records, setup_rank = prepare_analyses(df)

    if not records:
        raise RuntimeError("Khong co du lieu TOP5.")

    first = records[0][1]

    dashboard = [
        "📊 TOP 5 AI CHỨNG KHOÁN — PRO",
        "",
        f"📅 Dữ liệu: {signal_date.strftime('%d/%m/%Y')}",
        f"🕒 Gửi lúc: {now.strftime('%H:%M %d/%m/%Y')}",
        f"🌐 THỊ TRƯỜNG: {first['market_label']} ({first['market_score']:.1f}/100)",
        "",
        "🏁 DASHBOARD",
    ]

    for i, (row, a) in enumerate(records):
        symbol = str(row.get("symbol", ""))
        ai = safe_float(row.get("SCORE"), safe_float(row.get("SCORE_top5")))
        ai_txt = f"{ai:.4f}" if not pd.isna(ai) else "N/A"
        dashboard.append(
            f"#{i+1} {symbol} | AI {ai_txt} | Setup #{setup_rank[i]} "
            f"({a['setup_score']:.1f}) | KT {a['total']:.1f} | {a['setup']}"
        )

    dashboard += [
        "",
        "ℹ️ AI Rank = Frozen V2. Setup Rank = lớp kỹ thuật sau model.",
        "⚠️ Paper Scanner — không phải khuyến nghị mua/bán.",
    ]

    messages = ["\n".join(dashboard)]

    for i, (row, a) in enumerate(records):
        symbol = str(row.get("symbol", ""))
        exchange = row.get("exchange_top5", row.get("exchange", ""))
        if pd.isna(exchange):
            exchange = ""
        close = safe_float(row.get("close"), safe_float(row.get("close_top5")))
        ai = safe_float(row.get("SCORE"), safe_float(row.get("SCORE_top5")))
        rsi = safe_float(row.get("RSI14"), 0)
        vol_ratio = safe_float(row.get("VOL_RATIO"), 0)
        rel20 = safe_float(row.get("REL_RET20"), safe_float(row.get("REL_RET20_top5"), 0))
        dist20 = safe_float(row.get("DIST_MA20"), 0)

        resistance_txt = (
            fmt_price(a["resistance1"])
            if not pd.isna(a["resistance1"]) else "chưa xác định"
        )
        support_txt = (
            fmt_price(a["support"])
            if not pd.isna(a["support"]) else "chưa xác định"
        )

        warnings = []
        if rsi >= 75:
            warnings.append("RSI quá nóng")
        elif rsi >= 70:
            warnings.append("RSI khá cao")
        if vol_ratio < 0.30:
            warnings.append("dòng tiền rất yếu")
        elif vol_ratio < 0.60:
            warnings.append("dòng tiền chưa xác nhận")
        if dist20 >= 0.10:
            warnings.append("giá cách xa MA20")
        if first["market_score"] < 45:
            warnings.append("thị trường tiêu cực")
        if a["rr1"] < 1.0:
            warnings.append("R:R tới kháng cự gần dưới 1.0")
        elif a["rr1"] < 1.5:
            warnings.append("R:R tới kháng cự gần chỉ ở mức trung bình")

        if not pd.isna(a["resistance1"]) and close > 0:
            upside = a["resistance1"] / close - 1
            if upside < 0.03:
                warnings.append("dư địa tới kháng cự gần thấp")

        buy_instruction = (
            "CHỜ GIÁ VỀ VÙNG"
            if close > a["buy_high"] * 1.002
            else "GIÁ ĐANG TRONG/GẦN VÙNG"
        )
        target2_label = (
            "Target 2 cấu trúc"
            if a["target2_type"] == "STRUCTURE"
            else "Target mở rộng"
        )

        lines = [
            f"🏆 AI #{i+1} | 🎯 Setup #{setup_rank[i]} ({a['setup_score']:.1f}) — {symbol} ({exchange})",
            f"🤖 AI Score: {ai:.4f}" if not pd.isna(ai) else "🤖 AI Score: N/A",
            f"⭐ Kỹ thuật: {a['total']:.1f}/100 — {a['rating']}",
            f"🧭 Setup: {a['setup']}",
            f"⚡ Hành động: {a['action']}",
            "",
            f"💵 Giá: {fmt_price(close)}",
            f"🟢 Buy Zone: {fmt_price(a['buy_low'])} – {fmt_price(a['buy_high'])} | {buy_instruction}",
            f"🧱 Hỗ trợ gần: {support_txt}",
            f"🚧 Kháng cự gần: {resistance_txt}",
            f"📐 ATR14: {fmt_price(a['atr14'])}",
            f"🛑 Cut loss: {fmt_price(a['stop_loss'])} ({a['stop_pct']:.1%})",
            f"🎯 Target 1: {fmt_price(a['target1'])} (+{a['target1_pct']:.1%})",
            f"🚀 {target2_label}: {fmt_price(a['target2'])} (+{a['target2_pct']:.1%})",
            f"⚖️ R:R kế hoạch: 1:{a['rr1']:.2f} → 1:{a['rr2']:.2f}",
            "",
            f"📈 RSI14: {rsi:.1f}",
            f"💧 Volume: {vol_ratio:.2f}x — {volume_status(vol_ratio)}",
            f"💪 Relative 20D: {rel20:+.2%}",
            f"📏 Cách MA20: {dist20:+.2%}",
            "",
            "📋 Điểm thành phần: "
            f"Trend {a['trend']:.1f}/25 | Momentum {a['momentum']:.1f}/20 | "
            f"RS {a['relative']:.1f}/25 | Money {a['liquidity']:.1f}/15 | "
            f"Risk {a['risk']:.1f}/15",
        ]

        if warnings:
            lines += ["", "⚠️ " + "; ".join(warnings) + "."]

        lines += [
            "",
            "ℹ️ Support/Resistance/ATR được tính từ dữ liệu giá lịch sử; "
            "Target và Stop là vùng quản trị tham khảo.",
        ]
        messages.append("\n".join(lines))

    return messages


# ============================================================
# TELEGRAM
# ============================================================

def load_config():
    # GitHub Actions / cloud: read secrets from environment variables first.
    token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()

    if token and chat_id:
        return token, chat_id

    # Local Windows fallback: keep using telegram_config.json.
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Khong tim thay Telegram config. "
            "Hay cau hinh BOT_TOKEN va CHAT_ID trong environment/GitHub Secrets "
            "hoac tao telegram_config.json khi chay local."
        ) from exc

    token = str(config.get("BOT_TOKEN", "")).strip()
    chat_id = str(config.get("CHAT_ID", "")).strip()

    if not token or not chat_id:
        raise RuntimeError(
            "Telegram config thieu BOT_TOKEN hoac CHAT_ID."
        )

    return token, chat_id


def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
    }).encode("utf-8")

    request = urllib.request.Request(url, data=payload, method="POST")
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    with urllib.request.urlopen(
        request, timeout=30, context=ssl_context
    ) as response:
        return response.read().decode("utf-8")


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 65)
    print("TOP5 AI - TELEGRAM PRO ANALYSIS")
    print("=" * 65)

    df, signal_date = load_data()
    if df.empty:
        raise RuntimeError("Khong co du lieu TOP5.")

    messages = build_messages(df, signal_date)
    token, chat_id = load_config()

    for i, message in enumerate(messages, 1):
        print()
        print(f"--- MESSAGE {i}/{len(messages)} ---")
        print(message)
        # Each message is deliberately small, avoiding Telegram's long-message limit.
        send_telegram(token, chat_id, message)

    print()
    print("=" * 65)
    print(f"GUI TELEGRAM THANH CONG - {len(messages)} TIN NHAN")
    print("=" * 65)


if __name__ == "__main__":
    main()
