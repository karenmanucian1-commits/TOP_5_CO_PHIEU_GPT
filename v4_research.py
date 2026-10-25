from pathlib import Path
import os, math, json, urllib.parse, urllib.request
import numpy as np, pandas as pd

ROOT=Path(__file__).resolve().parent
DEV_END=pd.Timestamp("2026-04-23"); HOLD_START=pd.Timestamp("2026-04-24")
FEE=.004; NET_TARGET=.02
MIN_DEV=120; MIN_FOLD=25

def load():
    out=[]
    for p in (ROOT/"stock_cache").glob("*.csv"):
        try: d=pd.read_csv(p)
        except: continue
        if "time" in d.columns: d=d.rename(columns={"time":"date"})
        need={"date","open","high","low","close","volume"}
        if not need.issubset(d.columns): continue
        d["date"]=pd.to_datetime(d.date,errors="coerce").dt.normalize()
        for c in ["open","high","low","close","volume"]: d[c]=pd.to_numeric(d[c],errors="coerce")
        d=d.dropna(subset=["date","open","high","low","close"]).sort_values("date").drop_duplicates("date")
        if len(d)<220: continue
        c=d.close; h=d.high; l=d.low; v=d.volume
        ma20=c.rolling(20).mean(); ma50=c.rolling(50).mean(); ma200=c.rolling(200).mean()
        delta=c.diff(); up=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); dn=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
        d["rsi"]=100-100/(1+up/dn.replace(0,np.nan))
        d["volr"]=v/v.rolling(20).mean().replace(0,np.nan)
        tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        d["atrp"]=tr.rolling(14).mean()/c*100
        d["ma20d"]=c/ma20-1; d["ma50d"]=c/ma50-1
        d["trend"]=((c>ma20)&(ma20>ma50)&((ma200.isna())|(c>ma200)))
        d["ret5"]=c.pct_change(5); d["ret20"]=c.pct_change(20)
        d["hh20"]=h.rolling(20).max().shift(1); d["break20"]=c/d.hh20-1
        d["avgvol20"]=v.rolling(20).mean()
        d["days20"]=(v>0).rolling(20).sum()
        d["avgval20bn"]=(c*v).rolling(20).mean()/1_000_000
        d["symbol"]=p.stem.upper()
        out.append(d)
    x=pd.concat(out,ignore_index=True)
    x=x[(x.date>=pd.Timestamp("2023-01-01"))&(x.date<=pd.Timestamp("2026-10-09"))]
    return x.sort_values(["symbol","date"]).reset_index(drop=True)

def candidates(x):
    # Broad causal prefilter only. Optimization chooses thresholds inside this set.
    return x[(x.avgvol20>10000)&(x.days20>=15)&(x.avgval20bn>=3)&x.trend&
             x.rsi.between(42,75)&x.volr.between(.6,4)&x.atrp.between(.8,7)&
             x.ma20d.between(-.02,.12)&x.ret20.between(-.08,.30)].copy()

def attach_future(allx,cand):
    groups={s:g.reset_index(drop=True) for s,g in allx.groupby("symbol")}
    rows=[]
    for z in cand.itertuples(index=False):
        g=groups[z.symbol]; idx=g.index[g.date==z.date]
        if len(idx)==0: continue
        i=int(idx[0])
        if i+1>=len(g): continue
        fut=g.iloc[i+1:i+11]
        if len(fut)<3: continue
        r=z._asdict(); r["entry"]=float(fut.iloc[0].open); r["entry_date"]=fut.iloc[0].date
        for k in [3,5,10]:
            r[f"close{k}"] = float(fut.iloc[k-1].close) if len(fut)>=k else np.nan
        r["future"]=fut[["date","high","low","close"]].to_dict("records")
        rows.append(r)
    return pd.DataFrame(rows)

def trade_result(r, stop_pct, target_net=NET_TARGET, horizon=10):
    buy=r.entry; stop=buy*(1-stop_pct); target=buy*(1+target_net+FEE)
    future=r.future[:horizon]
    exitp=float(future[-1]["close"]); reason="TIME"
    mae=0.; mfe=0.
    for b in future:
        mae=min(mae,(float(b["low"])/buy-1)*100)
        mfe=max(mfe,(float(b["high"])/buy-1)*100)
        # Conservative: if both occur in one daily candle, STOP wins.
        if float(b["low"])<=stop:
            exitp=stop; reason="STOP"; break
        if float(b["high"])>=target:
            exitp=target; reason="TARGET"; break
    ret=(exitp/buy-1-FEE)*100
    return ret,reason,mae,mfe

def mask(df,p):
    return (
      df.rsi.between(p["rsi_lo"],p["rsi_hi"]) &
      (df.volr>=p["volr"]) &
      df.atrp.between(p["atr_lo"],p["atr_hi"]) &
      (df.ma20d<=p["ma20max"]) &
      (df.ret5>=p["ret5min"]) &
      (df.ret20>=p["ret20min"]) &
      (df.avgval20bn>=p["valuebn"]) &
      (df.break20>=p["breakmin"])
    )

def dedup(df):
    # At most one new position per symbol per 10 sessions; rank stronger flow/momentum first each date.
    if df.empty:return df
    d=df.copy(); d["rankscore"]=(d.volr.clip(0,3)*20 + (70-(d.rsi-56).abs()) + d.ret20*100)
    d=d.sort_values(["date","rankscore"],ascending=[True,False])
    kept=[]; last={}
    for r in d.itertuples(index=False):
        if r.symbol in last and (r.entry_date-last[r.symbol]).days<14: continue
        kept.append(r._asdict()); last[r.symbol]=r.entry_date
    return pd.DataFrame(kept)

