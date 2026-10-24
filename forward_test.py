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
FEE_RATE=0.004
BUY_LABEL="🟢 ĐỦ ĐIỀU KIỆN MUA"

SIGNAL_COLS=["signal_date","signal_time","session","bot","strategy_version","symbol","entry_price","buy_low","buy_high","stop","target1","target2","rr1","score","status"]
RESULT_COLS=["signal_date","session","bot","strategy_version","symbol","buy_date","buy_price","trade_day","t","close","return_pct","vni_return_pct","alpha_pct","high_since","low_since","mfe_pct","mae_pct","plan_event"]

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
    if "session" in d.columns:
        d["session"]=d["session"].fillna("6:00").replace("", "6:00")
    return d

def save_signals(new_rows):
    old=load_csv(SIGNALS,SIGNAL_COLS)
    add=pd.DataFrame(new_rows,columns=SIGNAL_COLS)
    d=pd.concat([old,add],ignore_index=True)
    if not d.empty:
        d["symbol"]=d["symbol"].astype(str).str.upper().str.strip()
        d=d.drop_duplicates(["signal_date","session","bot","strategy_version","symbol"],keep="last")
        d=d.sort_values(["signal_date","session","bot","symbol"])
    d.to_csv(SIGNALS,index=False,encoding="utf-8-sig")
    return len(add),len(d)

def capture(mode="morning"):
    now=datetime.now(VN); rows=[]
    mode=str(mode).lower().strip()
    if mode not in ("morning","midday"): raise ValueError("capture mode: morning | midday")
    session="6:00" if mode=="morning" else "11:35"

    import ai_decision_top5 as v2
    if mode=="morning":
        candidates,_=v2.build_decision()
        signal_date=now.date().isoformat()
        eod_file=ROOT/"TOP5_V2_TODAY.csv"
        if eod_file.exists():
            try:
                eod=pd.read_csv(eod_file)
                if not eod.empty and "date" in eod.columns:
                    signal_date=pd.to_datetime(eod["date"].iloc[0]).date().isoformat()
            except Exception as ex: print("EOD date fallback:",ex)
    else:
        import midday_decision_top5 as mid
        candidates,_=mid.build_midday_decision()
        signal_date=now.date().isoformat()

    for x in candidates:
        if not v2.is_eligible_candidate(x): continue
        action=str(x.get("action") or "").strip()
        if not action: continue
        rows.append({"signal_date":signal_date,"signal_time":now.strftime("%Y-%m-%d %H:%M:%S"),"session":session,
            "bot":"CỔ PHIẾU ĐẠT CHUẨN GPT","strategy_version":"V2_FROZEN_DECISION_2026-10-24",
            "symbol":x["symbol"],"entry_price":x["close"],"buy_low":x["buy_low"],"buy_high":x["buy_high"],
            "stop":x["stop"],"target1":x["target1"],"target2":x["target2"],"rr1":x["current_rr1"],
            "score":x["decision_score"],"status":action})

    import send_top5_advanced_v3_7_telegram as v3tg
    p=ROOT/"TOP5_ADVANCED_V3.csv"
    if p.exists():
        d=pd.read_csv(p)
        for _,x in d.iterrows():
            action=v3tg.action_label(x,False)
            rows.append({"signal_date":signal_date,"signal_time":now.strftime("%Y-%m-%d %H:%M:%S"),"session":session,
                "bot":"CỔ PHIẾU CHẤT LƯỢNG GPT","strategy_version":"V3_7_GATE_2026-10-24",
                "symbol":str(x.symbol).upper(),"entry_price":x.close,"buy_low":x.buy_low,"buy_high":x.buy_high,
                "stop":x.stop,"target1":x.target1,"target2":x.target2,"rr1":x.rr1,
                "score":x.advanced_score,"status":action})
    added,total=save_signals(rows)
    print(f"FORWARD CAPTURE {session}: {added} rows | ledger total {total}")

def fetch_history(symbol,start,end):
    d=kbs_client.lich_su_gia(symbol,start,end)
    if d is None or d.empty: return pd.DataFrame()
    d=d.copy()
    d["time"]=pd.to_datetime(d["time"],errors="coerce").dt.date
    return d.dropna(subset=["time","close"]).sort_values("time").drop_duplicates("time")

