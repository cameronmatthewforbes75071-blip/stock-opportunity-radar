"""Dedicated, rate-limited SEC fundamentals pass over already-screened candidates."""
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from quality import enrich_dataframe
from scanner import score_candidate, categories
from sec_fundamentals import fetch_sec_annual, fetch_sec_quarterly

DATA=Path(__file__).resolve().parent/'data'

def main():
    csv=DATA/'discovery.csv'
    if not csv.exists():
        print('SEC: no candidate file yet'); return
    df=pd.read_csv(csv)
    if df.empty or 'ticker' not in df:
        print('SEC: no candidates yet');return
    agent=os.getenv('RADAR_SEC_USER_AGENT','').strip()
    limit=min(20,max(0,int(os.getenv('RADAR_SEC_DAILY_LIMIT','10'))))
    if not agent or limit==0:
        print('SEC enrichment skipped: no configured contact or zero limit');return
    df['ticker']=df.ticker.astype(str)
    if 'sec_last_retrieved_utc' not in df: df['sec_last_retrieved_utc']=pd.NA
    if 'sec_quarterly_last_retrieved_utc' not in df: df['sec_quarterly_last_retrieved_utc']=pd.NA
    # Stale/missing SEC rows first; never rank NA as successful research.
    ann=pd.to_datetime(df.sec_last_retrieved_utc,utc=True,errors='coerce')
    qtr=pd.to_datetime(df.sec_quarterly_last_retrieved_utc,utc=True,errors='coerce')
    latest=pd.concat([ann,qtr],axis=1).min(axis=1)
    prioritized=df.assign(_date=latest).sort_values('_date',na_position='first').head(limit)
    successes=0; failures={}; quarterly_successes=0
    for idx,row in prioritized.iterrows():
        ticker=row['ticker']
        annual,msg=fetch_sec_annual(ticker,agent)
        if annual:
            for k in ('revenue_growth','earnings_growth','profit_margin','free_cash_flow'):
                if k in annual['metrics']: df.at[idx,k]=annual['metrics'][k]
            for k,key in [('sec_periods_json','periods'),('sec_tags_json','tags')]:
                df.at[idx,k]=json.dumps(annual[key],sort_keys=True)
            df.at[idx,'sec_last_retrieved_utc']=annual['sec_last_retrieved_utc']
            df.at[idx,'fundamentals_source']='SEC XBRL annual 10-K; other fields may be unofficial or missing'
            df.at[idx,'fundamentals_last_checked_utc']=annual['sec_last_retrieved_utc']
            df.at[idx,'fundamentals_fetch_status']='SEC annual facts available'
            successes+=1
        else: failures[ticker]=msg
        quarterly,qmsg=fetch_sec_quarterly(ticker,agent)
        if quarterly:
            for k,v in quarterly['metrics'].items():df.at[idx,k]=v
            for k,key in [('sec_quarterly_periods_json','periods'),('sec_quarterly_filed_json','filed_dates'),('sec_quarterly_tags_json','tags')]:
                df.at[idx,k]=json.dumps(quarterly[key],sort_keys=True)
            df.at[idx,'sec_quarterly_last_retrieved_utc']=quarterly['sec_last_retrieved_utc']
            quarterly_successes+=1
        else: failures[ticker+' (quarterly)']=qmsg
        time.sleep(.25)
    # Recompute scores when financial metrics change. Never use missing data as zero.
    for idx in prioritized.index:
        record=df.loc[idx].to_dict()
        for k,v in score_candidate(record).items():df.at[idx,k]=v
        df.at[idx,'categories']=categories(record)
    df=enrich_dataframe(df)
    df.to_csv(csv,index=False)
    report={'run_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'attempted':len(prioritized),'annual_success':successes,'quarterly_success':quarterly_successes,
      'failures':failures,'note':'SEC job enriches saved candidates, not the full market. Cached XBRL filings are not point-in-time backtest data.'}
    (DATA/'sec_enrichment_status.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
