# -*- coding: utf-8 -*-
"""
FINAL HOLDOUT 2026 ENGINE v1.0
Default behavior is VALIDATE ONLY. It will NOT reveal 2026 performance unless
called explicitly with:  --run-official --confirm LOCKED_SPEC_V1

Required beside this file:
  FINAL_HOLDOUT_2026_SPEC.md
  TOP5_V2_FROZEN_MODEL.joblib
  scanner_dataset_v2.csv
  send_top5_telegram_pro.py
  ai_decision_top5.py
  raw_cache/*.csv   (unfiltered OHLCV; execution source)

The official run creates FINAL_HOLDOUT_2026_OFFICIAL_LOCK.json and refuses to
overwrite an existing official result.
"""
from pathlib import Path
import argparse, hashlib, json, math, sys
import numpy as np
import pandas as pd
import joblib
import send_top5_telegram_pro as pro
import ai_decision_top5 as dec

BASE=Path(__file__).resolve().parent
SPEC=BASE/"FINAL_HOLDOUT_2026_SPEC.md"
MODEL=BASE/"TOP5_V2_FROZEN_MODEL.joblib"
SCANNER=BASE/"scanner_dataset_v2.csv"
RAW_DIR=BASE/"stock_cache"
LOCK=BASE/"FINAL_HOLDOUT_2026_OFFICIAL_LOCK.json"
EXPECTED_SPEC_SHA256="a8df38b00f54a3f664579dd262ed101430f3a374182d8ee7c06bca5dcd9dbbf6"

BUY_FEE=.0015; SELL_FEE=.0015; SELL_TAX=.0010
SLIPPAGES=(.001,.002,.003)
BUY_ZONE_SESSIONS=3; MAX_HOLD=20
POSITION_PCT=.10; MAX_OPEN=5
START=pd.Timestamp("2026-01-01")
END=pd.Timestamp("2026-12-31")

def sha256(p):
    h=hashlib.sha256()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()

def validate_inputs():
    checks=[]
    for p in [SPEC,MODEL,SCANNER,BASE/"send_top5_telegram_pro.py",BASE/"ai_decision_top5.py"]:
        checks.append((str(p.name),p.exists()))
    raw=list(RAW_DIR.glob("*.csv"))
    checks.append(("raw_cache>=687",len(raw)>=687))
    spec_ok=SPEC.exists() and sha256(SPEC)==EXPECTED_SPEC_SHA256
    checks.append(("SPEC_SHA256_LOCKED",spec_ok))
    if not all(x[1] for x in checks):
        raise RuntimeError("VALIDATION FAILED: "+str(checks))
    return checks,len(raw)

def load_raw():
    out={}
    for p in RAW_DIR.glob("*.csv"):
        g=pd.read_csv(p)
        if "time" in g.columns:g=g.rename(columns={"time":"date"})
        g["date"]=pd.to_datetime(g["date"])
        for c in ["open","high","low","close","volume"]:
            g[c]=pd.to_numeric(g[c],errors="coerce")
        g=g.dropna(subset=["date","open","high","low","close"]).sort_values("date").drop_duplicates("date").reset_index(drop=True)
        out[p.stem.upper()]=g
    return out

def load_scanner_and_score():
    d=pd.read_csv(SCANNER)
    d["date"]=pd.to_datetime(d["date"])
    d=d[(d.date>=START)&(d.date<=END)].copy()
    obj=joblib.load(MODEL)
    mdl=obj["model"]; feats=obj["features"]
    missing=[c for c in feats if c not in d.columns]
    if missing: raise RuntimeError(f"Missing model features: {missing}")
    # frozen model: prediction only, never fit
    d["SCORE"]=mdl.predict(d[feats])
    d=pro.add_price_structure(d)
    top=(d.sort_values(["date","SCORE"],ascending=[True,False])
           .groupby("date",as_index=False,group_keys=False).head(5).copy())
    top["RANK"]=top.groupby("date")["SCORE"].rank(method="first",ascending=False).astype(int)
    return d,top

