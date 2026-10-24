# -*- coding: utf-8 -*-
"""ISOLATED 6M methodology backtest. READS production assets; NEVER writes production bot files. Runner cache may be refreshed ephemerally."""
from pathlib import Path
import math, sqlite3, re
import numpy as np, pandas as pd, joblib
import send_top5_telegram_pro as pro
import ai_decision_top5 as dec
from send_top5_advanced_v3_7_telegram import action_label

ROOT=Path(__file__).resolve().parent
START=pd.Timestamp("2026-04-24"); END=pd.Timestamp("2026-10-23")
FEE=.004  # locked total round-trip cost
FEATURES=[
"RET1","RET5","RET10","RET20","RET60","DIST_MA20","DIST_MA50","MA20_SLOPE5","MA50_SLOPE10","RSI14","VOL_RATIO","VOLATILITY20","RANGE_POS20","RANGE_POS60",
"MKT_RET1","MKT_RET5","MKT_RET20","MKT_RET60","MKT_VOL20","BREADTH1","BREADTH5","BREADTH20","MKT_RET20_MA20","MKT_BREADTH20_MA20","MKT_VOL20_MA20","MKT_TREND_SCORE",
"REL_RET5","REL_RET10","REL_RET20","REL_RET60","CS_RET5_RANK","CS_RET20_RANK","CS_RET60_RANK","CS_VOLATILITY_RANK"]

def n(x):
    try:
        v=float(x); return v if math.isfinite(v) else np.nan
    except:return np.nan
def pct(x):
    x=n(x); return x*100 if pd.notna(x) and abs(x)<=1.5 else x
def hi(v,b):
    if pd.isna(v):return np.nan
    for t,s in b:
        if v>=t:return s
    return 0
def lo(v,b):
    if pd.isna(v):return np.nan
    for t,s in b:
        if v<=t:return s
    return 0
def pk(x):
    s=re.sub(r"_\d+$","",str(x).replace("-Năm","").replace("-Nam",""))
    m=re.search(r"(\d{4})-Q([1-4])",s)
    if m:return int(m.group(1))*10+int(m.group(2))
    m=re.search(r"(\d{4})",s); return int(m.group(1))*10 if m else -1
def fallback_available(period):
    k=pk(period)
    if k<0:return pd.NaT
    y,q=divmod(k,10)
    if q in (1,2,3,4):
        end=pd.Timestamp(y, q*3, 1)+pd.offsets.MonthEnd(0)
        return end+pd.Timedelta(days=45)
    return pd.Timestamp(y,12,31)+pd.Timedelta(days=90)

def load_raw():
    out={}
    for p in (ROOT/"stock_cache").glob("*.csv"):
        try:g=pd.read_csv(p)
        except:continue
        if "time" in g:g=g.rename(columns={"time":"date"})
        if "date" not in g:continue
        g["date"]=pd.to_datetime(g.date,errors="coerce").dt.normalize()
        for c in ["open","high","low","close","volume"]:
            g[c]=pd.to_numeric(g.get(c),errors="coerce")
        g=g.dropna(subset=["date","open","high","low","close"]).sort_values("date").drop_duplicates("date")
        if len(g)>=60:out[p.stem.upper()]=g.reset_index(drop=True)
    return out

def financial_raw():
    con=sqlite3.connect(ROOT/"financial_v3.sqlite")
    tabs=pd.read_sql("select name from sqlite_master where type='table'",con).name.tolist()
    tab="financial_raw" if "financial_raw" in tabs else "raw_financial"
    d=pd.read_sql(f"select * from {tab}",con); con.close()
    d["symbol"]=d.symbol.astype(str).str.upper()
    # Prefer a genuine publication/announcement date if provider stored one.
    pub=next((c for c in ["published_at","publication_date","publish_date","announcement_date","released_at"] if c in d.columns),None)
    fb=d["period"].map(fallback_available)
    if pub:
        actual=pd.to_datetime(d[pub],errors="coerce").dt.normalize()
        d["_available"]=actual.fillna(fb)
        source="actual publication date where present; +45d quarter fallback"
    else:
        d["_available"]=fb
        source="+45d after quarter end fallback (no publication-date field in DB)"
    d["_k"]=d.period.map(pk); d["_v"]=pd.to_numeric(d.value,errors="coerce")
    return d,source

def latest(g,ids,asof,quarter=None):
    z=g[(g.item_id.astype(str).isin(ids))&(g._available<=asof)&g._v.notna()].copy()
    if quarter is not None and "period_type" in z:
        p=z.period_type.astype(str).str.lower(); q=z[p.str.startswith("q")] if quarter else z[~p.str.startswith("q")]
        if len(q):z=q
    if z.empty:return np.nan
    z=z.sort_values(["_k","_available"]).drop_duplicates(["item_id","_k"],keep="last")
    return z.iloc[-1]._v
