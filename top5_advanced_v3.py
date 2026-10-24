from pathlib import Path
import pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parent
def sector_group(sym,typ):
    if typ=="NH":return "BANK"
    if typ=="CK":return "SECURITIES"
    if typ=="BH":return "INSURANCE"
    return "CORPORATE"
def main():
    m=pd.read_csv(ROOT/"CANDIDATE_GATE_V3.csv")
    m["sector_group"]=[sector_group(s,t) for s,t in zip(m.symbol,m.company_type)]
    # Rank only PASS candidates. Score uses only measured dimensions.
    m["advanced_score"]=(
      m.financial_score*.30+m.trend_score*.16+m.rs_score*.15+
      m.money_flow_score*.12+m.momentum_score*.08+
      m.liquidity_score*.07+m.entry_score*.12)
    m["advanced_score"]*=np.clip(m.coverage/80,.85,1)
    m["action"]=np.where(m.candidate_pass,"MUA/CHO DIEM VAO","KHONG MUA")
    passed=m[m.candidate_pass].sort_values("advanced_score",ascending=False)
    # User requirement: keep EVERY stock that passes the Quality Gate.
    # No Top-5 cap and no sector-count cap at the output layer.
    top=passed.copy()
    cols=["symbol","sector_group","action","advanced_score","financial_score","coverage","close",
          "buy_low","buy_high","stop","target1","target2","rr1","rr2","trend_score","rs_score",
          "money_flow_score","momentum_score","liquidity_score","avg_value20_bn","pe","pb"]
    m.sort_values(["candidate_pass","advanced_score"],ascending=[False,False]).to_csv(ROOT/"ADVANCED_V3_RANKING.csv",index=False,encoding="utf-8-sig")
    if len(top):top[cols].to_csv(ROOT/"TOP5_ADVANCED_V3.csv",index=False,encoding="utf-8-sig")
    else:pd.DataFrame(columns=cols).to_csv(ROOT/"TOP5_ADVANCED_V3.csv",index=False,encoding="utf-8-sig")
    print("\n=== TOP5 ADVANCED GPT V3.5 - QUALITY GATE ===")
    if len(top):print(top[cols].to_string(index=False))
    else:print("KHONG CO MA NAO DAT TOAN BO GATE -> KHONG EP DU 5 MA.")
    print("\nBAT BUOC: Finance>=60 | Coverage>=66 | Trend>=65 | RS>=55 | Flow>=45 | GTGD20>=3 ty | RR>=1.20")
    print("So ma PASS:",len(passed))
    print("TOP5 ->",ROOT/"TOP5_ADVANCED_V3.csv")
    return top
if __name__=="__main__":main()