def is_buy_label(x):
    return str(x or "").strip()==BUY_LABEL

def _trade_key(s):
    return (str(s.signal_date),str(s.session),str(s.bot),str(s.symbol).upper())

def select_counted_buys(sig):
    buys=sig[sig["status"].map(is_buy_label)].copy()
    if buys.empty: return buys,{}
    buys["_dt"]=pd.to_datetime(buys["signal_date"],errors="coerce")
    buys["_so"]=buys["session"].map({"6:00":0,"11:35":1}).fillna(9)
    buys=buys.sort_values(["symbol","_dt","_so","signal_time"])
    keep=[]; repeats={}; today=date.today()
    for symbol,g in buys.groupby("symbol",sort=False):
        locked_until=None; active_idx=None
        for idx,s in g.iterrows():
            sd=pd.to_datetime(s.signal_date).date()
            if locked_until is not None and sd<=locked_until:
                repeats[active_idx]=repeats.get(active_idx,0)+1; continue
            h=fetch_history(symbol,sd.isoformat(),(today+timedelta(days=60)).isoformat())
            h=h[h["time"]>sd] if str(s.session)=="6:00" else h[h["time"]>=sd]
            locked_until=h.iloc[HORIZON]["time"] if len(h)>HORIZON else date.max
            active_idx=idx; repeats[idx]=0; keep.append(idx)
    return buys.loc[keep].drop(columns=["_dt","_so"]),repeats

def evaluate():
    sig=load_csv(SIGNALS,SIGNAL_COLS)
    if sig.empty:
        pd.DataFrame(columns=RESULT_COLS).to_csv(RESULTS,index=False,encoding="utf-8-sig")
        return sig,pd.DataFrame(columns=RESULT_COLS),{}
    trades,repeats=select_counted_buys(sig)
    today=date.today(); rows=[]
    start=(pd.to_datetime(sig["signal_date"]).min().date()-timedelta(days=10)).isoformat()
    vni=fetch_history("VNINDEX",start,today.isoformat())
    for _,s in trades.iterrows():
        sd=pd.to_datetime(s.signal_date).date(); session=str(s.session); symbol=str(s.symbol).upper()
        h=fetch_history(symbol,sd.isoformat(),today.isoformat())
        h=h[h["time"]>sd].copy() if session=="6:00" else h[h["time"]>=sd].copy()
        h=h.head(HORIZON+1)
        if h.empty: continue
        buy_date=h.iloc[0]["time"] if session=="6:00" else sd
        buy_price=fnum(h.iloc[0]["open"]) if session=="6:00" else fnum(s.entry_price)
        if not math.isfinite(buy_price) or buy_price<=0: continue
        v0=vni[vni["time"]==buy_date] if not vni.empty else pd.DataFrame()
        vopen=fnum(v0.iloc[0]["open"]) if not v0.empty else float("nan")
        stop=fnum(s.stop); t1=fnum(s.target1); hi=-float("inf"); lo=float("inf"); event=""
        for t,r in enumerate(h.itertuples(index=False)):
            close=fnum(r.close); hi=max(hi,fnum(r.high,-float("inf"))); lo=min(lo,fnum(r.low,float("inf")))
            ret=(close/buy_price-1-FEE_RATE)*100
            vr=float("nan")
            if math.isfinite(vopen) and vopen>0:
                vv=vni[vni["time"]==r.time]
                if not vv.empty: vr=(fnum(vv.iloc[0]["close"])/vopen-1)*100
            if t>=2 and not event:
                if math.isfinite(t1) and fnum(r.high,-float("inf"))>=t1: event=f"Chạm chốt lời (T{t})"
                elif math.isfinite(stop) and close<stop: event=f"Dính cắt lỗ (T{t})"
            rows.append({"signal_date":s.signal_date,"session":session,"bot":s.bot,"strategy_version":s.strategy_version,
                "symbol":symbol,"buy_date":buy_date.isoformat(),"buy_price":buy_price,"trade_day":r.time.isoformat(),"t":t,
                "close":close,"return_pct":ret,"vni_return_pct":vr,"alpha_pct":ret-vr if math.isfinite(vr) else float("nan"),
                "high_since":hi,"low_since":lo,"mfe_pct":(hi/buy_price-1-FEE_RATE)*100,
                "mae_pct":(lo/buy_price-1-FEE_RATE)*100,"plan_event":event})
    res=pd.DataFrame(rows,columns=RESULT_COLS)
    if not res.empty: res=res.sort_values(["signal_date","session","bot","symbol","t"])
    res.to_csv(RESULTS,index=False,encoding="utf-8-sig")
    return sig,res,repeats

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