def fourq(g,ids,asof):
    z=g[(g.item_id.astype(str).isin(ids))&(g._available<=asof)&g._v.notna()].copy()
    if "period_type" in z:
        q=z[z.period_type.astype(str).str.lower().str.startswith("q")]
        if len(q):z=q
    if z.empty:return np.nan
    z=z.sort_values(["_k","_available"]).drop_duplicates("_k",keep="last").tail(4)
    if len(z)<4:return np.nan
    ks=z._k.tolist()
    def nxt(k):
        y,q=divmod(int(k),10); return (y+1)*10+1 if q==4 else y*10+q+1
    if any(nxt(ks[i])!=ks[i+1] for i in range(3)):return np.nan
    return float(z._v.sum())
def ctype(g):
    ids=set(g.item_id.astype(str))
    if {"net_interest_income","interest_and_similar_income"}&ids:return "NH"
    if {"revenue_in_brokerage_services","financial_assets_at_fair_value_through_profit_or_loss_fvtpl"}&ids:return "CK"
    if {"gross_written_premium","claim_and_maturity_payment_expenses"}&ids:return "BH"
    return "CT"
def finance_score(g,asof,price):
    typ=ctype(g)
    roe=pct(latest(g,["roe","roe_trailling"],asof)); roa=pct(latest(g,["roa","roa_trailling"],asof))
    mar=pct(latest(g,["net_margin"],asof)); debt=pct(latest(g,["debt_to_assets","liabilities_to_assets"],asof))
    nim=pct(latest(g,["net_interest_margin_nim","net_interest_margin"],asof)); npl=pct(latest(g,["npl","non_performing_loan_ratio"],asof))
    cap=latest(g,["charter_capital"],asof); eq=latest(g,["owners_equity","total_equity"],asof)
    eps=fourq(g,["eps_basic_vnd","earnings_per_share_vnd"],asof)
    if pd.isna(eps) or eps<=0:
        pat=fourq(g,["attributable_to_parent_company","profit_after_tax_for_shareholders_of_the_parent_company","net_profit_attributable_to_shareholders_of_the_group","net_profit_loss_after_tax","profit_after_tax"],asof)
        eps=pat*10000/cap if pd.notna(pat) and pd.notna(cap) and cap>0 else np.nan
    if pd.isna(eps) or eps<=0:eps=latest(g,["trailing_eps"],asof,None)
    if pd.isna(eps) or eps<=0:eps=latest(g,["eps_basic_vnd","earnings_per_share_vnd"],asof,False)
    bv=latest(g,["book_value_per_share_bvps"],asof)
    if pd.isna(bv) and pd.notna(eq) and pd.notna(cap) and cap>0:bv=eq/cap*10000
    pv=price*1000 if price<1000 else price
    pe=pv/eps if pd.notna(eps) and eps>0 else np.nan; pb=pv/bv if pd.notna(bv) and bv>0 else np.nan
    if pd.notna(pe) and not .5<=pe<=200:pe=np.nan
    if pd.notna(pb) and not .05<=pb<=30:pb=np.nan
    a=[hi(roe,[(20,100),(15,90),(10,75),(5,55),(0,35),(-999,10)]),hi(roa,[(8,100),(5,90),(3,75),(1,55),(0,35),(-999,10)])]
    if typ=="NH":a += [hi(nim,[(4,100),(3,85),(2,65),(1,40),(-999,20)]),lo(npl,[(1,100),(2,80),(3,55),(5,30),(999,10)])]
    else:a += [hi(mar,[(20,100),(12,85),(7,70),(3,50),(0,35),(-999,10)]),lo(debt,[(30,100),(45,85),(60,65),(75,40),(999,20)])]
    a += [lo(pe,[(8,100),(12,90),(18,75),(25,55),(40,35),(200,15)]),lo(pb,[(1,100),(1.5,90),(2.5,75),(4,55),(7,35),(30,15)])]
    good=[z for z in a if pd.notna(z)]; cov=len(good)/len(a)*100
    raw=np.mean(good) if good else np.nan; pen=1 if cov>=80 else (.88 if cov>=66 else (.72 if cov>=50 else .55))
    return (raw*pen if pd.notna(raw) else np.nan,cov,pe,pb,typ)

