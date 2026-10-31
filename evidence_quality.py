"""V6.0 read-only evidence quality: dependent-sample view and price-method gate.

Never modifies snapshots, outcomes, audit manifest, or V5.9 reports.
The one-per-ticker view is DESCRIPTIVE only, not an unbiased significance test.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np

DATA=Path(__file__).resolve().parent/'data'
HORIZONS=(7,30,90)
COLS=['horizon_days','comparability_group','score_bucket','raw_observations','unique_tickers','unique_snapshot_dates','selected_one_per_ticker','excluded_repeated_ticker_observations','mean_raw_price_change_pct','median_raw_price_change_pct','positive_change_pct','interpretation']
SELECTED=['ticker','run_date','horizon_days','comparability_group','score_bucket','raw_price_change_pct','selection_rule']

def read_csv(path, columns):
    if not path.is_file(): raise FileNotFoundError(path.name)
    try: df=pd.read_csv(path,dtype=str,keep_default_na=False)
    except pd.errors.EmptyDataError: raise ValueError('Headerless file: '+path.name)
    miss=set(columns)-set(df.columns)
    if miss: raise ValueError(f'{path.name}: missing {sorted(miss)}')
    return df

def build(data_dir=DATA, *, now=None):
    p=Path(data_dir)
    audit=json.loads((p/'prospective_audit_status.json').read_text())
    perf=json.loads((p/'prospective_performance_status.json').read_text())
    if audit.get('integrity_status')!='PASS' or perf.get('integrity_status')!='PASS':
        raise ValueError('Integrity controls must PASS')
    if int(audit.get('outcome_count',-1))!=int(perf.get('outcome_count',-2)):
        raise ValueError('Audit/performance outcome counts disagree')
    snapshots=read_csv(p/'prospective_score_snapshots.csv',['ticker','run_date'])
    details=read_csv(p/'prospective_performance_details.csv',['ticker','run_date','horizon_days','comparability_group','score_bucket','raw_price_change_pct','validity_status'])
    if len(snapshots)!=int(perf.get('snapshot_count',-1)):
        raise ValueError('Snapshot count differs from performance status')
    if len(details)!=int(perf.get('validated_outcome_count',-1)):
        raise ValueError('Detail count differs from performance status')
    if details.duplicated(['ticker','run_date','horizon_days']).any():
        raise ValueError('Duplicate outcome observation keys')
    if not details.empty:
        if not details.validity_status.eq('valid_observation').all(): raise ValueError('Invalid detail status')
        details['horizon_days']=pd.to_numeric(details.horizon_days,errors='coerce')
        details['raw_price_change_pct']=pd.to_numeric(details.raw_price_change_pct,errors='coerce')
        dates=pd.to_datetime(details.run_date,errors='coerce',utc=True)
        if dates.isna().any() or details.horizon_days.isna().any() or not details.horizon_days.isin(HORIZONS).all() or not np.isfinite(details.raw_price_change_pct).all():
            raise ValueError('Invalid observation dates, horizons or returns')
        if details[['ticker','run_date','horizon_days']].isna().any().any() or details.ticker.eq('').any():
            raise ValueError('Missing observation identifier')
        keys=snapshots[['ticker','run_date']].drop_duplicates()
        joined=details.merge(keys,on=['ticker','run_date'],how='left',indicator=True,validate='many_to_one')
        if not joined._merge.eq('both').all(): raise ValueError('Details have unknown snapshot keys')
    selected=pd.DataFrame(columns=SELECTED)
    out=pd.DataFrame(columns=COLS)
    if not details.empty:
        # Selection is the earliest original run_date for each ticker/horizon.
        # We select globally BEFORE cohort grouping to avoid double counting tickers across buckets.
        details=details.sort_values(['ticker','horizon_days','run_date'],kind='stable')
        one=details.drop_duplicates(['ticker','horizon_days'],keep='first').copy()
        one['selection_rule']='earliest_observed_snapshot_per_ticker_per_horizon'
        selected=one[SELECTED].copy()
        rows=[]
        for (h,g,b),group in details.groupby(['horizon_days','comparability_group','score_bucket'],dropna=False):
            subset=one[(one.horizon_days==h)&(one.comparability_group==g)&(one.score_bucket==b)]
            values=subset.raw_price_change_pct
            rows.append(dict(horizon_days=int(h),comparability_group=g,score_bucket=b,
                raw_observations=len(group),unique_tickers=group.ticker.nunique(),unique_snapshot_dates=group.run_date.nunique(),
                selected_one_per_ticker=len(subset),excluded_repeated_ticker_observations=len(group)-len(subset),
                mean_raw_price_change_pct=round(float(values.mean()),4) if len(values) else '',
                median_raw_price_change_pct=round(float(values.median()),4) if len(values) else '',
                positive_change_pct=round(float((values>0).mean()*100),2) if len(values) else '',
                interpretation='DESCRIPTIVE_ONLY_NOT_INDEPENDENCE_PROOF'))
        out=pd.DataFrame(rows,columns=COLS)
    status={
        'version':'6.0','run_utc':pd.Timestamp.now(tz='UTC').isoformat() if now is None else str(now),
        'integrity_status':'PASS','snapshot_count':len(snapshots),
        'validated_outcome_count':len(details),'one_per_ticker_horizon_count':len(selected),
        'excluded_repeated_ticker_observations':len(details)-len(selected),
        'price_adjustment_validation':'UNVERIFIED',
        'spy_relative_results':'WITHHELD',
        'significance_claim':'WITHHELD_DEPENDENCE_AND_SAMPLE_SIZE',
        'horizons':{str(h):{'original_observations':int((details.horizon_days==h).sum()) if len(details) else 0,
                            'selected_unique_tickers':int((selected.horizon_days==h).sum()) if len(selected) else 0} for h in HORIZONS},
        'interpretation':'NO_FORWARD_PERFORMANCE_DATA' if not len(details) else 'EXPLORATORY_DESCRIPTIVE_ONLY',
        'notes':'Read-only one-per-ticker sensitivity view selects earliest observed snapshot per ticker and horizon before assigning score cohorts; no repeated ticker appears more than once within a horizon. Selection is observational and may favor earlier dates; it does not establish independent returns, eliminate market-date correlation, correct selection bias, or constitute statistical significance. Price adjustment/corporate actions remain unverified; SPY-relative comparisons withheld. No claims of alpha or executable trade P&L.'
    }
    return out,selected,status

def main():
    out,selected,status=build()
    out.to_csv(DATA/'prospective_evidence_quality.csv',index=False)
    selected.to_csv(DATA/'prospective_unique_ticker_observations.csv',index=False)
    (DATA/'prospective_evidence_quality_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))
if __name__=='__main__': main()
