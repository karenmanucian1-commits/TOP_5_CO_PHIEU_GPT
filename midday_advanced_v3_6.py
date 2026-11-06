from pathlib import Path
from datetime import datetime
import pandas as pd, numpy as np
import ssi_client

ROOT=Path(__file__).resolve().parent
def main():
    src=ROOT/"TOP5_ADVANCED_V3.csv"
    print("[1] Doc danh sach EOD:",src,flush=True)
    if not src.exists(): raise RuntimeError("THIEU TOP5_ADVANCED_V3.csv - CHAY MORNING TRUOC.")
    d=pd.read_csv(src)
    if d.empty:
        print("Khong co ma EOD dat Quality Gate.",flush=True)
        d.to_csv(ROOT/"TOP5_ADVANCED_V3_REALTIME.csv",index=False,encoding="utf-8-sig")
        return d
    syms=d["symbol"].astype(str).str.upper().tolist()
    print("[2] EOD PASS:",", ".join(syms),flush=True)

    mp=ssi_client.latest_prices(syms)
    miss=[s for s in syms if s not in mp]
    if miss: raise RuntimeError("SSI THIEU GIA: "+", ".join(miss)+" -> DUNG, KHONG FALLBACK.")

    d["eod_close"]=d["close"]
    d["current_price"]=[mp[s] for s in syms]
    d["current_risk"]=d.current_price-d.stop
    d["current_rr1"]=np.where(d.current_risk>0,(d.target1-d.current_price)/d.current_risk,np.nan)

    d["realtime_buy"]=(
        (d.current_price>d.stop)&
        (d.current_price<d.target1)&
        (d.current_rr1>=1.20)&
        (d.current_price<=d.buy_high*1.005)&
        (d.current_price>=d.buy_low*0.985)
    )
    d["realtime_action"]=np.select(
        [d.current_price<=d.stop,
         d.current_price>=d.target1,
         d.realtime_buy,
         d.current_price>d.buy_high*1.005,
         d.current_price<d.buy_low*0.985],
        ["LOAI-THUNG STOP","DA DAT T1-KHONG MUA DUOI","DANG MUA",
         "CHO-GIA VUOT BUY ZONE","CHO-GIA DUOI BUY ZONE"],
        default="CHO-RR<1.20"
    )

    # Keep EVERY EOD Quality-Gate stock for the midday decision layer.
    # Do not pre-filter orange/red names here: a qualified stock must remain visible
    # so Telegram can explain whether it is BUY / WAIT / NO BUY at the current price.
    out=d.sort_values("advanced_score",ascending=False).copy()
    d.to_csv(ROOT/"ADVANCED_V3_REALTIME_CHECK.csv",index=False,encoding="utf-8-sig")
    out.to_csv(ROOT/"TOP5_ADVANCED_V3_REALTIME.csv",index=False,encoding="utf-8-sig")

    print("\n=== V3.6.2 REALTIME CHECK |",datetime.now().strftime("%d/%m/%Y %H:%M:%S"),"===",flush=True)
    cols=["symbol","eod_close","current_price","buy_low","buy_high","stop","target1","current_rr1","realtime_action"]
    print(d[cols].to_string(index=False),flush=True)
    buy_count=int(d["realtime_buy"].sum())
    print("\n=> DAT GATE EOD:",len(out),"| QUA BO LOC REALTIME CU:",buy_count,flush=True)
    print("=> GIU TOAN BO MA DAT QUALITY GATE DE DECISION LAYER PHAN LOAI.",flush=True)
    return out

if __name__=="__main__": main()