def technical_panel(raw):
    frames=[]
    for s,g in raw.items():
        d=g.copy(); c=d.close; h=d.high; l=d.low; v=d.volume
        ma20=c.rolling(20).mean(); ma50=c.rolling(50).mean(); ma200=c.rolling(200).mean()
        delta=c.diff(); gain=delta.clip(lower=0).rolling(14).mean(); loss=(-delta.clip(upper=0)).rolling(14).mean()
        rsi=100-100/(1+gain/loss.replace(0,np.nan)); rsi=rsi.where(loss!=0,100)
        av=v.rolling(20).mean(); mf=(((c-l)-(h-c))/(h-l).replace(0,np.nan)*v).rolling(20).sum()/v.rolling(20).sum()
        d["trend_score"]=(np.where(c>ma20,25,5)+np.where(c>ma50,25,5)+np.where(ma200.notna()&(c>ma200),25,10)+np.where(ma20>ma50,25,5))
        d["momentum_score"]=rsi.clip(0,100).fillna(50); d["money_flow_score"]=(50+mf.fillna(0)*100).clip(0,100)
        d["liquidity_score"]=(v/av.replace(0,np.nan)*50).clip(0,100).fillna(50)
        d["ret20"]=c.pct_change(20); d["ret60"]=c.pct_change(60); d["avg_volume20"]=av
        d["support"]=c.rolling(20).min(); d["resistance"]=c.rolling(60).max(); d["symbol"]=s
        frames.append(d[["date","symbol","close","trend_score","momentum_score","money_flow_score","liquidity_score","ret20","ret60","avg_volume20","support","resistance"]])
    x=pd.concat(frames,ignore_index=True)
    x["rs_score"]=x.groupby("date").ret20.rank(pct=True)*40+x.groupby("date").ret60.rank(pct=True)*60
    risk=np.where((x.trend_score<65)|(x.momentum_score<45),.055,np.where((x.trend_score>=80)&(x.rs_score>=65),.04,.045))
    x["stop"]=np.maximum(np.minimum(x.support*.99,x.close*(1-risk)),x.close*.93); x["risk"]=x.close-x.stop
    x["target1"]=x.resistance; x["rr1"]=(x.target1-x.close)/x.risk.replace(0,np.nan)
    x["target2"]=np.maximum(x.target1,x.close+2*x.risk); x["buy_high"]=x.close; x["buy_low"]=np.maximum(x.support,x.close*.985)
    x["entry_score"]=np.select([x.rr1>=2,x.rr1>=1.5,x.rr1>=1.2,x.rr1>=1],[100,90,78,65],default=20)
    x["avg_value20_bn"]=x.close*x.avg_volume20/1_000_000
    return x[(x.date>=START)&(x.date<=END)].copy()

def exits(tr,raw):
    rows=[]
    active={}
    for z in sorted(tr,key=lambda x:(x["signal_date"],x["model"],x["symbol"])):
        s=z["symbol"]; g=raw.get(s)
        if g is None:continue
        fut=g[g.date>z["signal_date"]].reset_index(drop=True)
        if fut.empty:continue
        e=fut.iloc[0]; ed=e.date
        if active.get((z["model"],s),pd.Timestamp.min)>=ed:continue
        buy=float(e.open); rec=dict(z,entry_date=ed,entry=buy)
        for k in [5,10,20]:
            rec[f"ret{k}"]=(float(fut.iloc[k-1].close)/buy-1-FEE)*100 if len(fut)>=k else np.nan
        horizon=fut.head(20); rec["mae"]=(horizon.low.min()/buy-1)*100 if len(horizon) else np.nan
        rec["mfe"]=(horizon.high.max()/buy-1)*100 if len(horizon) else np.nan
        reason="TIME20"; xp=float(horizon.iloc[-1].close) if len(horizon) else buy; xd=horizon.iloc[-1].date if len(horizon) else ed
        for i,b in horizon.iterrows():
            t=i+1
            if t<2:continue
            if float(b.close)<z["stop"]:
                if i+1<len(fut): xp=float(fut.iloc[i+1].open); xd=fut.iloc[i+1].date
                else: xp=float(b.close); xd=b.date
                reason=f"STOP_T{t}"; break
            if float(b.high)>=z["target1"]:
                xp=z["target1"]; xd=b.date; reason=f"T1_T{t}"; break
        rec["exit_reason"]=reason; rec["exit_date"]=xd; rec["planned_ret"]=(xp/buy-1-FEE)*100
        rec["stop_hit"]=reason.startswith("STOP"); rec["t1_hit"]=reason.startswith("T1")
        active[(z["model"],s)]=fut.iloc[min(19,len(fut)-1)].date
        rows.append(rec)
    return pd.DataFrame(rows)

