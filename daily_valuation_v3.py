from v3_common import *
OUT=ROOT/"VALUATION_V3.csv"
EPS=["eps_basic_vnd","earnings_per_share_vnd","trailing_eps"]
PAT=["attributable_to_parent_company","profit_after_tax_for_shareholders_of_the_parent_company","net_profit_attributable_to_shareholders_of_the_group","net_profit_loss_after_tax","profit_after_tax"]
def eps_calc(g,cap):
    # 1) Four quarterly EPS
    e=four_quarter_sum(g,["eps_basic_vnd","earnings_per_share_vnd"])
    if pd.notna(e) and e>0:return e,"4Q_EPS"
    # 2) Four-quarter PAT / current shares
    pat=four_quarter_sum(g,PAT)
    if pd.notna(pat) and pd.notna(cap) and cap>0:
        e=pat*10000/cap
        if e>0:return e,"4Q_PAT"
    # 3) Provider trailing EPS
    e=latest(g,["trailing_eps"],None)
    if pd.notna(e) and e>0:return e,"TRAILING"
    # 4) Latest annual EPS, never a single-quarter EPS
    e=latest(g,["eps_basic_vnd","earnings_per_share_vnd"],False)
    if pd.notna(e) and e>0:return e,"ANNUAL"
    return np.nan,"NONE"
def main():
    d=raw();s=pd.read_csv(ROOT/"scanner_dataset_v2.csv");sc=next(c for c in ["symbol","ticker","code"] if c in s);pc=next(c for c in ["close","price"] if c in s);dc=next((c for c in ["date","time","trading_date"] if c in s),None);s["symbol"]=s[sc].astype(str).str.upper()
    if dc:s[dc]=pd.to_datetime(s[dc],errors="coerce");s=s.sort_values(dc).groupby("symbol",as_index=False).tail(1)
    pm=dict(zip(s.symbol,pd.to_numeric(s[pc],errors="coerce")));out=[]
    for sym,g in d.groupby(d.symbol.astype(str).str.upper()):
        p=num(pm.get(sym));pv=p*1000 if pd.notna(p) and p<1000 else p;cap=latest(g,["charter_capital"]);eq=latest(g,["owners_equity","total_equity"])
        eps,src=eps_calc(g,cap);bv=latest(g,["book_value_per_share_bvps"])
        if pd.isna(bv) and pd.notna(eq) and pd.notna(cap) and cap>0:bv=eq/cap*10000
        pe=pv/eps if pd.notna(pv) and pd.notna(eps) and eps>0 else np.nan;pb=pv/bv if pd.notna(pv) and pd.notna(bv) and bv>0 else np.nan
        if pd.notna(pe) and not .5<=pe<=200:pe=np.nan
        if pd.notna(pb) and not .05<=pb<=30:pb=np.nan
        out.append([sym,ctype(g),p,eps,src,bv,pe,pb])
    o=pd.DataFrame(out,columns=["symbol","company_type","price","eps_ttm","eps_source","bvps","pe","pb"]);o.to_csv(OUT,index=False,encoding="utf-8-sig")
    print("VALUATION V3.3",len(o));print(o[o.symbol.isin(["VIB","ACV","HPG","VCB","SSI","BVH"])].to_string(index=False));return o
if __name__=="__main__":main()