def build_decisions(top):
    out=[]
    for day,g in top.groupby("date",sort=True):
        g=g.sort_values("RANK").copy()
        records,setup_rank=pro.prepare_analyses(g)
        cand=[]
        for i,(row,a) in enumerate(records):
            c=dec.evaluate_candidate(i+1,setup_rank[i],row,a)
            c.update({"date":day,"SCORE":row["SCORE"],"RANK":int(row["RANK"])})
            cand.append(c)
        cand.sort(key=lambda x:(not x["hard_no_buy"],x["decision_score"],x["setup_score"],x["rr1"],x["technical"]),reverse=True)
        for j,c in enumerate(cand,1): c["decision_rank"]=j
        best=dec.choose_best(cand); bs=best["symbol"] if best else None
        for c in cand:
            c["chosen"]=c["symbol"]==bs; out.append(c)
    return pd.DataFrame(out)

def net_leg(entry,exit_,slip,weight=1.0):
    buy=entry*(1+slip)*(1+BUY_FEE)
    sell=exit_*(1-slip)*(1-SELL_FEE-SELL_TAX)
    return weight*(sell/buy-1)

def after_signal(g,dt,n=50):
    return g[g.date>dt].head(n).reset_index(drop=True)

def simple_trade(symbol,signal_date,bmap,slip):
    g=after_signal(bmap[symbol],signal_date,MAX_HOLD+2)
    if len(g)<MAX_HOLD:return None
    e=g.iloc[0]; x=g.iloc[MAX_HOLD-1]
    return dict(symbol=symbol,signal_date=signal_date,entry_date=e.date,entry=float(e.open),
                exit_date=x.date,exit=float(x.close),ret=net_leg(float(e.open),float(x.close),slip),reason="TIME20")

def zone_trade(r,bmap,slip,ambiguous_open=False):
    g=after_signal(bmap[r.symbol],r.date,BUY_ZONE_SESSIONS+MAX_HOLD+5)
    if g.empty:return {"filled":False,"reason":"NO_DATA"}
    bl,bh=float(r.buy_low),float(r.buy_high); ent=None
    for i in range(min(BUY_ZONE_SESSIONS,len(g))):
        b=g.iloc[i]; o,h,l=float(b.open),float(b.high),float(b.low)
        if o<bl and h>=bl:
            if ambiguous_open: ent=(i,b.date,o,"AMBIG_OPEN")
            else:return {"filled":False,"reason":"AMBIGUOUS"}
            break
        if bl<=o<=bh: ent=(i,b.date,o,"OPEN_IN_ZONE"); break
        if o>bh and l<=bh: ent=(i,b.date,bh,"TOUCH_BUY_HIGH"); break
    if ent is None:return {"filled":False,"reason":"EXPIRED"}
    i,ed,ep,etype=ent; f=g.iloc[i:].reset_index(drop=True)
    stop=float(r.stop); t1=float(r.target1); t2=float(r.target2)
    hit1=False; legs=[]
    for j in range(1,min(MAX_HOLD-1,len(f)-1)+1):
        if j<3: continue
        b=f.iloc[j]; o,h,l=map(float,[b.open,b.high,b.low])
        st=ep if hit1 else stop
        if o<=st:
            w=.5 if hit1 else 1.; legs.append((b.date,o,w,"GAP_BE" if hit1 else "GAP_STOP")); break
        if l<=st:
            w=.5 if hit1 else 1.; legs.append((b.date,st,w,"BREAKEVEN" if hit1 else "STOP")); break
        if not hit1 and h>=t1:
            legs.append((b.date,t1,.5,"T1")); hit1=True; continue
        if hit1 and h>=t2:
            legs.append((b.date,t2,.5,"T2")); break
    if sum(x[2] for x in legs)<.999:
        idx=min(MAX_HOLD-1,len(f)-1); b=f.iloc[idx]
        legs.append((b.date,float(b.close),1-sum(x[2] for x in legs),"TIME20"))
    ret=sum(net_leg(ep,x[1],slip,x[2]) for x in legs)
    return dict(filled=True,reason="+".join(x[3] for x in legs),symbol=r.symbol,signal_date=r.date,
                entry_date=ed,entry=ep,entry_type=etype,exit_date=max(x[0] for x in legs),ret=ret,legs=str(legs))