def main():
    raw=load_raw(); scan=pd.read_csv(ROOT/"scanner_dataset_v2.csv"); scan["date"]=pd.to_datetime(scan.date).dt.normalize()
    obj=joblib.load(ROOT/"TOP5_V2_FROZEN_MODEL.joblib"); model=obj["model"]
    scan["SCORE"]=model.predict(scan[FEATURES])
    scan=pro.add_price_structure(scan)
    test=scan[(scan.date>=START)&(scan.date<=END)&(scan.VALUE20>=3_000_000)].copy()
    trades=[]
    # V2 exact daily replay
    for day,g in test.groupby("date"):
        pool=g.sort_values(["SCORE","symbol"],ascending=[False,True]).head(30)
        records,ranks=pro.prepare_analyses(pool)
        for i,(row,a) in enumerate(records):
            c=dec.evaluate_candidate(i+1,ranks[i],row,a)
            if c.get("action")=="🟢 ĐỦ ĐIỀU KIỆN MUA":
                trades.append(dict(model="V2",signal_date=day,symbol=c["symbol"],stop=float(c["stop"]),target1=float(c["target1"])))
    # V3.7 point-in-time
    fin,fin_source=financial_raw(); fg={s:g for s,g in fin.groupby("symbol")}
    tech=technical_panel(raw)
    fcache={}
    for day,g in tech.groupby("date"):
        cand=[]
        for r in g.itertuples(index=False):
            # Cheap technical gates first; finance PIT is the expensive step.
            if not (r.trend_score>=65 and r.rs_score>=55 and r.money_flow_score>=45 and r.avg_value20_bn>=3 and r.rr1>=1.20):continue
            if r.symbol not in fg:continue
            key=(r.symbol,day)
            fs,cov,pe,pb,typ=fcache.setdefault(key,finance_score(fg[r.symbol],day,float(r.close)))
            if not (pd.notna(fs) and fs>=60 and cov>=66):continue
            adv=(fs*.30+r.trend_score*.16+r.rs_score*.15+r.money_flow_score*.12+r.momentum_score*.08+r.liquidity_score*.07+r.entry_score*.12)*np.clip(cov/80,.85,1)
            x={"close":r.close,"buy_low":r.buy_low,"buy_high":r.buy_high,"stop":r.stop,"target1":r.target1,"rr1":r.rr1,
               "momentum_score":r.momentum_score,"money_flow_score":r.money_flow_score,"liquidity_score":r.liquidity_score}
            if action_label(x,False)=="🟢 ĐỦ ĐIỀU KIỆN MUA":
                cand.append((adv,r))
        for adv,r in cand:
            trades.append(dict(model="V3.7",signal_date=day,symbol=r.symbol,stop=float(r.stop),target1=float(r.target1)))
    out=exits(trades,raw)
    summaries=[]
    for modelname,g in out.groupby("model"):
        row={"model":modelname,"trades":len(g)}
        for k in [5,10,20]:
            x=g[f"ret{k}"].dropna(); row[f"avg_ret{k}"]=x.mean(); row[f"median_ret{k}"]=x.median(); row[f"win{k}"]=(x>0).mean()*100; row[f"n{k}"]=len(x)
        row.update(planned_avg=g.planned_ret.mean(),planned_median=g.planned_ret.median(),planned_win=(g.planned_ret>0).mean()*100,
                   t1_hit=g.t1_hit.mean()*100,stop_hit=g.stop_hit.mean()*100,mae=g.mae.mean(),mfe=g.mfe.mean())
        summaries.append(row)
    pd.DataFrame(summaries).to_csv(ROOT/"BACKTEST_6M_SUMMARY.csv",index=False,encoding="utf-8-sig")
    out.to_csv(ROOT/"BACKTEST_6M_TRADES.csv",index=False,encoding="utf-8-sig")
    audit=f"""ISOLATED 6M BACKTEST\nPeriod: {START.date()} -> {END.date()}\nProduction bot files modified by this engine: NO\nEntry: next trading session Open (EOD replay).\nFee: 0.4%.\nExit event from T2: Close < Stop checked BEFORE High >= T1. Stop executes next session Open; T1 executes at T1.\nDedup: same model+symbol blocked for 20 sessions after accepted signal.\nFinance point-in-time: {fin_source}.\nImportant limitation: historical 11:35 intraday prices are not present in repository, therefore this methodology run replays EOD/morning signals only; it does not fabricate midday prices.\nRecent signals without enough future bars remain NaN for T5/T10/T20 rather than being fabricated.\n"""
    (ROOT/"BACKTEST_6M_AUDIT.txt").write_text(audit,encoding="utf-8")
    print(pd.DataFrame(summaries).to_string(index=False)); print(audit)

if __name__=="__main__":main()
