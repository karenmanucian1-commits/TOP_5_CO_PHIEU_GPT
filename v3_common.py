from pathlib import Path
import sqlite3,re,math,pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parent; DB=ROOT/"financial_v3.sqlite"
def num(x):
    try:v=float(x);return v if math.isfinite(v) else np.nan
    except:return np.nan
def pk(x):
    s=re.sub(r"_\d+$","",str(x).replace("-Năm","").replace("-Nam",""))
    m=re.search(r"(\d{4})-Q([1-4])",s)
    if m:return int(m.group(1))*10+int(m.group(2))
    m=re.search(r"(\d{4})",s);return int(m.group(1))*10 if m else -1
def raw():
    if not DB.exists():raise SystemExit("KHONG THAY financial_v3.sqlite")
    c=sqlite3.connect(DB);tabs=pd.read_sql("select name from sqlite_master where type='table'",c)["name"].tolist()
    t="financial_raw" if "financial_raw" in tabs else ("raw_financial" if "raw_financial" in tabs else None)
    if not t:c.close();raise SystemExit("DB thieu financial_raw/raw_financial")
    d=pd.read_sql(f"select * from {t}",c);c.close();return d
def rows(g,ids,quarter=None):
    z=g[g.item_id.astype(str).isin(ids)].copy()
    if z.empty:return z
    if quarter is not None and "period_type" in z:
        p=z.period_type.astype(str).str.lower()
        q=z[p.str.startswith("q")] if quarter else z[~p.str.startswith("q")]
        if not q.empty:z=q
    z["_k"]=z.period.map(pk);z["v"]=pd.to_numeric(z.value,errors="coerce")
    sort=["_k"]+(["fetched_at"] if "fetched_at" in z else [])
    return z.dropna(subset=["v"]).sort_values(sort).drop_duplicates(["item_id","_k"],keep="last")
def latest(g,ids,quarter=None):
    z=rows(g,ids,quarter);return z.v.iloc[-1] if len(z) else np.nan
def four_quarter_sum(g,ids):
    z=rows(g,ids,True)
    if z.empty:return np.nan
    # choose one observation per quarter; require 4 distinct recent quarters
    z=z.sort_values("_k").drop_duplicates("_k",keep="last").tail(4)
    if len(z)<4:return np.nan
    ks=z["_k"].tolist()
    # continuity check across year boundary
    def nxt(k):
        y,q=divmod(int(k),10);return (y+1)*10+1 if q==4 else y*10+q+1
    if any(nxt(ks[i])!=ks[i+1] for i in range(3)):return np.nan
    return float(z.v.sum())
def ctype(g):
    ids=set(g.item_id.astype(str))
    if {"net_interest_income","interest_and_similar_income"}&ids:return "NH"
    if {"revenue_in_brokerage_services","financial_assets_at_fair_value_through_profit_or_loss_fvtpl"}&ids:return "CK"
    if {"gross_written_premium","claim_and_maturity_payment_expenses"}&ids:return "BH"
    return "CT"
