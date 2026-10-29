"""
LƯỢT 11:35 – TẠO BỘ DỮ LIỆU PHIÊN SÁNG RIÊNG (không đụng stock_cache EOD).

  stock_cache/          = CHỈ dữ liệu EOD hoàn chỉnh (production, không bao giờ ghi nến dở vào đây)
  stock_cache_midday/   = bản sao EOD + nến phiên sáng hôm nay (chỉ dùng trong lượt 11:35, không lưu cache)

Nến phiên sáng lấy từ Vietcap (ONE_DAY, đổi thời gian theo Asia/Ho_Chi_Minh).
Khối lượng phiên sáng được quy đổi ra cả ngày (chia MORNING_VOLUME_FRACTION) để các chỉ báo
volume (VOL_RATIO, VALUE20, money flow, liquidity) không bị méo do nến chưa hết phiên.

Ghi MIDDAY_SESSION.json để các bước sau và Telegram biết đang dùng dữ liệu phiên sáng ngày nào.
Dừng (exit 1) nếu: hôm nay không phải ngày giao dịch, nến không phải ngày hôm nay, hoặc thiếu > 10% mã.
"""
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
EOD_CACHE = ROOT / "stock_cache"
MIDDAY_CACHE = ROOT / "stock_cache_midday"
UNIVERSE = ROOT / "quality_universe.csv"
INFO_FILE = ROOT / "MIDDAY_SESSION.json"

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
URL = "https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
HEADERS = {"Referer": "https://trading.vietcap.com.vn/", "Origin": "https://trading.vietcap.com.vn",
           "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
           "Accept": "application/json, text/plain, */*"}
KBS_URL = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stocks/{sym}/data_day"

# Phiên sáng thường chiếm ~45% khối lượng cả ngày (phiên chiều + ATC nhiều hơn).
MORNING_VOLUME_FRACTION = 0.45
MIN_COVERAGE = 0.90
SAMPLES = ("FPT", "VCB", "HPG", "MWG", "TCB")


def fetch_vietcap(symbol):
    body = {"timeFrame": "ONE_DAY", "symbols": [symbol], "to": int(time.time()) + 86400, "countBack": 5}
    r = requests.post(URL, headers=HEADERS, json=body, timeout=(8, 15))
    r.raise_for_status()
    x = r.json()
    if isinstance(x, list) and x and isinstance(x[0], dict):
        x = x[0]
    if isinstance(x, dict) and isinstance(x.get("data"), dict):
        x = x["data"]
    if not isinstance(x, dict) or not all(k in x for k in ("t", "o", "h", "l", "c", "v")):
        return pd.DataFrame()
    n = min(len(x[k]) for k in ("t", "o", "h", "l", "c", "v"))
    if not n:
        return pd.DataFrame()
    return pd.DataFrame({
        "time": pd.to_datetime(pd.Series(x["t"][:n]).astype("int64"), unit="s", utc=True)
                  .dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None).dt.normalize(),
        "open": x["o"][:n], "high": x["h"][:n], "low": x["l"][:n], "close": x["c"][:n], "volume": x["v"][:n]})


