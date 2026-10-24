from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import os, sys, requests, pandas as pd

ROOT=Path(__file__).resolve().parent

def f(x,n=2):
    try:return f"{float(x):.{n}f}"
    except:return "-"

def action_label(x, realtime=False):
    """Unified execution meaning with V2.

    GREEN means the stock is qualified AND the current entry is executable.
    Passing the V3.7 Quality Gate alone is never enough to produce GREEN.
    """
    def num(name, fallback=None, default=float("nan")):
        try:
            v=x.get(name, x.get(fallback) if fallback else default)
            return float(v)
        except Exception:
            return default

    price=num("current_price","close")
    low=num("buy_low")
    high=num("buy_high")
    stop=num("stop")
    target1=num("target1")
    rr=num("current_rr1","rr1",0.0)
    momentum=num("momentum_score",default=50.0)   # RSI14 in Technical V3
    flow=num("money_flow_score",default=0.0)
    liquidity=num("liquidity_score",default=0.0)

    # Invalidated / already reached target: no new entry.
    if price <= stop:
        return "🔴 KHÔNG MUA - ĐÃ THỦNG STOP"
    if price >= target1:
        return "🔴 KHÔNG MUA - ĐÃ ĐẠT TARGET 1"

    # Same interpretation as V2: qualified stock, but entry is not ready.
    if price < low:
        return "🟠 CHỜ GIÁ ỔN ĐỊNH/VỀ VÙNG MUA"
    if price > high * 1.002:
        return "🟠 CHỜ GIÁ VỀ VÙNG MUA"
    if rr < 1.30:
        return "🟠 CHỜ ĐIỂM VÀO TỐT HƠN"
    if momentum >= 75:
        return "🟠 CHỜ HẠ NHIỆT RSI"
    if flow < 50:
        return "🟠 CHỜ DÒNG TIỀN XÁC NHẬN"
    if liquidity < 30:
        return "🟠 CHỜ THANH KHOẢN XÁC NHẬN"

    return "🟢 ĐỦ ĐIỀU KIỆN MUA"

def build(mode):
    realtime=mode=="midday"
    p=ROOT/("TOP5_ADVANCED_V3_REALTIME.csv" if realtime else "TOP5_ADVANCED_V3.csv")
    if not p.exists(): raise RuntimeError("Thieu file ket qua: "+str(p))
    d=pd.read_csv(p)
    title="🚀 CỔ PHIẾU CHẤT LƯỢNG GPT" if not realtime else "🚀 CỔ PHIẾU CHẤT LƯỢNG GPT — REALTIME"
    lines=[title,datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("🕒 %d/%m/%Y %H:%M"),
           "✅ Đã qua Quality Gate — xem HÀNH ĐỘNG để biết điểm mua",""]
    if d.empty:
        lines.append("⛔ Hiện không có cổ phiếu đủ chuẩn mua.")
    for i,x in d.reset_index(drop=True).iterrows():
        price=x.get("current_price",x.get("close"))
        rr=x.get("current_rr1",x.get("rr1"))
        action=action_label(x,realtime)
        reason=""
        try:
            price_num=float(price)
            low_num=float(x.buy_low)
            high_num=float(x.buy_high)
            rr_num=float(rr)
            momentum=float(x.get("momentum_score",50.0))
            flow=float(x.get("money_flow_score",0.0))
            liquidity=float(x.get("liquidity_score",0.0))
            stop_num=float(x.stop)
            target1_num=float(x.target1)
            if price_num <= stop_num:
                reason=f"↳ Lý do: Giá {f(price_num)} ≤ Stop {f(stop_num)}"
            elif price_num >= target1_num:
                reason=f"↳ Lý do: Giá {f(price_num)} ≥ T1 {f(target1_num)}"
            elif price_num < low_num:
                reason=f"↳ Lý do: Giá {f(price_num)} < Buy Low {f(low_num)}"
            elif price_num > high_num*1.002:
                reason=f"↳ Lý do: Giá {f(price_num)} > Buy High {f(high_num)} × 1.002"
            elif rr_num < 1.30:
                reason=f"↳ Lý do: R:R {rr_num:.2f} < 1.30"
            elif momentum >= 75:
                reason=f"↳ Lý do: RSI {momentum:.1f} ≥ 75"
            elif flow < 50:
                reason=f"↳ Lý do: Flow {flow:.0f} < 50"
            elif liquidity < 30:
                reason=f"↳ Lý do: Liquidity score {liquidity:.0f} < 30"
        except Exception:
            reason=""

        lines += [
          f"📌 {x.symbol} | ⭐ {f(x.advanced_score,1)}",
          f"⚡ Hành động: {action}",
          *([reason] if reason else []),
          f"💵 Giá: {f(price)} | Buy: {f(x.buy_low)} – {f(x.buy_high)}",
          f"🛑 Stop: {f(x.stop)}",
          f"🎯 T1: {f(x.target1)} | 🚀 T2: {f(x.target2)}",
          f"⚖️ R:R hiện tại: 1:{f(rr)}" if realtime else f"⚖️ R:R tại giá EOD: 1:{f(rr)}",
          f"📊 Finance {f(x.financial_score,0)} | Trend {f(x.trend_score,0)} | RS {f(x.rs_score,0)} | Flow {f(x.money_flow_score,0)}",
          ""
        ]
    if realtime:
        buy_now=[]
        for _,x in d.iterrows():
            if action_label(x,True)=="🟢 ĐỦ ĐIỀU KIỆN MUA":
                buy_now.append(str(x.symbol))
        if buy_now:
            lines += ["━━━━━━━━━━━━━━━━━━━━", "🟢 CÒN MUA ĐƯỢC NGAY: "+", ".join(buy_now)]
        else:
            lines += ["━━━━━━━━━━━━━━━━━━━━", "⛔ Hiện không có mã nào còn điểm mua đạt chuẩn realtime."]
        lines.append("📡 Giá thực thi: KBS realtime | Nền chọn mã: phiên EOD hoàn chỉnh")
    else:
        lines.append("📌 Nền chọn mã: phiên EOD hoàn chỉnh")
    return "\n".join(lines)

def main(mode=None):
    mode=(mode or (sys.argv[1] if len(sys.argv)>1 else "midday")).lower()
    msg=build(mode)
    print("\n"+msg,flush=True)
    token=(os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat=(os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        print("\n[TELEGRAM] Thieu BOT_TOKEN/CHAT_ID -> khong gui.",flush=True)
        return
    u=f"https://api.telegram.org/bot{token}/sendMessage"
    res=requests.post(u,json={"chat_id":chat,"text":msg,"disable_web_page_preview":True},timeout=(5,15))
    if not res.ok: raise RuntimeError(f"Telegram HTTP {res.status_code}: {res.text[:300]}")
    print("[TELEGRAM] SENT OK",flush=True)

if __name__=="__main__":main()
