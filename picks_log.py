# -*- coding: utf-8 -*-
"""Append/replace TOP5 AI Telegram picks in data/picks_log.csv."""
from pathlib import Path
import csv

PATH=Path("data/picks_log.csv")
FIELDS=["run_date","session","rank","symbol","price","stop","t1","status","priority"]

def _status(c):
    a=str(c.get("action",""))
    return "MUA" if ("ĐỦ ĐIỀU KIỆN" in a and "MUA" in a) else a.replace("🟢","").replace("🟠","").replace("🟡","").replace("🔴","").replace("⛔","").strip()

def write_picks(run_date,session,candidates):
    session=str(session).upper()
    run_date=str(run_date)
    old=[]
    if PATH.exists():
        with PATH.open("r",encoding="utf-8-sig",newline="") as f:
            old=[r for r in csv.DictReader(f) if not (r["run_date"]==run_date and r["session"]==session)]
    eligible=[c for c in candidates if c.get("symbol")]
    priority_symbol=None
    for c in eligible:
        if _status(c)=="MUA":
            priority_symbol=str(c["symbol"]).upper(); break
    rows=[]
    for i,c in enumerate(eligible,1):
        rows.append({
            "run_date":run_date,"session":session,"rank":i,
            "symbol":str(c["symbol"]).upper(),"price":c.get("close",""),
            "stop":c.get("stop",""),"t1":c.get("target1",""),
            "status":_status(c),"priority":1 if str(c["symbol"]).upper()==priority_symbol else 0,
        })
    PATH.parent.mkdir(parents=True,exist_ok=True)
    with PATH.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(old+rows)
    print(f"PICKS LOG: {run_date} {session} -> {len(rows)} rows")
