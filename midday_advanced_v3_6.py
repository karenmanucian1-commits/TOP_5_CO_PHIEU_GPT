from pathlib import Path
from datetime import datetime
import pandas as pd, numpy as np, requests

ROOT=Path(__file__).resolve().parent
KBS_URL="https://kbbuddywts.kbsec.com.vn/iis-server/investment/stock/iss"

def kbs_prices(symbols):
    print("[KBS] Dang ket noi truc tiep...", flush=True)
    try:
        z=requests.post(
            KBS_URL,
            json={"code":",".join(symbols)},
            headers={"x-lang":"vi","User-Agent":"Mozilla/5.0","Content-Type":"application/json"},
            timeout=(5,12)
        )
        print("[KBS] HTTP",z.status_code,flush=True)
        z.raise_for_status()
        obj=z.json()
    except requests.Timeout:
        raise RuntimeError("KBS TIMEOUT >12s. DUNG, KHONG DUNG GIA EOD.")
    except Exception as e:
        raise RuntimeError(f"KBS CONNECTION ERROR: {type(e).__name__}: {e}")

    # recursively locate quote dictionaries containing SB + CP
    found=[]
    def walk(x):
        if isinstance(x,dict):
            if ("SB" in x or "symbol" in x or "code" in x) and ("CP" in x or "price" in x or "close" in x):
                found.append(x)
            for v in x.values(): walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj)

    mp={}
    for x in found:
        s=str(x.get("SB") or x.get("symbol") or x.get("code") or "").upper().strip()
        p=x.get("CP",x.get("price",x.get("close")))
        try:
            p=float(str(p).replace(",",""))
            if p>1000: p/=1000.0
            if s and p>0: mp[s]=p
        except: pass
    print(f"[KBS] Lay duoc {len(mp)}/{len(symbols)} gia",flush=True)
    return mp

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

    mp=kbs_prices(syms)
    miss=[s for s in syms if s not in mp]
    if miss: raise RuntimeError("KBS THIEU GIA: "+", ".join(miss)+" -> DUNG, KHONG FALLBACK.")

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

    out=d[d.realtime_buy].sort_values("advanced_score",ascending=False).copy()
    d.to_csv(ROOT/"ADVANCED_V3_REALTIME_CHECK.csv",index=False,encoding="utf-8-sig")
    out.to_csv(ROOT/"TOP5_ADVANCED_V3_REALTIME.csv",index=False,encoding="utf-8-sig")

    print("\n=== V3.6.2 REALTIME CHECK |",datetime.now().strftime("%d/%m/%Y %H:%M:%S"),"===",flush=True)
    cols=["symbol","eod_close","current_price","buy_low","buy_high","stop","target1","current_rr1","realtime_action"]
    print(d[cols].to_string(index=False),flush=True)
    print("\n=> CON DANG MUA:",len(out),"/",len(d),flush=True)
    if len(out): print("=>",", ".join(out.symbol.astype(str)),flush=True)
    else: print("=> KHONG CO MA NAO CON DU CHUAN MUA",flush=True)
    return out

if __name__=="__main__": main()
