from pathlib import Path
import pandas as pd, numpy as np
ROOT=Path(__file__).resolve().parent
def main():
    f=pd.read_csv(ROOT/"FINANCIAL_SCORE_V3.csv")
    e=pd.read_csv(ROOT/"ENTRY_V3.csv")
    m=f.merge(e,on="symbol",how="inner")
    # Value20 in billion VND/day: price is thousand VND, volume shares => price*volume*1000 VND.
    m["avg_value20_bn"]=m["close"]*m["avg_volume20"]/1_000_000
    rules={
      "financial_ok":m.financial_score>=60,
      "coverage_ok":m.coverage>=66,
      "trend_ok":m.trend_score>=65,
      "rs_ok":m.rs_score>=55,
      "flow_ok":m.money_flow_score>=45,
      "liquidity_ok":m.avg_value20_bn>=3,
      "rr_ok":m.rr1>=1.20
    }
    for k,v in rules.items():m[k]=v.fillna(False)
    cols=list(rules)
    m["gate_pass_count"]=m[cols].sum(axis=1)
    m["candidate_pass"]=m[cols].all(axis=1)
    def why(x):
        names=[("financial_ok","Finance"),("coverage_ok","Coverage"),("trend_ok","Trend"),
               ("rs_ok","RS"),("flow_ok","Flow"),("liquidity_ok","GTGD20"),("rr_ok","R:R")]
        bad=[label for key,label in names if not bool(x[key])]
        return "PASS" if not bad else "FAIL: "+", ".join(bad)
    m["gate_reason"]=m.apply(why,axis=1)
    m.to_csv(ROOT/"CANDIDATE_GATE_V3.csv",index=False,encoding="utf-8-sig")
    print("CANDIDATE GATE V3.5:",int(m.candidate_pass.sum()),"/",len(m),"PASS")
    return m
if __name__=="__main__":main()