def evaluate(df,p):
    d=dedup(df[mask(df,p)])
    if d.empty:return None
    vals=[trade_result(r,p["stop"],horizon=p["horizon"]) for r in d.itertuples(index=False)]
    ret=np.array([v[0] for v in vals]); rs=[v[1] for v in vals]
    wins=ret>=NET_TARGET*100-1e-8
    pos=ret[ret>0]; neg=ret[ret<0]
    pf=pos.sum()/abs(neg.sum()) if len(neg) and neg.sum()!=0 else 99.
    return {"n":len(ret),"win2":wins.mean()*100,"positive":(ret>0).mean()*100,
            "avg":ret.mean(),"median":np.median(ret),"stop":np.mean(np.array(rs)=="STOP")*100,
            "target":np.mean(np.array(rs)=="TARGET")*100,
            "avgwin":pos.mean() if len(pos) else 0,"avgloss":neg.mean() if len(neg) else 0,
            "pf":pf,"mae":np.mean([v[2] for v in vals]),"mfe":np.mean([v[3] for v in vals])}

def params():
    for rlo in [45,48,50,52]:
      for rhi in [62,66,70]:
       if rhi<=rlo: continue
       for vr in [.8,1.0,1.2,1.5]:
        for ahi in [3.5,5.0]:
         for m20 in [.03,.06,.10]:
          for r5 in [-.02,0,.02]:
           for r20 in [-.03,.02,.06]:
            for val in [3,10]:
             for br in [-.04,-.01,.01]:
              for stop in [.015,.02,.025,.03]:
               for hz in [5,10]:
                yield dict(rsi_lo=rlo,rsi_hi=rhi,volr=vr,atr_lo=.8,atr_hi=ahi,ma20max=m20,
                           ret5min=r5,ret20min=r20,valuebn=val,breakmin=br,stop=stop,horizon=hz)

def main():
    allx=load(); c=attach_future(allx,candidates(allx))
    dev=c[c.date<=DEV_END].copy(); hold=c[c.date>=HOLD_START].copy()
    folds=[dev[(dev.date>=a)&(dev.date<=b)] for a,b in [
      (pd.Timestamp("2023-01-01"),pd.Timestamp("2024-01-31")),
      (pd.Timestamp("2024-02-01"),pd.Timestamp("2025-03-31")),
      (pd.Timestamp("2025-04-01"),DEV_END)]]
    best=None; tested=0; passed=0
    for p in params():
        tested+=1; e=evaluate(dev,p)
        if not e or e["n"]<MIN_DEV or e["avg"]<=0 or e["pf"]<=1: continue
        fs=[evaluate(f,p) for f in folds]
        if any((not q) or q["n"]<MIN_FOLD or q["avg"]<=0 for q in fs): continue
        passed+=1
        # Reward net >=2% hit-rate + expectancy/PF; penalize stops and fold instability.
        minavg=min(q["avg"] for q in fs)
        score=e["win2"]*1.2 + e["positive"]*.35 + e["avg"]*10 + min(e["pf"],3)*5 - e["stop"]*.45 + minavg*8
        rec=(score,p,e,fs)
        if best is None or score>best[0]: best=rec
    if best is None: raise RuntimeError("No robust V4 configuration passed locked Development constraints")
    score,p,devres,fs=best
    # LOCK occurs here. Holdout is evaluated only after winner selection; never feeds parameter search.
    holdres=evaluate(hold,p)
    out={"tested":tested,"robust_passed":passed,"locked_params":p,"development":devres,
         "development_folds":fs,"holdout":holdres,
         "data_max":str(allx.date.max().date()),"fee_pct":FEE*100,
         "definition":"TARGET = >= +2.0% net after 0.4% round-trip fee; conservative STOP-first same candle"}
    Path("v4_result.json").write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print("V4_RESULT",json.dumps(out,ensure_ascii=False,default=str))
    token=os.getenv("BOT_TOKEN"); chat=os.getenv("CHAT_ID")
    if token and chat:
        h=holdres or {}
        msg=("🧪 BOT V4 EXPERIMENTAL - BACKTEST XONG\n"
             f"Grid: {tested:,} | Robust pass: {passed:,}\n"
             f"DEV: {devres['n']} lệnh | Win ≥2%: {devres['win2']:.1f}% | Có lãi: {devres['positive']:.1f}% | P/L TB: {devres['avg']:+.2f}% | Stop: {devres['stop']:.1f}% | PF: {devres['pf']:.2f}\n"
             f"HOLDOUT: {h.get('n',0)} lệnh | Win ≥2%: {h.get('win2',float('nan')):.1f}% | Có lãi: {h.get('positive',float('nan')):.1f}% | P/L TB: {h.get('avg',float('nan')):+.2f}% | Stop: {h.get('stop',float('nan')):.1f}% | PF: {h.get('pf',float('nan')):.2f}\n"
             f"Phí: 0.4% | Data tới: {out['data_max']}\n"
             "⚠️ Research only - KHÔNG thay đổi V2/V3.7.")
        data=urllib.parse.urlencode({"chat_id":chat,"text":msg}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",data=data),timeout=20).read()
        print("TELEGRAM_SENT")
if __name__=="__main__": main()