def build_excel(sig,res,repeats=None):
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

    # Keep every published label visible. Only exact BUY_LABEL enters performance statistics.
    details=[]
    today=date.today()
    vni_cache={}
    for _,s in sig.iterrows():
        sd=pd.to_datetime(s["signal_date"]).date()
        symbol=str(s["symbol"]).upper().strip()
        entry=fnum(s["entry_price"])
        q=res[(res["signal_date"].astype(str)==str(s["signal_date"])) &
              (res["session"].astype(str)==str(s.get("session") or "6:00")) &
              (res["bot"].astype(str)==str(s["bot"])) &
              (res["symbol"].astype(str).str.upper()==symbol)].copy() if not res.empty else pd.DataFrame()

        row={
            "Bot":bot_map.get(str(s["bot"]),str(s["bot"])),
            "Nhãn tại T0":str(s.get("status") or ""),
            "Lượt":str(s.get("session") or "6:00"),
            "Ngày tín hiệu":sd,
            "Ngày mua (T0)":None,
            "Mã":symbol,
            "Giá tín hiệu":entry,
            "Giá mua":float("nan"),
            "Vùng mua thấp":fnum(s.get("buy_low")),
            "Vùng mua cao":fnum(s.get("buy_high")),
            "Target 1":fnum(s.get("target1")),
            "Target 2":fnum(s.get("target2")),
            "Cắt lỗ":fnum(s.get("stop")),
            "Điểm":fnum(s.get("score")),
            "R:R":fnum(s.get("rr1")),
            "T0":float("nan"),
        }
        for t in range(1,HORIZON+1): row[f"T{t}"]=float("nan")

        if not q.empty:
            q=q.sort_values("t")
            for _,r in q.iterrows():
                t=int(r["t"])
                if 0<=t<=HORIZON: row[f"T{t}"]=fnum(r["return_pct"])
            first=q.iloc[0]
            row["Ngày mua (T0)"]=pd.to_datetime(first["buy_date"]).date()
            row["Giá mua"]=fnum(first["buy_price"])
            last=q.iloc[-1]
            current=fnum(last["return_pct"])
            high=fnum(last["mfe_pct"])
            low=fnum(last["mae_pct"])
            n=int(last["t"])
            first_event=str(last.get("plan_event") or "").strip()
        else:
            current=float("nan"); high=float("nan"); low=float("nan"); n=-1; first_event=""

        # Benchmark is already aligned per trade/Tk by evaluate().
        vni_ret=fnum(last.get("vni_return_pct")) if not q.empty else float("nan")
        alpha=fnum(last.get("alpha_pct")) if not q.empty else float("nan")
        plan=first_event if first_event else ("Chưa chạm" if not q.empty else ("Không tính - THEO DÕI/CHỜ" if not is_buy_label(s.get("status")) else "Chưa có T0"))

        row.update({
            "Hiện tại %":current,
            "Cao nhất %":high,
            "Thấp nhất %":low,
            "VN-Index cùng kỳ %":vni_ret,
            "Hơn VN-Index %":alpha,
            "Kế hoạch bot":plan,
            "Trạng thái":("Đã đủ 20 phiên" if n>=HORIZON else (f"Đang theo dõi ({max(n,0)}/20 phiên)" if not q.empty else ("THEO DÕI / CHỜ" if not is_buy_label(s.get("status")) else "Chờ phiên T0"))),
            "Số lần KN lại":0,
        })
        details.append(row)

    detail=pd.DataFrame(details)
    base_cols=["Bot","Lượt","Nhãn tại T0","Ngày tín hiệu","Ngày mua (T0)","Mã","Giá tín hiệu","Giá mua","Vùng mua thấp","Vùng mua cao",
               "Target 1","Target 2","Cắt lỗ","Điểm","R:R"]+[f"T{i}" for i in range(0,HORIZON+1)]+[
               "Hiện tại %","Cao nhất %","Thấp nhất %","VN-Index cùng kỳ %","Hơn VN-Index %",
               "Kế hoạch bot","Trạng thái","Số lần KN lại"]
    if detail.empty: detail=pd.DataFrame(columns=base_cols)
    else: detail=detail.reindex(columns=base_cols)

    # Management summary by bot + action label.
    summary=[]
    for bot_full in bot_map.values():
        buy_all=detail[(detail["Bot"]==bot_full) & (detail["Nhãn tại T0"]==BUY_LABEL)].copy()
        q=buy_all[buy_all["Giá mua"].notna()].copy()
        total=len(buy_all)
        entered=len(q)
        complete=int(q["Trạng thái"].astype(str).str.startswith("Đủ").sum()) if entered else 0
        tracking=entered-complete
        hit=int((q["Kế hoạch bot"]=="Chạm chốt lời").sum()) if entered else 0
        stopn=int((q["Kế hoạch bot"]=="Dính cắt lỗ").sum()) if entered else 0
        summary.append({
            "Bot":bot_full,"Tổng lệnh MUA":total,"Đang theo dõi":tracking,"Đủ 20 phiên":complete,
            "Lãi/lỗ hiện tại TB %":q["Hiện tại %"].mean() if entered else 0,
            "% mã đang lãi":q["Hiện tại %"].gt(0).mean()*100 if entered else 0,
            "VNI cùng kỳ TB %":q["VN-Index cùng kỳ %"].mean() if entered else float("nan"),
            "Hơn VNI TB %":q["Hơn VN-Index %"].mean() if entered else float("nan"),
            "Chạm chốt lời":hit,"Dính cắt lỗ":stopn,"Chưa chạm":entered-hit-stopn,
        })
    summary_df=pd.DataFrame(summary)

    # Separate BUY vs WAIT so Decision Gate can be audited without mixing populations.
    label_rows=[]
    for bot_full in bot_map.values():
        q=detail[detail["Bot"]==bot_full].copy()
        for label_group,mask in [
            ("ĐỦ ĐIỀU KIỆN MUA",q["Nhãn tại T0"].astype(str).eq(BUY_LABEL)),
            ("THEO DÕI / CHỜ",~q["Nhãn tại T0"].astype(str).eq(BUY_LABEL)),
        ]:
            z=q[mask].copy()
            label_rows.append({
                "Bot":bot_full,"Nhóm nhãn T0":label_group,"Số mã":len(z),
                "Lãi/lỗ hiện tại TB %":z["Hiện tại %"].mean() if len(z) else float("nan"),
                "% mã đang lãi":z["Hiện tại %"].gt(0).mean()*100 if len(z) else float("nan"),
                "Hơn VNI TB %":z["Hơn VN-Index %"].mean() if len(z) else float("nan"),
            })
    label_df=pd.DataFrame(label_rows)

    # T0..T20 average performance table, directly comparable V2 vs V3.7.
    perf=[]
    bots=list(bot_map.values())
    for t in range(0,HORIZON+1):
        rr={"Phiên":f"T{t}"}
        for name,bot_full in [("V2",bots[0]),("V3.7",bots[1])]:
            q=detail[(detail["Bot"]==bot_full) & (detail["Nhãn tại T0"]==BUY_LABEL) & detail["Giá mua"].notna()].copy()
            vals=pd.to_numeric(q[f"T{t}"],errors="coerce").dropna() if not q.empty else pd.Series(dtype=float)
            rr[f"{name} số mã"]=len(vals)
            rr[f"{name} lãi TB %"]=vals.mean() if len(vals) else float("nan")
            rr[f"{name} % mã lãi"]=vals.gt(0).mean()*100 if len(vals) else float("nan")
            al=[]
            for _,dr in q.iterrows():
                rq=res[(res["signal_date"].astype(str)==str(dr["Ngày tín hiệu"])) & (res["session"].astype(str)==str(dr["Lượt"])) & (res["symbol"].astype(str).str.upper()==str(dr["Mã"]).upper()) & (res["t"]==t)]
                if not rq.empty and math.isfinite(fnum(rq.iloc[0]["alpha_pct"])): al.append(fnum(rq.iloc[0]["alpha_pct"]))
            rr[f"{name} hơn VNI %"]=sum(al)/len(al) if al else float("nan")
        perf.append(rr)
    perf_df=pd.DataFrame(perf)

    # Write workbook with fixed, non-overlapping summary ranges.
    with pd.ExcelWriter(out,engine="openpyxl") as xw:
        summary_df.to_excel(xw,index=False,sheet_name="Tổng hợp",startrow=4)
        label_df.to_excel(xw,index=False,sheet_name="Tổng hợp",startrow=10)
        perf_df.to_excel(xw,index=False,sheet_name="Tổng hợp",startrow=17)
        detail.to_excel(xw,index=False,sheet_name="Chi tiết từng mã")
        detail[detail["Bot"]==bots[0]].to_excel(xw,index=False,sheet_name="Chi tiết V2")
        detail[detail["Bot"]==bots[1]].to_excel(xw,index=False,sheet_name="Chi tiết V3.7")

    # Presentation formatting.
    wb=load_workbook(out)
    ws=wb["Tổng hợp"]
    ws.insert_rows(1,amount=0)

    # Re-write summary tables cell-by-cell after pandas export.
    # This avoids Google Sheets XLSX import losing DataFrame text while retaining filters/styles.
    def write_table_direct(ws, df, header_row, start_col=1):
        for j,colname in enumerate(df.columns,start_col):
            cell=ws.cell(header_row,j,value=str(colname))
            cell.font=Font(bold=True,color="000000")
        for i,record in enumerate(df.itertuples(index=False,name=None),header_row+1):
            for j,val in enumerate(record,start_col):
                if pd.isna(val):
                    val=None
                cell=ws.cell(i,j,value=val)
                cell.font=Font(color="000000")

    write_table_direct(ws,summary_df,5)
    write_table_direct(ws,label_df,11)
    write_table_direct(ws,perf_df,18)
    ws["A1"]=f"BÁO CÁO FORWARD TEST - CHỨNG KHOÁN GPT – {datetime.now(VN).strftime('%H:%M %d/%m/%Y')}"
    ws["A2"]="6:00: mua Open phiên kế tiếp = T0. 11:35: mua giá bot báo = T0. P/L T0-T20 = Close/Giá mua - 1 - 0,4% phí. Tổng quan chỉ tính đúng nhãn 🟢 ĐỦ ĐIỀU KIỆN MUA."
    # Never merge rows that contain DataFrame headers/data.
    # Section titles sit one row ABOVE each table.
    ws["A4"]="1. TỔNG QUAN - CHỈ LỆNH MUA"
    ws["A10"]="2. HIỆU QUẢ THEO NHÃN TẠI T0"
    ws["A17"]="3. T0-T20 - CHỈ LỆNH MUA, KÈM ALPHA VNINDEX"
    ws.merge_cells("A1:K1"); ws.merge_cells("A2:K2")
    ws["A1"].font=Font(bold=True,size=16)
    ws["A2"].font=Font(italic=True,size=10)
    for rr in (4,10,17):
        ws.cell(rr,1).font=Font(bold=True,size=12)

    thin=Side(style="thin",color="D9D9D9")
    header_fill=PatternFill("solid",fgColor="D9EAF7")
    section_fill=PatternFill("solid",fgColor="E2F0D9")
    for sname in wb.sheetnames:
        sh=wb[sname]
        sh.freeze_panes="G2" if sname!="Tổng hợp" else "A6"
        sh.auto_filter.ref=sh.dimensions if sname!="Tổng hợp" else None
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

    for rownum in (5,11,18):
        for cell in ws[rownum]:
            cell.font=Font(bold=True); cell.fill=header_fill; cell.border=Border(bottom=thin)
    for rownum in (4,10,17):
        ws.cell(rownum,1).fill=section_fill

    # Date display in detail sheets.
    for sname in ["Chi tiết từng mã","Chi tiết V2","Chi tiết V3.7"]:
        sh=wb[sname]
        headers={c.value:c.column for c in sh[1]}
        for h in ["Ngày tín hiệu","Ngày mua (T0)"]:
            col=headers.get(h)
            if col:
                for r in range(2,sh.max_row+1):
                    cell=sh.cell(r,col)
                    if isinstance(cell.value,str) and cell.value:
                        try:
                            cell.value=pd.to_datetime(cell.value).to_pydatetime()
                        except Exception:
                            pass
                    cell.number_format="dd/mm/yyyy"

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

