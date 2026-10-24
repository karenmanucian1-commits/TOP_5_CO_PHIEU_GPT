from pathlib import Path
import pandas as pd, numpy as np
ROOT=Path(__file__).resolve().parent
TECH=ROOT/"TECHNICAL_V3.csv"; OUT=ROOT/"ENTRY_V3.csv"

def main():
    d=pd.read_csv(TECH)
    # Reconstruct a robust volatility proxy from support/current range if ATR is absent.
    # Stop must never be unrealistically close: min risk 3.0%, normally 4.5%, max 7%.
    d["risk_pct"]=0.045
    weak=(d["trend_score"]<65)|(d["momentum_score"]<45)
    strong=(d["trend_score"]>=80)&(d["rs_score"]>=65)
    d.loc[weak,"risk_pct"]=0.055
    d.loc[strong,"risk_pct"]=0.040
    d["struct_stop"]=d["support"]*0.99
    d["pct_stop"]=d["close"]*(1-d["risk_pct"])
    # use lower stop (more room) but cap total risk at 7%
    d["stop"]=np.minimum(d["struct_stop"],d["pct_stop"])
    d["stop"]=np.maximum(d["stop"],d["close"]*0.93)
    d["risk"]=d["close"]-d["stop"]
    # T1 is structural resistance only if it offers >=1R; otherwise not a valid T1.
    d["target1"]=d["resistance"]
    d["rr1"]=(d["target1"]-d["close"])/d["risk"].replace(0,np.nan)
    # T2: extension at 2R, but never below T1.
    d["target2"]=np.maximum(d["target1"],d["close"]+2*d["risk"])
    d["rr2"]=(d["target2"]-d["close"])/d["risk"].replace(0,np.nan)
    # Buy zone: current to a modest pullback, never below support.
    d["buy_high"]=d["close"]
    d["buy_low"]=np.maximum(d["support"],d["close"]*0.985)
    d["entry_score"]=np.select(
        [d.rr1>=2,d.rr1>=1.5,d.rr1>=1.2,d.rr1>=1],
        [100,90,78,65],default=20)
    d["entry_ok"]=d.rr1>=1.2
    d.to_csv(OUT,index=False,encoding="utf-8-sig")
    print("ENTRY V3.4 DONE:",len(d))
    return d
if __name__=="__main__":main()
