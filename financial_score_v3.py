from v3_common import *
OUT=ROOT/"FINANCIAL_SCORE_V3.csv"
def pct(x):
    x=num(x);return x*100 if pd.notna(x) and abs(x)<=1.5 else x
def hi(v,b):
    if pd.isna(v):return np.nan
    for t,s in b:
        if v>=t:return s
    return 0
def lo(v,b):
    if pd.isna(v):return np.nan
    for t,s in b:
        if v<=t:return s
    return 0
def main():
    v=pd.read_csv(ROOT/"VALUATION_V3.csv");d=raw();out=[]
    for sym,g in d.groupby(d.symbol.astype(str).str.upper()):
        q=v[v.symbol==sym]
        if q.empty:continue
        x=q.iloc[0];roe=pct(latest(g,["roe","roe_trailling"]));roa=pct(latest(g,["roa","roa_trailling"]));mar=pct(latest(g,["net_margin"]));debt=pct(latest(g,["debt_to_assets","liabilities_to_assets"]));nim=pct(latest(g,["net_interest_margin_nim","net_interest_margin"]));npl=pct(latest(g,["npl","non_performing_loan_ratio"]))
        a=[hi(roe,[(20,100),(15,90),(10,75),(5,55),(0,35),(-999,10)]),hi(roa,[(8,100),(5,90),(3,75),(1,55),(0,35),(-999,10)])]
        if x.company_type=="NH":a += [hi(nim,[(4,100),(3,85),(2,65),(1,40),(-999,20)]),lo(npl,[(1,100),(2,80),(3,55),(5,30),(999,10)])]
        else:a += [hi(mar,[(20,100),(12,85),(7,70),(3,50),(0,35),(-999,10)]),lo(debt,[(30,100),(45,85),(60,65),(75,40),(999,20)])]
        a += [lo(num(x.pe),[(8,100),(12,90),(18,75),(25,55),(40,35),(200,15)]),lo(num(x.pb),[(1,100),(1.5,90),(2.5,75),(4,55),(7,35),(30,15)])]
        good=[z for z in a if pd.notna(z)];cov=len(good)/len(a)*100;rawscore=np.mean(good) if good else np.nan
        # Strong coverage penalty: 50% data can no longer display 90-100 financial quality.
        penalty=1.0 if cov>=80 else (.88 if cov>=66 else (.72 if cov>=50 else .55))
        fs=rawscore*penalty if pd.notna(rawscore) else np.nan
        out.append([sym,x.company_type,round(fs,1),round(rawscore,1) if pd.notna(rawscore) else np.nan,round(cov,1),roe,roa,x.pe,x.pb,x.eps_source])
    o=pd.DataFrame(out,columns=["symbol","company_type","financial_score","raw_financial_score","coverage","roe","roa","pe","pb","eps_source"]);o.to_csv(OUT,index=False,encoding="utf-8-sig");print("FIN SCORE V3.3",len(o));return o
if __name__=="__main__":main()
