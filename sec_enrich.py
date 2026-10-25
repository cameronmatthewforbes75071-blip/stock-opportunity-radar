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
    limit=min(75,max(0,int(os.getenv('RADAR_SEC_DAILY_LIMIT','50'))))
    if not agent or limit==0:
        print('SEC enrichment skipped: no configured contact or zero limit');return
    df['ticker']=df.ticker.astype(str)
    if 'sec_last_retrieved_utc' not in df: df['sec_last_retrieved_utc']=pd.NA
    if 'sec_quarterly_last_retrieved_utc' not in df: df['sec_quarterly_last_retrieved_utc']=pd.NA
    # V4.8: prioritize candidates not yet checked; do not repeatedly select
    # unsupported securities merely because their SEC provenance is missing.
    # Retrieval timestamps and reporting periods remain separate.
    now=datetime.now(timezone.utc)
    if 'sec_enrichment_last_attempt_utc' not in df: df['sec_enrichment_last_attempt_utc']=pd.NA
    if 'sec_enrichment_failure_reason' not in df: df['sec_enrichment_failure_reason']=pd.NA
    ann=pd.to_datetime(df.sec_last_retrieved_utc,utc=True,errors='coerce')
    qtr=pd.to_datetime(df.sec_quarterly_last_retrieved_utc,utc=True,errors='coerce')
    prior=pd.to_datetime(df.sec_enrichment_last_attempt_utc,utc=True,errors='coerce')
    # Missing both annual and quarterly provenance: priority, but previous
    # failures are cooldown-limited (7 days), even across workflow runs.
    previously_failed=prior.notna() & ann.isna() & qtr.isna()
    eligible=~(previously_failed & ((pd.Timestamp(now)-prior)<pd.Timedelta(days=7)))
    # Annual or quarterly gaps are preferred before fully enriched rows.
    missing_both=(ann.isna() & qtr.isna())
    missing_one=(ann.isna() ^ qtr.isna())
    # Never attempted precedes past failed attempts and partial coverage.
    priority=pd.Series(3,index=df.index)
    priority[missing_one]=2
    priority[missing_both]=1
    priority[missing_both & prior.isna()]=0
    oldest=prior.fillna(pd.Timestamp('1970-01-01',tz='UTC'))
    prioritized=(df.loc[eligible].assign(_priority=priority.loc[eligible],_last_attempt=oldest.loc[eligible])
                 .sort_values(['_priority','_last_attempt','ticker'],kind='stable')
                 .head(limit))
    selected_never_attempted=int(prior.loc[prioritized.index].isna().sum())
    deferred_failure_cooldown=int((~eligible).sum())
    successes=0; failures={}; quarterly_successes=0; full_successes=0; any_successes=0; selected_details=[]
    for idx,row in prioritized.iterrows():
        ticker=row['ticker']
        df.at[idx,'sec_enrichment_last_attempt_utc']=datetime.now(timezone.utc).isoformat(timespec='seconds')
        df.at[idx,'sec_enrichment_failure_reason']=pd.NA
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
        if annual or quarterly: any_successes+=1
        if annual and quarterly: full_successes+=1
        if not annual and not quarterly:
            df.at[idx,'sec_enrichment_failure_reason']=f'annual: {msg}; quarterly: {qmsg}'[:320]
        selected_details.append({'ticker':ticker, 'annual_success':bool(annual),
                                 'quarterly_success':bool(quarterly),
                                 'annual_result':'OK' if annual else str(msg)[:180],
                                 'quarterly_result':'OK' if quarterly else str(qmsg)[:180]})
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
      'any_sec_success':any_successes,'both_sec_success':full_successes,
      'no_sec_success':len(prioritized)-any_successes,
      'selected_never_attempted':selected_never_attempted,
      'deferred_recent_no_data_failures':deferred_failure_cooldown,
      'configured_daily_limit':limit,
      'selected_with_missing_both_sec':int(missing_both.loc[prioritized.index].sum()),
      'selected_with_missing_one_sec':int(missing_one.loc[prioritized.index].sum()),
      'annual_success_rate_pct':round(100*successes/len(prioritized),2) if len(prioritized) else 0.0,
      'quarterly_success_rate_pct':round(100*quarterly_successes/len(prioritized),2) if len(prioritized) else 0.0,
      'results_by_ticker':selected_details,
      'failures':failures,'note':'V4.9 SEC 50-candidate controlled throughput benchmark. Missing tags and missing SEC listing do not mean weak fundamentals. SEC retrieval timestamps are not filing dates; cached XBRL data are not point-in-time backtest data.'}
    (DATA/'sec_enrichment_status.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
