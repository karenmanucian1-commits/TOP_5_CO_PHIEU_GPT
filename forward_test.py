# -*- coding: utf-8 -*-
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import os, sys, math, urllib.parse, urllib.request, ssl, json, base64
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

    # Use completed EOD session date, never workflow execution date.
    signal_date=now.date().isoformat()
    eod_file=ROOT/"TOP5_V2_TODAY.csv"
    if eod_file.exists():
        try:
            eod=pd.read_csv(eod_file)
            if not eod.empty and "date" in eod.columns:
                signal_date=pd.to_datetime(eod["date"].iloc[0]).date().isoformat()
        except Exception as ex:
            print(f"EOD signal date fallback to workflow date: {ex}")

    # V2: capture EVERY displayed eligible candidate and freeze its original action label.
    import ai_decision_top5 as v2
    candidates, signal_eod=v2.build_decision()
    for x in candidates:
        if not v2.is_eligible_candidate(x): continue
        action=str(x.get("action") or "").strip()
        if not action: continue
        rows.append({
            "signal_date":signal_date,"signal_time":now.strftime("%Y-%m-%d %H:%M:%S"),
            "bot":"CỔ PHIẾU ĐẠT CHUẨN GPT","strategy_version":"V2_FROZEN_DECISION_2026-10-24",
            "symbol":x["symbol"],"entry_price":x["close"],"buy_low":x["buy_low"],"buy_high":x["buy_high"],
            "stop":x["stop"],"target1":x["target1"],"target2":x["target2"],"rr1":x["current_rr1"],
            "score":x["decision_score"],"status":action
        })

    # V3.7: capture EVERY Quality-Gate candidate and freeze its Decision-Gate label.
    import send_top5_advanced_v3_7_telegram as v3tg
    p=ROOT/"TOP5_ADVANCED_V3.csv"
    if p.exists():
        d=pd.read_csv(p)
        for _,x in d.iterrows():
            action=v3tg.action_label(x,False)
            rows.append({
                "signal_date":signal_date,"signal_time":now.strftime("%Y-%m-%d %H:%M:%S"),
                "bot":"CỔ PHIẾU CHẤT LƯỢNG GPT","strategy_version":"V3_7_GATE_2026-10-24",
                "symbol":str(x.symbol).upper(),"entry_price":x.close,"buy_low":x.buy_low,"buy_high":x.buy_high,
                "stop":x.stop,"target1":x.target1,"target2":x.target2,"rr1":x.rr1,
                "score":x.advanced_score,"status":action
            })

    added,total=save_signals(rows)
    print(f"FORWARD CAPTURE: {added} displayed candidates seen | ledger total {total}")

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
        hist=hist[hist["time"]>sd].copy().head(HORIZON)
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
    """Short Telegram notification only. Detailed data stays in CSV/Drive report."""
    now=datetime.now(VN)
    return "\n".join([
        "📊 FORWARD TEST - CHỨNG KHOÁN GPT",
        f"✅ Đã cập nhật báo cáo kiểm định ngày {now.strftime('%d/%m/%Y')}.",
        "📁 Báo cáo chi tiết đã được cập nhật trên Google Drive."
    ])

def send(msg):
    token=(os.getenv("BOT_TOKEN") or "").strip(); chat=(os.getenv("CHAT_ID") or "").strip()
    print(msg)
    if not token or not chat: raise RuntimeError("Thieu BOT_TOKEN/CHAT_ID")
    data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
    req=urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=data,method="POST")
    with urllib.request.urlopen(req,timeout=30,context=ssl.create_default_context(cafile=certifi.where())) as r:
        r.read()

