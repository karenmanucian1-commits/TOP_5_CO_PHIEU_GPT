# -*- coding: utf-8 -*-
"""Statistical benchmark audit only. No production bot changes."""
from pathlib import Path
import json, urllib.request, numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parent; FEE=.004

def load_raw():
    out={}
    for p in (ROOT/"stock_cache").glob("*.csv"):
        try:g=pd.read_csv(p)
        except:continue
        if "time" in g:g=g.rename(columns={"time":"date"})
        if "date" not in g:continue
        g["date"]=pd.to_datetime(g.date,errors="coerce").dt.normalize()
        for c in ["open","close","volume"]:g[c]=pd.to_numeric(g.get(c),errors="coerce")
        g=g.dropna(subset=["date","open","close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)
        if len(g)>=20:out[p.stem.upper()]=g
    return out

def get_vni():
    url="https://kbbuddywts.kbsec.com.vn/iis-server/investment/index/VNINDEX/data_day?sdate=01-01-2023&edate=23-10-2026"
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as r:j=json.load(r)
    a=j["data_day"]; d=pd.DataFrame(a)
    d=d.rename(columns={"t":"date","o":"open","c":"close"})
    # KBS may return epoch milliseconds or date strings.
    if pd.api.types.is_numeric_dtype(d["date"]):
        d["date"]=pd.to_datetime(d.date,unit="ms",errors="coerce").dt.normalize()
    else:d["date"]=pd.to_datetime(d.date,errors="coerce",dayfirst=True).dt.normalize()
    d["open"]=pd.to_numeric(d.open,errors="coerce"); d["close"]=pd.to_numeric(d.close,errors="coerce")
    return d.dropna(subset=["date","open","close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)

def ret20(g,d,fee=FEE):
    z=g[g.date>=d].reset_index(drop=True)
    if len(z)<20 or z.iloc[0].date!=d:return np.nan
    return (float(z.iloc[19].close)/float(z.iloc[0].open)-1-fee)*100

def universe(raw,d,min_value_bn=None):
    vals=[]
    for s,g in raw.items():
        hist=g[g.date<d].tail(20)
        if len(hist)<20:continue
        avgvol=float(hist.volume.fillna(0).mean()); traded=int((hist.volume.fillna(0)>0).sum())
        if avgvol<=10000 or traded<15 or (d-hist.iloc[-1].date).days>7:continue
        if min_value_bn is not None:
            avgval=float((hist.close*hist.volume).mean()/1_000_000)
            if avgval<min_value_bn:continue
        x=ret20(g,d,FEE)
        if pd.notna(x):vals.append(x)
    return np.mean(vals) if vals else np.nan

def stats(x):
    x=pd.Series(x).dropna(); n=len(x); mean=x.mean(); sd=x.std(ddof=1)
    se=sd/np.sqrt(n) if n>1 else np.nan; t=mean/se if se and np.isfinite(se) else np.nan
    return n,mean,mean-1.96*se if n>1 else np.nan,mean+1.96*se if n>1 else np.nan,t

def main():
    raw=load_raw(); vni=get_vni()
    p=ROOT/"BACKTEST_FULL_TRADES.csv"
    if not p.exists():p=ROOT/"BACKTEST_6M_TRADES.csv"
    t=pd.read_csv(p)
    for c in ["signal_date","entry_date"]:t[c]=pd.to_datetime(t[c]).dt.normalize()
    cache1={}; cache3={}
    t["vnindex20"]=t.entry_date.map(lambda d:ret20(vni,d,FEE))
    t["alpha_vnindex20"]=t.ret20-t.vnindex20
    t["universe10k20"]=t.entry_date.map(lambda d:cache1.setdefault(d,universe(raw,d,None)))
    t["alpha_universe10k20"]=t.ret20-t.universe10k20
    t["universe3b20"]=t.entry_date.map(lambda d:cache3.setdefault(d,universe(raw,d,3.0)))
    t["alpha_universe3b20"]=t.ret20-t.universe3b20
    rows=[]
    for (p,m),g in t.groupby(["period","model"]):
        row={"period":p,"model":m}
        for col in ["alpha_vnindex20","alpha_universe10k20","alpha_universe3b20"]:
            n,mu,lo,hi,tt=stats(g[col]); row.update({col+"_n":n,col+"_mean":mu,col+"_ci95_lo":lo,col+"_ci95_hi":hi,col+"_t":tt})
        row.update(stock20=g.ret20.mean(),vnindex20=g.vnindex20.mean(),universe10k20=g.universe10k20.mean(),universe3b20=g.universe3b20.mean())
        rows.append(row)
    # Non-overlapping 20-session VNINDEX buy/hold windows to avoid massively overlapping observations.
    vals=[]; i=0
    while i+19<len(vni):
        vals.append((float(vni.iloc[i+19].close)/float(vni.iloc[i].open)-1-FEE)*100); i+=20
    n,mu,lo,hi,tt=stats(vals)
    print("VNINDEX RANGE",vni.date.min(),vni.date.max(),"ROWS",len(vni))
    print("VNINDEX BUY-HOLD NONOVERLAP 20D",dict(n=n,mean=mu,ci95_lo=lo,ci95_hi=hi,t=tt))
    print("NOTE: BOTH BOT AND ALL BENCHMARKS INCLUDE THE SAME 0.4% ROUND-TRIP COST.")
    print(pd.DataFrame(rows).to_string(index=False))
    pd.DataFrame(rows).to_csv("BACKTEST_STAT_BENCHMARK.csv",index=False,encoding="utf-8-sig")

if __name__=="__main__":main()