def trade_stats(df,name,slip):
    if df is None or len(df)==0:return {"strategy":name,"slippage":slip,"trades":0}
    r=pd.Series(df.ret,dtype=float); pos=r[r>0].sum(); neg=-r[r<0].sum()
    winners=r.sort_values(ascending=False)
    total=r.sum()
    return dict(strategy=name,slippage=slip,trades=len(r),avg_trade=r.mean(),median_trade=r.median(),
                win_rate=(r>0).mean(),profit_factor=pos/neg if neg else np.inf,
                top5_winner_share=(winners.head(5).sum()/total if total else np.nan),
                top10_winner_share=(winners.head(10).sum()/total if total else np.nan))

def mtm_portfolio(trades,bmap):
    """Fixed 10% initial NAV allocation; daily mark-to-market; max five positions."""
    if trades.empty:return 0.,0.,pd.DataFrame(),pd.DataFrame(columns=["date","equity"])
    trades=trades.sort_values(["signal_date","priority"]).copy()
    calendar=sorted(set(pd.concat([g.date for g in bmap.values()]).dropna()))
    lo=min(trades.signal_date.min(),trades.entry_date.min()); hi=trades.exit_date.max()
    calendar=[d for d in calendar if lo<=d<=hi]
    pending=trades.to_dict("records"); accepted=[]; openp=[]; cash=1.0; curve=[]
    k=0
    for day in calendar:
        # close positions whose exit is today at their precomputed net trade outcome
        still=[]
        for p in openp:
            if p["exit_date"]<=day:
                cash += p["alloc"]*(1+p["ret"])
            else: still.append(p)
        openp=still
        # accept signals whose actual entry date is today, respecting 5 slots
        while k<len(pending) and pending[k]["entry_date"]<=day:
            p=pending[k]; k+=1
            if p["entry_date"]!=day or len(openp)>=MAX_OPEN: continue
            alloc=POSITION_PCT
            if cash+1e-12<alloc: continue
            cash-=alloc; p["alloc"]=alloc; openp.append(p); accepted.append(p)
        # MTM open positions using raw close; costs on hypothetical liquidation are included
        eq=cash
        for p in openp:
            g=bmap[p["symbol"]]
            z=g[g.date<=day]
            if z.empty: mark=p["entry"]
            else: mark=float(z.iloc[-1].close)
            # conservative mark: value after sell costs/slippage using base trade's slip stored
            slip=p["_slip"]
            gross=mark*(1-slip)*(1-SELL_FEE-SELL_TAX)/(p["entry"]*(1+slip)*(1+BUY_FEE))
            eq += p["alloc"]*gross
        curve.append((day,eq))
    ec=pd.DataFrame(curve,columns=["date","equity"])
    peak=ec.equity.cummax(); mdd=float((ec.equity/peak-1).min()) if len(ec) else np.nan
    return float(ec.equity.iloc[-1]-1),mdd,pd.DataFrame(accepted),ec

def benchmark_a(top,bmap,slip):
    """Like-for-like investable-universe signal benchmark on Top5 signal dates.
    Uses all scanner-eligible symbols available in top's parent scanner date set.
    This function is populated in official_run from scored scanner rows."""
    raise RuntimeError("benchmark_a must be called through official_run with scanner universe")

