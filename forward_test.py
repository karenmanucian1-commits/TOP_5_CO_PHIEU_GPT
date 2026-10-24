# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import os, sys, math, urllib.parse, urllib.request, ssl
import certifi
import pandas as pd
import kbs_client

ROOT=Path(__file__).resolve().parent
SIGNALS=ROOT/"FORWARD_SIGNALS.csv"
RESULTS=ROOT/"FORWARD_RESULTS.csv"
VN=ZoneInfo("Asia/Ho_Chi_Minh")
CAPITAL=100_000_000.0
HORIZON=20

SIGNAL_COLS=["signal_date","signal_time","bot","strategy_version","symbol","entry_price","buy_low","buy_high","stop","target1","target2","rr1","score","status"]
RESULT_COLS=["signal_date","bot","strategy_version","symbol","entry_price","trade_day","t","close","return_pct","pnl_100m_vnd","high_since","low_since","mfe_pct","mae_pct","target1_hit","target2_hit","stop_hit","first_event"]

def fnum(x,default=float("nan")):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except: return default

def load_csv(path,cols):
    if not path.exists(): return pd.DataFrame(columns=cols)
    try: d=pd.read_csv(path)
    except pd.errors.EmptyDataError: return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in d.columns: d[c]=pd.NA
    return d

def save_signals(new_rows):
    old=load_csv(SIGNALS,SIGNAL_COLS)
    add=pd.DataFrame(new_rows,columns=SIGNAL_COLS)
    d=pd.concat([old,add],ignore_index=True)
    if not d.empty:
        d["symbol"]=d["symbol"].astype(str).str.upper().str.strip()
        d=d.drop_duplicates(["signal_date","bot","strategy_version","symbol"],keep="first")
        d=d.sort_values(["signal_date","bot","symbol"])
    d.to_csv(SIGNALS,index=False,encoding="utf-8-sig")
    return len(add),len(d)

def capture():
    now=datetime.now(VN)
    rows=[]

    # V2: only actual green BUY signal, never orange WAIT.
    import ai_decision_top5 as v2
    candidates, signal_eod=v2.build_decision()
    for c in candidates:
        if not v2.is_eligible_candidate(c): continue
        if c.get("action")!="🟢 ĐỦ ĐIỀU KIỆN THEO DÕI MUA": continue
        rows.append({
            "signal_date":now.date().isoformat(),"signal_time":now.strftime("%Y-%m-%d %H:%M:%S"),
            "bot":"CỔ PHIẾU ĐẠT CHUẨN GPT","strategy_version":"V2_FROZEN_DECISION_2026-10-24",
            "symbol":c["symbol"],"entry_price":c["close"],"buy_low":c["buy_low"],"buy_high":c["buy_high"],
            "stop":c["stop"],"target1":c["target1"],"target2":c["target2"],"rr1":c["current_rr1"],
            "score":c["decision_score"],"status":"BUY"
        })

    # V3: only actual green CANH MUA at EOD price.
    import send_top5_advanced_v3_7_telegram as v3tg
    p=ROOT/"TOP5_ADVANCED_V3.csv"
    if p.exists():
        d=pd.read_csv(p)
        for _,x in d.iterrows():
            if v3tg.action_label(x,False)!="🟢 CANH MUA": continue
            rows.append({
                "signal_date":now.date().isoformat(),"signal_time":now.strftime("%Y-%m-%d %H:%M:%S"),
                "bot":"CỔ PHIẾU CHẤT LƯỢNG GPT","strategy_version":"V3_7_GATE_2026-10-24",
                "symbol":str(x.symbol).upper(),"entry_price":x.close,"buy_low":x.buy_low,"buy_high":x.buy_high,
                "stop":x.stop,"target1":x.target1,"target2":x.target2,"rr1":x.rr1,
                "score":x.advanced_score,"status":"BUY"
            })

    added,total=save_signals(rows)
    print(f"FORWARD CAPTURE: {added} BUY signals seen | ledger total {total}")

def fetch_history(symbol,start,end):
    d=kbs_client.lich_su_gia(symbol,start,end)
    if d is None or d.empty: return pd.DataFrame()
    d=d.copy()
    d["time"]=pd.to_datetime(d["time"],errors="coerce").dt.date
    return d.dropna(subset=["time","close"]).sort_values("time").drop_duplicates("time")

