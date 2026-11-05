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
BAD = []


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
        # Vietcap trả giá theo ĐỒNG (vd 26500) -> đổi về NGHÌN ĐỒNG (26.5) cho cùng đơn vị stock_cache
        "open": pd.Series(x["o"][:n], dtype=float) / 1000, "high": pd.Series(x["h"][:n], dtype=float) / 1000,
        "low": pd.Series(x["l"][:n], dtype=float) / 1000, "close": pd.Series(x["c"][:n], dtype=float) / 1000,
        "volume": x["v"][:n]})


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


def fetch_kbs_snapshot(symbols):
    """Lay snapshot KBS theo lo de tao nen phien sang nhanh, khong goi tung ma."""
    url = "https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/iss"
    headers = {"User-Agent": HEADERS["User-Agent"], "Content-Type": "application/json", "x-lang": "vi"}
    rows = []
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        r = requests.post(url, headers=headers, json={"code": ",".join(batch)}, timeout=(8, 20))
        r.raise_for_status()
        x = r.json()
        part = x if isinstance(x, list) else x.get("data", [])
        if part:
            rows.extend(part)
        time.sleep(0.05)
    if not rows:
        return {}
    q = pd.DataFrame(rows).rename(columns={"SB":"symbol","CP":"close","OP":"open","HI":"high","LO":"low","TT":"volume"})
    if "symbol" not in q.columns or "close" not in q.columns:
        return {}
    q["symbol"] = q["symbol"].astype(str).str.strip().str.upper()
    for col in ("open","high","low","close"):
        q[col] = pd.to_numeric(q.get(col), errors="coerce") / 1000.0
    q["volume"] = pd.to_numeric(q.get("volume"), errors="coerce")
    q = q.dropna(subset=["symbol","open","high","low","close","volume"])
    q = q[(q["close"] > 0) & (q["volume"] > 0)]
    return {str(r.symbol): r for r in q[["symbol","open","high","low","close","volume"]].itertuples(index=False)}

def main():
    now = datetime.now(VN_TZ)
    today = pd.Timestamp(now.date())
    print("=" * 70)
    print(f"Run: {now:%H:%M}")
    print("Data session: MORNING SESSION - KBS SNAPSHOT")
    print(f"Data date: {today:%d/%m/%Y}")
    print("=" * 70)
    if now.weekday() >= 5:
        raise SystemExit("BLOCK MIDDAY: cuoi tuan.")

    syms = pd.read_csv(UNIVERSE).iloc[:,0].astype(str).str.strip().str.upper().drop_duplicates().tolist()
    if MIDDAY_CACHE.exists():
        shutil.rmtree(MIDDAY_CACHE)
    shutil.copytree(EOD_CACHE, MIDDAY_CACHE)

    snap = fetch_kbs_snapshot(syms)
    print(f"KBS snapshot: {len(snap)}/{len(syms)}")
    updated, missing, bad = 0, [], []
    for s in syms:
        p = MIDDAY_CACHE / f"{s}.csv"
        r = snap.get(s)
        if not p.exists() or r is None:
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
        chg = float(r.close)/prev_close-1 if prev_close>0 else 0
        if abs(chg) > 0.16:
            bad.append(s); missing.append(s); continue
        row = pd.DataFrame([{
            "time": today, "open": float(r.open), "high": float(r.high),
            "low": float(r.low), "close": float(r.close),
            "volume": round(float(r.volume)/MORNING_VOLUME_FRACTION)
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
          "volume_fraction":MORNING_VOLUME_FRACTION,"sources":{"KBS_SNAPSHOT":updated},"bad":len(bad)}
    INFO_FILE.write_text(json.dumps(info,ensure_ascii=False,indent=1),encoding="utf-8")
    print(f"MIDDAY UPDATED: {updated}/{len(syms)} ({cov:.1%}) | missing={len(missing)} | bad={len(bad)}")
    if len(bad) > 0.05*len(syms):
        raise SystemExit(f"BLOCK MIDDAY: {len(bad)} ma gia bat thuong.")
    if cov < MIN_COVERAGE:
        raise SystemExit(f"BLOCK MIDDAY: coverage {cov:.1%} < {MIN_COVERAGE:.0%}.")
    print("MIDDAY CACHE OK ->",MIDDAY_CACHE)

if __name__ == "__main__":
    sys.exit(main())