def verify_excel(path, expected_sig):
    """Fail closed: never upload a visually empty/invalid Forward Test workbook."""
    from openpyxl import load_workbook
    wb=load_workbook(path,data_only=True)
    ws=wb["Tổng hợp"]
    # Summary counts only BUY trades that already have a valid T0/buy price.
    # Signals waiting for the next tradable T0 remain in Detail and must not inflate Summary.
    detail_ws=wb["Chi tiết từng mã"]
    headers={c.value:c.column for c in detail_ws[1]}
    bot_col=headers["Bot"]; label_col=headers["Nhãn tại T0"]; buy_col=headers["Giá mua"]
    expected={
        "V2 - CỔ PHIẾU ĐẠT CHUẨN GPT":0,
        "V3.7 - CỔ PHIẾU CHẤT LƯỢNG GPT":0,
    }
    bot_map={
        "CỔ PHIẾU ĐẠT CHUẨN GPT":"V2 - CỔ PHIẾU ĐẠT CHUẨN GPT",
        "CỔ PHIẾU CHẤT LƯỢNG GPT":"V3.7 - CỔ PHIẾU CHẤT LƯỢNG GPT",
    }
    for r in range(2,detail_ws.max_row+1):
        bot=detail_ws.cell(r,bot_col).value
        label=detail_ws.cell(r,label_col).value
        if bot in bot_map and label==BUY_LABEL:
            expected[bot_map[bot]]+=1
    actual={}
    for r in range(6,8):
        name=ws.cell(r,1).value
        count=ws.cell(r,2).value
        if name is not None:
            actual[str(name)]=int(count or 0)
    if actual != expected:
        raise RuntimeError(f"Forward Test XLSX self-check FAILED: expected={expected}, actual={actual}")
    if ws["A5"].value!="Bot" or ws["B5"].value!="Tổng lệnh MUA":
        raise RuntimeError("Forward Test XLSX self-check FAILED: summary header missing")
    print(f"XLSX SELF-CHECK OK: corrected T0/fee/VNI/BUY-only structure {actual}")
    return actual

def upload_drive(path):
    url=(os.getenv("GDRIVE_WEBHOOK") or "").strip()
    if not url: raise RuntimeError("Thieu GDRIVE_WEBHOOK")
    payload=json.dumps({"name":path.name,"data":base64.b64encode(path.read_bytes()).decode("ascii")}).encode("utf-8")
    req=urllib.request.Request(url,data=payload,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=120,context=ssl.create_default_context(cafile=certifi.where())) as r:
        ans=r.read().decode("utf-8","replace").strip()
    if not ans.startswith("OK "): raise RuntimeError("Google Drive upload failed: "+ans)
    print(ans)
    return ans

def report():
    sig,res,repeats=evaluate()
    xlsx=build_excel(sig,res,repeats)
    verify_excel(xlsx,sig)
    upload_drive(xlsx)
    send(build_report(sig,res))

if __name__=="__main__":
    mode=(sys.argv[1] if len(sys.argv)>1 else "report").lower()
    if mode=="capture": capture(sys.argv[2] if len(sys.argv)>2 else "morning")
    elif mode=="report": report()
    else: raise SystemExit("mode: capture | report")