def official_run():
    d,top=load_scanner_and_score()
    decisions=build_decisions(top); bmap=load_raw()
    # Safety: raw data must cover enough future bars for official 2026 signals.
    max_raw=max(g.date.max() for g in bmap.values())
    if max_raw < pd.Timestamp("2027-01-20"):
        raise RuntimeError(
            f"OFFICIAL RUN BLOCKED: raw data ends {max_raw.date()}. "
            "Full-year 2026 + 20-session exits require 2027 follow-through data. "
            "Do not run a partial holdout as the official test."
        )
    signal_dates=sorted(top.date.unique())
    summaries=[]; alltrades=[]
    for slip in SLIPPAGES:
        # A: all scanner-eligible symbols on the exact B signal dates, same T+1 Open -> H20 Close
        aa=[]
        for day,g in d[d.date.isin(signal_dates)].groupby("date"):
            for s in g.symbol.unique():
                if s not in bmap: continue
                tr=simple_trade(s,day,bmap,slip)
                if tr: aa.append(tr)
        adf=pd.DataFrame(aa); summaries.append(trade_stats(adf,"A_UNIVERSE_LIKE_FOR_LIKE",slip))
        # B1
        bb=[]
        for r in top.itertuples(index=False):
            if r.symbol not in bmap: continue
            tr=simple_trade(r.symbol,r.date,bmap,slip)
            if tr: tr.update(priority=int(r.RANK),_slip=slip); bb.append(tr)
        bdf=pd.DataFrame(bb); summaries.append(trade_stats(bdf,"B1_TOP5_ALL_SIGNALS",slip))
        pr,mdd,acc,ec=mtm_portfolio(bdf,bmap); z=trade_stats(acc,"B2_TOP5_PORTFOLIO",slip); z.update(portfolio_return=pr,max_drawdown=mdd,return_over_mdd=(pr/abs(mdd) if mdd else np.nan)); summaries.append(z)
        # C
        chosen=decisions[decisions.chosen]
        cc=[]
        for r in chosen.itertuples(index=False):
            if r.symbol not in bmap: continue
            tr=simple_trade(r.symbol,r.date,bmap,slip)
            if tr: tr.update(priority=1,_slip=slip); cc.append(tr)
        cdf=pd.DataFrame(cc); z=trade_stats(cdf,"C_ALL_DECISION_SIGNALS",slip); summaries.append(z)
        pr,mdd,acc,ec=mtm_portfolio(cdf,bmap); z=trade_stats(acc,"C_PORTFOLIO",slip); z.update(portfolio_return=pr,max_drawdown=mdd,return_over_mdd=(pr/abs(mdd) if mdd else np.nan)); summaries.append(z)
        # D main and ambiguous sensitivity
        for amb,label in [(False,"D_MAIN"),(True,"D_AMBIG_OPEN")]:
            dd=[]
            for r in chosen.itertuples(index=False):
                if r.symbol not in bmap: continue
                tr=zone_trade(r,bmap,slip,amb)
                if tr.get("filled"):
                    tr.update(priority=1,_slip=slip); dd.append(tr)
            ddf=pd.DataFrame(dd); summaries.append(trade_stats(ddf,label+"_ALL_FILLED",slip))
            if len(ddf):
                pr,mdd,acc,ec=mtm_portfolio(ddf,bmap); z=trade_stats(acc,label+"_PORTFOLIO",slip); z.update(portfolio_return=pr,max_drawdown=mdd,return_over_mdd=(pr/abs(mdd) if mdd else np.nan)); summaries.append(z)
                for q in dd: q.update(strategy=label,slippage=slip); alltrades.append(q)
    pd.DataFrame(summaries).to_csv(BASE/"FINAL_HOLDOUT_2026_OFFICIAL_SUMMARY.csv",index=False)
    decisions.to_csv(BASE/"FINAL_HOLDOUT_2026_OFFICIAL_SIGNALS.csv",index=False)
    pd.DataFrame(alltrades).to_csv(BASE/"FINAL_HOLDOUT_2026_OFFICIAL_D_TRADES.csv",index=False)
    meta={"spec_sha256":sha256(SPEC),"engine_sha256":sha256(Path(__file__)),
          "model_sha256":sha256(MODEL),"scanner_sha256":sha256(SCANNER),
          "official_result_created":pd.Timestamp.utcnow().isoformat()}
    LOCK.write_text(json.dumps(meta,indent=2),encoding="utf-8")
    print("OFFICIAL 2026 HOLDOUT COMPLETE. Result is now locked.")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--run-official",action="store_true")
    ap.add_argument("--confirm",default="")
    args=ap.parse_args()
    checks,nraw=validate_inputs()
    print("VALIDATOR PASS")
    for k,v in checks: print(f"  {k}: {v}")
    print("  raw files:",nraw)
    print("  spec sha256:",sha256(SPEC))
    print("  engine sha256:",sha256(Path(__file__)))
    if not args.run_official:
        print("\nVALIDATE-ONLY MODE: 2026 performance was NOT revealed.")
        return
    if args.confirm!="LOCKED_SPEC_V1":
        raise RuntimeError("Official run requires --confirm LOCKED_SPEC_V1")
    if LOCK.exists():
        raise RuntimeError("Official result lock already exists. Refusing to overwrite/re-run.")
    official_run()

if __name__=="__main__": main()
