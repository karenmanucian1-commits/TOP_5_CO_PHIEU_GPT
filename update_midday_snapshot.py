from pathlib import Path
from datetime import date
import time, requests, pandas as pd
ROOT=Path(__file__).resolve().parent; CACHE=ROOT/"stock_cache"; UNIVERSE=ROOT/"quality_universe.csv"
URL="https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart"
HEADERS={"Referer":"https://trading.vietcap.com.vn/","Origin":"https://trading.vietcap.com.vn","User-Agent":"Mozilla/5.0"}
def fetch(symbol):
    b={"timeFrame":"ONE_DAY","symbols":[symbol],"to":int(time.time())+86400,"countBack":10}
    r=requests.post(URL,headers=HEADERS,json=b,timeout=30); r.raise_for_status(); x=r.json()
    if isinstance(x,list) and x and isinstance(x[0],dict): x=x[0]
    if isinstance(x,dict) and isinstance(x.get("data"),dict): x=x["data"]
    if not isinstance(x,dict) or not all(k in x for k in ("t","o","h","l","c","v")): return pd.DataFrame()
    n=min(len(x[k]) for k in ("t","o","h","l","c","v"))
    if not n:return pd.DataFrame()
    return pd.DataFrame({"time":pd.to_datetime(x["t"][:n],unit="s",utc=True).tz_convert("Asia/Ho_Chi_Minh").tz_localize(None).normalize(),"open":x["o"][:n],"high":x["h"][:n],"low":x["l"][:n],"close":x["c"][:n],"volume":x["v"][:n]})
u=pd.read_csv(UNIVERSE); syms=u.symbol.astype(str).str.strip().str.upper().drop_duplicates().tolist()
today=pd.Timestamp(date.today()); updated=0; missing=[]
print("MIDDAY SNAPSHOT - Vietcap current-session ONE_DAY candle")
for s in syms:
    try:
        q=fetch(s); q=q[q.time==today]
        if q.empty: missing.append(s); continue
        p=CACHE/f"{s}.csv"
        if not p.exists(): missing.append(s); continue
        old=pd.read_csv(p); old["time"]=pd.to_datetime(old["time"],errors="coerce").dt.normalize()
        row=q.tail(1); old=old[old.time!=today]
        out=pd.concat([old,row],ignore_index=True).sort_values("time")
        out["time"]=pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%d"); out.to_csv(p,index=False,encoding="utf-8-sig")
        updated+=1
        if s in {"FPT","VCB","HPG","MWG","TCB"}: print(f"SAMPLE {s}: {row.time.iloc[-1].date()} close={row.close.iloc[-1]} volume={row.volume.iloc[-1]}")
    except Exception as e:
        missing.append(s); print(f"ERROR {s}: {e}")
    time.sleep(0.08)
print(f"MIDDAY UPDATED: {updated}/{len(syms)} | DATE: {date.today()} | MISSING: {len(missing)}")
if updated < max(50,int(len(syms)*0.80)): raise SystemExit("MIDDAY SNAPSHOT BLOCKED: insufficient Vietcap current-session coverage")