def evaluate():
    sig=load_csv(SIGNALS,SIGNAL_COLS)
    if sig.empty:
        pd.DataFrame(columns=RESULT_COLS).to_csv(RESULTS,index=False,encoding="utf-8-sig")
        return sig,pd.DataFrame(columns=RESULT_COLS)
    today=date.today()
    all_rows=[]
    for _,s in sig.iterrows():
        sd=pd.to_datetime(s.signal_date).date()
        # enough calendar room for 20 trading sessions; KBS returns only trading bars
        hist=fetch_history(str(s.symbol),sd.isoformat(),today.isoformat())
        if hist.empty: continue
        hist=hist[hist["time"]>=sd].copy().head(HORIZON)
        entry=fnum(s.entry_price); stop=fnum(s.stop); t1=fnum(s.target1); t2=fnum(s.target2)
        if not math.isfinite(entry) or entry<=0: continue
        first_event=""
        hi=-float("inf"); lo=float("inf")
        for i,r in enumerate(hist.itertuples(index=False),1):
            hi=max(hi,fnum(r.high,-float("inf"))); lo=min(lo,fnum(r.low,float("inf")))
            hit_stop=math.isfinite(stop) and fnum(r.low,float("inf"))<=stop
            hit_t1=math.isfinite(t1) and fnum(r.high,-float("inf"))>=t1
            hit_t2=math.isfinite(t2) and fnum(r.high,-float("inf"))>=t2
            # Conservative daily-bar rule: if stop and target are both inside same bar, STOP wins.
            event=""
            if hit_stop: event="STOP"
            elif hit_t2: event="TARGET2"
            elif hit_t1: event="TARGET1"
            if not first_event and event: first_event=event
            close=fnum(r.close)
            ret=(close/entry-1)*100
            all_rows.append({
                "signal_date":s.signal_date,"bot":s.bot,"strategy_version":s.strategy_version,"symbol":s.symbol,
                "entry_price":entry,"trade_day":r.time.isoformat(),"t":i,"close":close,"return_pct":ret,
                "pnl_100m_vnd":CAPITAL*ret/100,"high_since":hi,"low_since":lo,
                "mfe_pct":(hi/entry-1)*100,"mae_pct":(lo/entry-1)*100,
                "target1_hit":bool(hit_t1),"target2_hit":bool(hit_t2),"stop_hit":bool(hit_stop),
                "first_event":first_event
            })
    res=pd.DataFrame(all_rows,columns=RESULT_COLS)
    if not res.empty: res=res.sort_values(["signal_date","bot","symbol","t"])
    res.to_csv(RESULTS,index=False,encoding="utf-8-sig")
    return sig,res

def money(v):
    try:return f"{float(v):+,.0f}đ"
    except:return "-"

def pct(v):
    try:return f"{float(v):+.2f}%"
    except:return "-"

def build_report(sig,res):
    now=datetime.now(VN)
    lines=["📊 FORWARD TEST — KIỂM ĐỊNH PHÍM MUA",now.strftime("🕕 %H:%M %d/%m/%Y"),
           "🔒 Tín hiệu đã khóa tại thời điểm bot phím mua | T1→T20 là các phiên giao dịch sau tín hiệu",""]
    if sig.empty:
        return "\n".join(lines+["Chưa có tín hiệu 🟢 mua nào để kiểm định."])
    active=0
    for bot in ["CỔ PHIẾU ĐẠT CHUẨN GPT","CỔ PHIẾU CHẤT LƯỢNG GPT"]:
        sb=sig[sig.bot==bot]
        if sb.empty: continue
        lines += ["━━━━━━━━━━━━━━━━━━━━",("🤖 " if "ĐẠT CHUẨN" in bot else "🚀 ")+bot]
        for _,s in sb.sort_values(["signal_date","symbol"]).iterrows():
            rr=res[(res.bot==bot)&(res.symbol.astype(str)==str(s.symbol))&(res.signal_date.astype(str)==str(s.signal_date))]
            if rr.empty:
                lines.append(f"• {s.symbol} | Phím {fnum(s.entry_price):.2f} | đang chờ T1")
                continue
            x=rr.sort_values("t").iloc[-1]; active+=1
            event=str(x.first_event) if pd.notna(x.first_event) and str(x.first_event) else "Chưa chạm Stop/Target"
            lines.append(f"• {s.symbol} | {s.signal_date} | Phím {fnum(s.entry_price):.2f}")
            lines.append(f"  T{int(x.t)}: {fnum(x.close):.2f} | {pct(x.return_pct)} | {money(x.pnl_100m_vnd)}/100tr")
            lines.append(f"  MFE {pct(x.mfe_pct)} | MAE {pct(x.mae_pct)} | {event}")
        latest=[]
        for _,s in sb.iterrows():
            rr=res[(res.bot==bot)&(res.symbol.astype(str)==str(s.symbol))&(res.signal_date.astype(str)==str(s.signal_date))]
            if not rr.empty: latest.append(rr.sort_values("t").iloc[-1])
        if latest:
            q=pd.DataFrame(latest)
            win=(q.return_pct>0).mean()*100
            lines += [f"📈 Đang theo dõi: {len(q)} tín hiệu | Win hiện tại {win:.1f}% | Lãi/lỗ TB {pct(q.return_pct.mean())}",""]
    lines += ["━━━━━━━━━━━━━━━━━━━━","💰 Quy đổi 100 triệu/mã chỉ để dễ hình dung; % lãi/lỗ là chỉ số kiểm định chính.",
              "📌 Chưa trừ phí/thuế trong bản V1; sẽ giữ cùng một quy tắc cho toàn bộ mẫu để so sánh công bằng."]
    return "\n".join(lines)

def send(msg):
    token=(os.getenv("BOT_TOKEN") or "").strip(); chat=(os.getenv("CHAT_ID") or "").strip()
    print(msg)
    if not token or not chat: raise RuntimeError("Thieu BOT_TOKEN/CHAT_ID")
    data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
    req=urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=data,method="POST")
    with urllib.request.urlopen(req,timeout=30,context=ssl.create_default_context(cafile=certifi.where())) as r:
        r.read()

def report():
    sig,res=evaluate()
    send(build_report(sig,res))

if __name__=="__main__":
    mode=(sys.argv[1] if len(sys.argv)>1 else "report").lower()
    if mode=="capture": capture()
    elif mode=="report": report()
    else: raise SystemExit("mode: capture | report")
