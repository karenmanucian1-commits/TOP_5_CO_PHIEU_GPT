from pathlib import Path
from datetime import datetime
import os, sys, requests, pandas as pd

ROOT=Path(__file__).resolve().parent

def f(x,n=2):
    try:return f"{float(x):.{n}f}"
    except:return "-"

def build(mode):
    realtime=mode=="midday"
    p=ROOT/("TOP5_ADVANCED_V3_REALTIME.csv" if realtime else "TOP5_ADVANCED_V3.csv")
    if not p.exists(): raise RuntimeError("Thieu file ket qua: "+str(p))
    d=pd.read_csv(p)
    title="🌅 TOP CỔ PHIẾU ĐÁNG MUA GPT V3.7" if not realtime else "⚡ CỔ PHIẾU CÒN ĐÁNG MUA - REALTIME V3.7"
    lines=[title,datetime.now().strftime("🕒 %d/%m/%Y %H:%M"),
           "✅ Chỉ hiển thị mã còn đủ chuẩn MUA",""]
    if d.empty:
        lines.append("⛔ Hiện không có cổ phiếu đủ chuẩn mua.")
    for i,x in d.reset_index(drop=True).iterrows():
        price=x.get("current_price",x.get("close"))
        rr=x.get("current_rr1",x.get("rr1"))
        lines += [
          f"🏆 #{i+1} {x.symbol} | ⭐ {f(x.advanced_score,1)}",
          f"💵 Giá: {f(price)} | Buy: {f(x.buy_low)} – {f(x.buy_high)}",
          f"🛑 Stop: {f(x.stop)}",
          f"🎯 T1: {f(x.target1)} | 🚀 T2: {f(x.target2)}",
          f"⚖️ R:R hiện tại: 1:{f(rr)}",
          f"📊 Finance {f(x.financial_score,0)} | Trend {f(x.trend_score,0)} | RS {f(x.rs_score,0)} | Flow {f(x.money_flow_score,0)}",
          ""
        ]
    if realtime:
        lines.append("📡 Giá thực thi: KBS realtime | Nền chọn mã: phiên EOD hoàn chỉnh")
    else:
        lines.append("📌 Nền chọn mã: phiên EOD hoàn chỉnh")
    return "\n".join(lines)

def main(mode=None):
    mode=(mode or (sys.argv[1] if len(sys.argv)>1 else "midday")).lower()
    msg=build(mode)
    print("\n"+msg,flush=True)
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:
        print("\n[TELEGRAM] Thieu TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID -> khong gui.",flush=True)
        return
    u=f"https://api.telegram.org/bot{token}/sendMessage"
    res=requests.post(u,json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},timeout=(5,15))
    if not res.ok: raise RuntimeError(f"Telegram HTTP {res.status_code}: {res.text[:300]}")
    print("[TELEGRAM] SENT OK",flush=True)

if __name__=="__main__":main()
