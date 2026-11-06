"""
LƯỢT 11:35 – TẠO BỘ DỮ LIỆU PHIÊN SÁNG RIÊNG (không đụng stock_cache EOD).

  stock_cache/          = CHỈ dữ liệu EOD hoàn chỉnh (production, không bao giờ ghi nến dở vào đây)
  stock_cache_midday/   = bản sao EOD + nến phiên sáng hôm nay (chỉ dùng trong lượt 11:35, không lưu cache)

Nến phiên sáng được tổng hợp từ nến phút SSI, đến 11:30 giờ Việt Nam.
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
import ssi_client

ROOT = Path(__file__).resolve().parent
EOD_CACHE = ROOT / "stock_cache"
MIDDAY_CACHE = ROOT / "stock_cache_midday"
UNIVERSE = ROOT / "quality_universe.csv"
INFO_FILE = ROOT / "MIDDAY_SESSION.json"

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# Phiên sáng thường chiếm ~45% khối lượng cả ngày (phiên chiều + ATC nhiều hơn).
MORNING_VOLUME_FRACTION = 0.45
MIN_COVERAGE = 0.90
SAMPLES = ("FPT", "VCB", "HPG", "MWG", "TCB")
BAD = []


def main():
    now = datetime.now(VN_TZ)
    today = pd.Timestamp(now.date())
    print("=" * 70)
    print(f"Run: {now:%H:%M}")
    print("Data session: MORNING SESSION - SSI")
    print(f"Data date: {today:%d/%m/%Y}")
    print("=" * 70)
    if now.weekday() >= 5 or (now.hour, now.minute) < (11, 30):
        raise SystemExit("BLOCK MIDDAY: chua ket thuc phien sang hoac cuoi tuan.")

    syms = pd.read_csv(UNIVERSE).iloc[:,0].astype(str).str.strip().str.upper().drop_duplicates().tolist()
    if MIDDAY_CACHE.exists():
        shutil.rmtree(MIDDAY_CACHE)
    shutil.copytree(EOD_CACHE, MIDDAY_CACHE)

    updated, missing, bad = 0, [], []
    for s in syms:
        p = MIDDAY_CACHE / f"{s}.csv"
        if not p.exists():
            missing.append(s); continue
        r = ssi_client.morning_bar(s, today)
        time.sleep(0.7)
        if r is None:
            missing.append(s); continue
        old = pd.read_csv(p)
        old["time"] = pd.to_datetime(old["time"], errors="coerce").dt.normalize()
        old = old.dropna(subset=["time"]).sort_values("time")
        if old.empty:
            missing.append(s); continue
        prev = old[old.time < today]
        if prev.empty:
            missing.append(s); continue
        prev_close = float(pd.to_numeric(prev["close"],errors="coerce").dropna().iloc[-1])
        chg = float(r['close'])/prev_close-1 if prev_close>0 else 0
        if abs(chg) > 0.16:
            bad.append(s); missing.append(s); continue
        row = pd.DataFrame([{
            "time": today, "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r['close']),
            "volume": round(float(r["volume"])/MORNING_VOLUME_FRACTION)
        }])
        out = pd.concat([prev,row],ignore_index=True).sort_values("time")
        out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%d")
        out.to_csv(p,index=False,encoding="utf-8-sig")
        updated += 1

    for s in missing:
        p=MIDDAY_CACHE/f"{s}.csv"
        if p.exists(): p.unlink()

    cov=updated/max(len(syms),1)
    info={"session":"MORNING","data_date":today.strftime("%Y-%m-%d"),"run_time":now.strftime("%Y-%m-%d %H:%M"),
          "updated":updated,"universe":len(syms),"coverage":round(cov,4),
          "volume_fraction":MORNING_VOLUME_FRACTION,"sources":{"SSI_V3":updated},"bad":len(bad)}
    INFO_FILE.write_text(json.dumps(info,ensure_ascii=False,indent=1),encoding="utf-8")
    print(f"MIDDAY UPDATED: {updated}/{len(syms)} ({cov:.1%}) | missing={len(missing)} | bad={len(bad)}")
    if len(bad) > 0.05*len(syms):
        raise SystemExit(f"BLOCK MIDDAY: {len(bad)} ma gia bat thuong.")
    if cov < MIN_COVERAGE:
        raise SystemExit(f"BLOCK MIDDAY: coverage {cov:.1%} < {MIN_COVERAGE:.0%}.")
    print("MIDDAY CACHE OK ->",MIDDAY_CACHE)

if __name__ == "__main__":
    sys.exit(main())
