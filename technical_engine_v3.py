from v3_common import *
import os
CACHE=Path(os.environ.get("STOCK_CACHE_DIR") or (ROOT/"stock_cache"))
OUT=ROOT/"TECHNICAL_V3.csv"
def RSI(c,n=14):
 d=c.diff();u=d.clip(lower=0).rolling(n).mean();q=(-d.clip(upper=0)).rolling(n).mean();rs=u/q.replace(0,np.nan);return 100-100/(1+rs)
def calc(sym):
 f=CACHE/f"{sym}.csv"
 if not f.exists():return
 d=pd.read_csv(f);cc=next((x for x in ["close","Close"] if x in d),None);vc=next((x for x in ["volume","Volume"] if x in d),None);hc=next((x for x in ["high","High"] if x in d),None);lc=next((x for x in ["low","Low"] if x in d),None)
 if not cc or len(d)<60:return
 c=pd.to_numeric(d[cc],errors="coerce");v=pd.to_numeric(d[vc],errors="coerce") if vc else pd.Series(np.nan,index=d.index);h=pd.to_numeric(d[hc],errors="coerce") if hc else c;l=pd.to_numeric(d[lc],errors="coerce") if lc else c
 ma20=c.rolling(20).mean();ma50=c.rolling(50).mean();ma200=c.rolling(200).mean();rv=RSI(c);av=v.rolling(20).mean();mf=(((c-l)-(h-c))/(h-l).replace(0,np.nan)*v).rolling(20).sum()/v.rolling(20).sum();tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1);atr=tr.rolling(14).mean();i=-1
 trend=(25 if c.iloc[i]>ma20.iloc[i] else 5)+(25 if c.iloc[i]>ma50.iloc[i] else 5)+(25 if pd.notna(ma200.iloc[i]) and c.iloc[i]>ma200.iloc[i] else 10)+(25 if ma20.iloc[i]>ma50.iloc[i] else 5);mom=float(np.clip(rv.iloc[i],0,100)) if pd.notna(rv.iloc[i]) else 50;money=float(np.clip(50+(mf.iloc[i] if pd.notna(mf.iloc[i]) else 0)*100,0,100));liq=float(np.clip(v.iloc[i]/av.iloc[i]*50,0,100)) if pd.notna(av.iloc[i]) and av.iloc[i]>0 else 50;sup=float(c.tail(20).min());res=float(c.tail(60).max());stop=max(sup,float(c.iloc[i]-2*(atr.iloc[i] if pd.notna(atr.iloc[i]) else 0)));risk=c.iloc[i]-stop;rr=(res-c.iloc[i])/risk if risk>0 else np.nan
 return [sym,c.iloc[i],trend,mom,money,liq,c.pct_change(20).iloc[i],c.pct_change(60).iloc[i],av.iloc[i],sup,res,stop,rr]
def main():
 q=pd.read_csv(ROOT/"quality_universe.csv");sc=next(c for c in ["symbol","ticker","code"] if c in q);a=[]
 for s in q[sc].astype(str).str.upper().unique():
  x=calc(s)
  if x is not None:a.append(x)
 o=pd.DataFrame(a,columns=["symbol","close","trend_score","momentum_score","money_flow_score","liquidity_score","ret20","ret60","avg_volume20","support","resistance","stop","rr1"]);o["rs_score"]=o.ret20.rank(pct=True)*40+o.ret60.rank(pct=True)*60;o.to_csv(OUT,index=False,encoding="utf-8-sig");print("TECHNICAL V3.3",len(o));return o
if __name__=="__main__":main()