def fetch_kbs(symbol, today):
    d = today.strftime("%d-%m-%Y")
    r = requests.get(KBS_URL.format(sym=symbol), params={"sdate": d, "edate": d}, timeout=(8, 15),
                     headers={"User-Agent": HEADERS["User-Agent"]})
    r.raise_for_status()
    rows = r.json().get("data_day") or []
    if not rows:
        return pd.DataFrame()
    q = pd.DataFrame(rows).rename(columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    q["time"] = pd.to_datetime(q["time"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for c in ("open", "high", "low", "close"):
        q[c] = pd.to_numeric(q[c], errors="coerce") / 1000          # KBS: đồng -> nghìn đồng (cùng đơn vị cache)
    return q[["time", "open", "high", "low", "close", "volume"]]


def main():
    now = datetime.now(VN_TZ)
    today = pd.Timestamp(now.date())
    print("=" * 70)
    print(f"Run: {now:%H:%M}")
    print("Data session: MORNING SESSION")
    print(f"Data date: {today:%d/%m/%Y}")
    print("=" * 70)
    if now.weekday() >= 5:
        raise SystemExit("BLOCK MIDDAY: hôm nay là cuối tuần, không có phiên sáng.")

    syms = pd.read_csv(UNIVERSE).iloc[:, 0].astype(str).str.strip().str.upper().drop_duplicates().tolist()
    if MIDDAY_CACHE.exists():
        shutil.rmtree(MIDDAY_CACHE)
    shutil.copytree(EOD_CACHE, MIDDAY_CACHE)

    updated, missing, src_count = 0, [], {"VCI": 0, "KBS": 0}
    vci_fail_streak = 0
    for s in syms:
        p = MIDDAY_CACHE / f"{s}.csv"
        if not p.exists():
            missing.append(s)
            continue
        row, src = pd.DataFrame(), None
        if vci_fail_streak < 10:
            try:
                q = fetch_vietcap(s)
                row = q[q.time == today].tail(1)
                src = "VCI"
                vci_fail_streak = 0 if not row.empty else vci_fail_streak
            except Exception as e:  # noqa: BLE001
                vci_fail_streak += 1
                print(f"VCI lỗi {s}: {type(e).__name__}")
        if row.empty:
            try:
                q = fetch_kbs(s, today)
                row = q[q.time == today].tail(1)
                src = "KBS"
            except Exception as e:  # noqa: BLE001
                print(f"KBS lỗi {s}: {type(e).__name__}")
        if row.empty:
            missing.append(s)
            continue
        row = row.copy()
        raw_vol = float(row["volume"].iloc[0])
        row["volume"] = round(raw_vol / MORNING_VOLUME_FRACTION)
        old = pd.read_csv(p)
        old["time"] = pd.to_datetime(old["time"], errors="coerce").dt.normalize()
        old = old[old.time < today]                       # bỏ mọi nến hôm nay cũ (nếu có)
        out = pd.concat([old, row], ignore_index=True).sort_values("time")
        out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%d")
        out.to_csv(p, index=False, encoding="utf-8-sig")
        updated += 1
        src_count[src] += 1
        if s in SAMPLES:
            print(f"SAMPLE {s} [{src}]: {today:%d/%m/%Y} close={row.close.iloc[0]} "
                  f"KL phiên sáng={raw_vol:,.0f} -> quy đổi cả ngày={row.volume.iloc[0]:,.0f}")
        time.sleep(0.08)

    cov = updated / max(len(syms), 1)
    print(f"MIDDAY UPDATED: {updated}/{len(syms)} ({cov:.0%}) | nguồn VCI {src_count['VCI']} / KBS {src_count['KBS']} "
          f"| thiếu {len(missing)}: {', '.join(missing[:30])}")
    info = {"session": "MORNING", "data_date": today.strftime("%Y-%m-%d"), "run_time": now.strftime("%Y-%m-%d %H:%M"),
            "updated": updated, "universe": len(syms), "coverage": round(cov, 4),
            "volume_fraction": MORNING_VOLUME_FRACTION, "sources": src_count}
    INFO_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")

    # Mã không có nến hôm nay: bỏ khỏi bộ phiên sáng để mặt cắt thị trường ngày hôm nay chỉ gồm mã có dữ liệu thật
    for s in missing:
        f = MIDDAY_CACHE / f"{s}.csv"
        if f.exists():
            f.unlink()
    if cov < MIN_COVERAGE:
        raise SystemExit(f"BLOCK MIDDAY: chỉ có {cov:.0%} mã có nến phiên sáng {today:%d/%m} (cần ≥ {MIN_COVERAGE:.0%}).")
    print("MIDDAY CACHE OK ->", MIDDAY_CACHE)


if __name__ == "__main__":
    sys.exit(main())
