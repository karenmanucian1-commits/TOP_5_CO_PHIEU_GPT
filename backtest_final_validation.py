# -*- coding: utf-8 -*-
"""Final isolated validation: universe benchmark + frozen swing-high target. Never edits production bots."""
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path(__file__).resolve().parent
FEE=.004
DEV_END=pd.Timestamp("2026-04-23")

def load_raw():
    out={}
    for p in (ROOT/"stock_cache").glob("*.csv"):
        try:g=pd.read_csv(p)
        except:continue
        if "time" in g:g=g.rename(columns={"time":"date"})
        if "date" not in g:continue
        g["date"]=pd.to_datetime(g.date,errors="coerce").dt.normalize()
        for c in ["open","high","low","close","volume"]:g[c]=pd.to_numeric(g.get(c),errors="coerce")
        g=g.dropna(subset=["date","open","high","low","close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)
        if len(g)>=80:out[p.stem.upper()]=g
    return out

def universe20(raw,entry_date):
    vals=[]
    for s,g in raw.items():
        if s=="VNINDEX":continue
        hist=g[g.date<entry_date].tail(20)
        if len(hist)<20:continue
        avgvol=hist.volume.fillna(0).mean(); traded=(hist.volume.fillna(0)>0).sum()
        stale=(entry_date-hist.iloc[-1].date).days
        if avgvol<=10000 or traded<15 or stale>7:continue
        fut=g[g.date>=entry_date].reset_index(drop=True)
        if len(fut)>=20 and fut.iloc[0].date==entry_date:
            vals.append((float(fut.iloc[19].close)/float(fut.iloc[0].open)-1-FEE)*100)
    return float(np.mean(vals)) if vals else np.nan

def atr14(g,idx):
    h=g.iloc[max(0,idx-20):idx+1].copy()
    pc=h.close.shift(1); tr=pd.concat([(h.high-h.low),(h.high-pc).abs(),(h.low-pc).abs()],axis=1).max(axis=1)
    return float(tr.rolling(14).mean().iloc[-1])

def swing_target(g,signal_date,entry,stop):
    # Frozen before holdout: 60 sessions, swing high > 3 prior + 3 next, > current + 1 ATR; nearest is T1.
    idxs=g.index[g.date<=signal_date]
    if len(idxs)==0:return entry+2*(entry-stop)
    ix=int(idxs[-1]); start=max(3,ix-59); end=ix-3
    atr=atr14(g,ix); cand=[]
    if np.isfinite(atr):
        for j in range(start,end+1):
            x=float(g.loc[j,"high"])
            if x>float(g.loc[j-3:j-1,"high"].max()) and x>float(g.loc[j+1:j+3,"high"].max()) and x>entry+atr:
                cand.append(x)
    return min(cand) if cand else entry+2*(entry-stop)

def simulate(g,entry_date,entry,stop,target):
    fut=g[g.date>=entry_date].reset_index(drop=True).head(21)
    if fut.empty:return np.nan,False,False
    horizon=fut.head(20); xp=float(horizon.iloc[-1].close); reason="TIME20"
    for i,b in horizon.iterrows():
        t=i+1
        if t<2:continue
        if float(b.close)<stop:
            xp=float(fut.iloc[i+1].open) if i+1<len(fut) else float(b.close); reason="STOP"; break
        if float(b.high)>=target:
            xp=target; reason="T1"; break
    return (xp/entry-1-FEE)*100,reason=="T1",reason=="STOP"

def main():
    raw=load_raw()
    t=pd.read_csv(ROOT/"BACKTEST_FULL_TRADES.csv")
    for c in ["signal_date","entry_date"]:t[c]=pd.to_datetime(t[c]).dt.normalize()
    # Benchmark is point-in-time equal-weight liquid universe.
    cache={}
    t["universe20"]=t.entry_date.map(lambda d:cache.setdefault(d,universe20(raw,d)))
    t["alpha_universe20"]=t.ret20-t.universe20
    exp=[]
    for r in t.itertuples(index=False):
        g=raw.get(r.symbol)
        if g is None:continue
        nt=swing_target(g,r.signal_date,float(r.entry),float(r.stop))
        nr,hit,st=simulate(g,r.entry_date,float(r.entry),float(r.stop),nt)
        exp.append(dict(model=r.model,period=r.period,symbol=r.symbol,signal_date=r.signal_date,entry=r.entry,stop=r.stop,
                        old_target=r.target1,new_target=nt,old_planned=r.planned_ret,new_planned=nr,
                        old_stop=bool(r.stop_hit),new_stop=st,old_t1=bool(r.t1_hit),new_t1=hit))
    e=pd.DataFrame(exp)
    rows=[]
    for (p,m),g in e.groupby(["period","model"]):
        rows.append(dict(period=p,model=m,n=len(g),old_planned=g.old_planned.mean(),new_planned=g.new_planned.mean(),
                         delta_planned=g.new_planned.mean()-g.old_planned.mean(),
                         old_stop_pct=g.old_stop.mean()*100,new_stop_pct=g.new_stop.mean()*100,
                         old_t1_pct=g.old_t1.mean()*100,new_t1_pct=g.new_t1.mean()*100,
                         old_target_dist=((g.old_target/g.entry-1)*100).mean(),new_target_dist=((g.new_target/g.entry-1)*100).mean()))
    b=[]
    for (p,m),g in t.groupby(["period","model"]):
        b.append(dict(period=p,model=m,n20=g.ret20.notna().sum(),stock20=g.ret20.mean(),universe20=g.universe20.mean(),
                      alpha_universe20=g.alpha_universe20.mean()))
    pd.DataFrame(rows).to_csv(ROOT/"BACKTEST_TARGET_COMPARISON.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame(b).to_csv(ROOT/"BACKTEST_BENCHMARK.csv",index=False,encoding="utf-8-sig")
    e.to_csv(ROOT/"BACKTEST_TARGET_TRADES.csv",index=False,encoding="utf-8-sig")
    print("TARGET COMPARISON"); print(pd.DataFrame(rows).to_string(index=False))
    print("\nUNIVERSE BENCHMARK"); print(pd.DataFrame(b).to_string(index=False))
    print("\nRULE: accept experimental target only if avg planned P/L higher AND stop-hit not higher in BOTH Development and Holdout. No live bot changes.")

if __name__=="__main__":main()