def build_excel(sig,res):
    """Create a management-style Forward Test workbook. Reporting layout follows
    the useful structure of the reference workbook, while all data and trading
    rules remain GPT Forward Test data/rules.
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    out=ROOT/"FORWARD_TEST_CHUNG_KHOAN_GPT.xlsx"

    bot_map={
        "CỔ PHIẾU ĐẠT CHUẨN GPT":"V2 - CỔ PHIẾU ĐẠT CHUẨN GPT",
        "CỔ PHIẾU CHẤT LƯỢNG GPT":"V3.7 - CỔ PHIẾU CHẤT LƯỢNG GPT",
    }

    sig=sig.copy()
    res=res.copy()
    if not sig.empty:
        sig["symbol"]=sig["symbol"].astype(str).str.upper().str.strip()

    # One row per original BUY signal. GPT rule: signal day=T0, next session=T1.
    details=[]
    today=date.today()
    vni_cache={}
    for _,s in sig.iterrows():
        sd=pd.to_datetime(s["signal_date"]).date()
        symbol=str(s["symbol"]).upper().strip()
        entry=fnum(s["entry_price"])
        q=res[(res["signal_date"].astype(str)==str(s["signal_date"])) &
              (res["bot"].astype(str)==str(s["bot"])) &
              (res["symbol"].astype(str).str.upper()==symbol)].copy() if not res.empty else pd.DataFrame()

        row={
            "Bot":bot_map.get(str(s["bot"]),str(s["bot"])),
            "Nhãn tại T0":str(s.get("status") or ""),
            "Ngày tín hiệu (T0)":sd,
            "Mã":symbol,
            "Giá tín hiệu":entry,
            "Vùng mua thấp":fnum(s.get("buy_low")),
            "Vùng mua cao":fnum(s.get("buy_high")),
            "Target 1":fnum(s.get("target1")),
            "Target 2":fnum(s.get("target2")),
            "Cắt lỗ":fnum(s.get("stop")),
            "Điểm":fnum(s.get("score")),
            "R:R":fnum(s.get("rr1")),
            "T0":0.0,
        }
        for t in range(1,HORIZON+1): row[f"T{t}"]=float("nan")

        if not q.empty:
            q=q.sort_values("t")
            for _,r in q.iterrows():
                t=int(r["t"])
                if 1<=t<=HORIZON: row[f"T{t}"]=fnum(r["return_pct"])
            last=q.iloc[-1]
            current=fnum(last["return_pct"])
            high=fnum(last["mfe_pct"])
            low=fnum(last["mae_pct"])
            n=int(last["t"])
            first_event=str(last.get("first_event") or "").strip()
        else:
            current=0.0; high=0.0; low=0.0; n=0; first_event=""

        # VN-Index benchmark from T0 close to latest available trading close.
        vni_ret=float("nan")
        try:
            key=(sd.isoformat(),today.isoformat())
            if key not in vni_cache:
                vh=fetch_history("VNINDEX",key[0],key[1])
                vni_cache[key]=vh
            vh=vni_cache[key]
            if vh is not None and not vh.empty:
                base=vh[vh["time"]<=sd]
                future=vh[vh["time"]>sd].head(max(n,1))
                if not base.empty and not future.empty:
                    b=fnum(base.iloc[-1]["close"]); e=fnum(future.iloc[-1]["close"])
                    if b>0 and e>0: vni_ret=(e/b-1)*100
        except Exception as ex:
            print(f"VNI benchmark unavailable for {sd}: {ex}")

        if first_event=="STOP": plan="Dính cắt lỗ"
        elif first_event in ("TARGET1","TARGET2"): plan="Chạm chốt lời"
        else: plan="Chưa chạm"

        row.update({
            "Hiện tại %":current,
            "Cao nhất %":high,
            "Thấp nhất %":low,
            "VN-Index cùng kỳ %":vni_ret,
            "Hơn VN-Index %":(current-vni_ret if math.isfinite(vni_ret) else float("nan")),
            "Kế hoạch bot":plan,
            "Trạng thái":("Đủ 20/20 phiên" if n>=HORIZON else f"Đang theo dõi ({n}/20 phiên)"),
        })
        details.append(row)

    detail=pd.DataFrame(details)
    base_cols=["Bot","Nhãn tại T0","Ngày tín hiệu (T0)","Mã","Giá tín hiệu","Vùng mua thấp","Vùng mua cao",
               "Target 1","Target 2","Cắt lỗ","Điểm","R:R"]+[f"T{i}" for i in range(0,HORIZON+1)]+[
               "Hiện tại %","Cao nhất %","Thấp nhất %","VN-Index cùng kỳ %","Hơn VN-Index %",
               "Kế hoạch bot","Trạng thái"]
    if detail.empty: detail=pd.DataFrame(columns=base_cols)
    else: detail=detail.reindex(columns=base_cols)

    # Management summary by bot.
    summary=[]
    for bot_full in bot_map.values():
        q=detail[detail["Bot"]==bot_full].copy()
        total=len(q)
        complete=int(q["Trạng thái"].astype(str).str.startswith("Đủ").sum()) if total else 0
        tracking=total-complete
        hit=int((q["Kế hoạch bot"]=="Chạm chốt lời").sum()) if total else 0
        stopn=int((q["Kế hoạch bot"]=="Dính cắt lỗ").sum()) if total else 0
        summary.append({
            "Bot":bot_full,"Tổng lệnh":total,"Đang theo dõi":tracking,"Đủ 20 phiên":complete,
            "Lãi/lỗ hiện tại TB %":q["Hiện tại %"].mean() if total else 0,
            "% lệnh đang lãi":q["Hiện tại %"].gt(0).mean()*100 if total else 0,
            "VNI cùng kỳ TB %":q["VN-Index cùng kỳ %"].mean() if total else float("nan"),
            "Hơn VNI TB %":q["Hơn VN-Index %"].mean() if total else float("nan"),
            "Chạm chốt lời":hit,"Dính cắt lỗ":stopn,"Chưa chạm":total-hit-stopn,
        })
    summary_df=pd.DataFrame(summary)

    # T0..T20 average performance table.
    perf=[]
    bots=list(bot_map.values())
    for t in range(0,HORIZON+1):
        rr={"Phiên":f"T{t}"}
        for idx,bot_full in enumerate(bots,1):
            q=detail[detail["Bot"]==bot_full].copy()
            vals=pd.to_numeric(q[f"T{t}"],errors="coerce").dropna() if not q.empty else pd.Series(dtype=float)
            # Alpha uses current benchmark only when available; for T tables leave blank if no aligned benchmark.
            rr[f"Bot {idx} số lệnh"]=len(vals)
            rr[f"Bot {idx} lãi TB %"]=vals.mean() if len(vals) else float("nan")
            rr[f"Bot {idx} % lệnh lãi"]=vals.gt(0).mean()*100 if len(vals) else float("nan")
        perf.append(rr)
    perf_df=pd.DataFrame(perf)

    # Write raw workbook first.
    with pd.ExcelWriter(out,engine="openpyxl") as xw:
        summary_df.to_excel(xw,index=False,sheet_name="Tổng hợp",startrow=4)
        perf_df.to_excel(xw,index=False,sheet_name="Tổng hợp",startrow=11)
        detail.to_excel(xw,index=False,sheet_name="Chi tiết từng mã")
        detail[detail["Bot"]==bots[0]].to_excel(xw,index=False,sheet_name="Chi tiết V2")
        detail[detail["Bot"]==bots[1]].to_excel(xw,index=False,sheet_name="Chi tiết V3.7")

    # Presentation formatting.
    wb=load_workbook(out)
    ws=wb["Tổng hợp"]
    ws.insert_rows(1,amount=0)
    ws["A1"]=f"BÁO CÁO FORWARD TEST - CHỨNG KHOÁN GPT – {datetime.now(VN).strftime('%H:%M %d/%m/%Y')}"
    ws["A2"]="Ghi nhận TOÀN BỘ danh sách bot công bố và đóng băng nhãn tại T0 (MUA/CHỜ...). T0 là ngày tín hiệu; phiên kế tiếp = T1; theo dõi đến T20."
    ws["A4"]="1. TỔNG QUAN"
    ws["A11"]="2. LÃI/LỖ TRUNG BÌNH THEO TỪNG PHIÊN SAU TÍN HIỆU"
    ws.merge_cells("A1:K1"); ws.merge_cells("A2:K2"); ws.merge_cells("A4:K4"); ws.merge_cells("A11:K11")
    ws["A1"].font=Font(bold=True,size=16)
    ws["A2"].font=Font(italic=True,size=10)
    ws["A4"].font=Font(bold=True,size=12); ws["A11"].font=Font(bold=True,size=12)

    thin=Side(style="thin",color="D9D9D9")
    header_fill=PatternFill("solid",fgColor="D9EAF7")
    section_fill=PatternFill("solid",fgColor="E2F0D9")
    for sname in wb.sheetnames:
        sh=wb[sname]
        sh.freeze_panes="A2" if sname!="Tổng hợp" else "A6"
        sh.auto_filter.ref=sh.dimensions if sname!="Tổng hợp" else f"A5:K7"
        for row in sh.iter_rows():
            for cell in row:
                cell.alignment=Alignment(vertical="center")
        if sname!="Tổng hợp":
            for cell in sh[1]:
                cell.font=Font(bold=True); cell.fill=header_fill; cell.border=Border(bottom=thin)
        for col in range(1,sh.max_column+1):
            letter=get_column_letter(col)
            maxlen=0
            for cell in sh[letter]:
                if cell.value is not None: maxlen=max(maxlen,len(str(cell.value)))
            sh.column_dimensions[letter].width=min(max(maxlen+2,10),28)

    for rownum in (5,12):
        for cell in ws[rownum]:
            cell.font=Font(bold=True); cell.fill=header_fill; cell.border=Border(bottom=thin)
    for rownum in (4,11):
        ws.cell(rownum,1).fill=section_fill

    # Percentage display for report percentage columns.
    for sname in ["Chi tiết từng mã","Chi tiết V2","Chi tiết V3.7"]:
        sh=wb[sname]
        headers={c.value:c.column for c in sh[1]}
        pct_headers=[f"T{i}" for i in range(0,HORIZON+1)]+["Hiện tại %","Cao nhất %","Thấp nhất %","VN-Index cùng kỳ %","Hơn VN-Index %"]
        for h in pct_headers:
            col=headers.get(h)
            if col:
                for r in range(2,sh.max_row+1):
                    sh.cell(r,col).number_format='0.00"%"'

    wb.save(out)
    return out

def upload_drive(path):
    url=(os.getenv("GDRIVE_WEBHOOK") or "").strip()
    if not url: raise RuntimeError("Thieu GDRIVE_WEBHOOK")
    payload=json.dumps({"name":path.name,"data":base64.b64encode(path.read_bytes()).decode("ascii")}).encode("utf-8")
    req=urllib.request.Request(url,data=payload,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=60,context=ssl.create_default_context(cafile=certifi.where())) as r:
        ans=r.read().decode("utf-8","replace").strip()
    if not ans.startswith("OK "): raise RuntimeError("Google Drive upload failed: "+ans)
    print(ans)
    return ans

def report():
    sig,res=evaluate()
    xlsx=build_excel(sig,res)
    upload_drive(xlsx)
    send(build_report(sig,res))

if __name__=="__main__":
    mode=(sys.argv[1] if len(sys.argv)>1 else "report").lower()
    if mode=="capture": capture()
    elif mode=="report": report()
    else: raise SystemExit("mode: capture | report")
