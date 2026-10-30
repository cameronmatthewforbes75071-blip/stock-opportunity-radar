"""V5.9 read-only, prospective-only descriptive performance analytics.

Input ledgers and manifest are never modified. Raw close-to-close price changes
are NOT total returns or executable strategy performance. Benchmark-relative
numbers are suppressed until equity/SPY adjustment methods are verified.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).resolve().parent / 'data'
HORIZONS = (7, 30, 90)
DETAIL_COLUMNS = ['ticker','run_date','horizon_days','comparability_group','score_at_snapshot',
    'score_bucket','origin_price_date','observed_price_date','raw_price_change_pct',
    'benchmark_status','spy_price_change_pct','relative_to_spy_pct_points','validity_status']
SUMMARY_COLUMNS = ['horizon_days','comparability_group','score_bucket','observations',
    'distinct_tickers','distinct_snapshot_dates','mean_raw_price_change_pct',
    'median_raw_price_change_pct','min_raw_price_change_pct','max_raw_price_change_pct',
    'positive_change_pct','benchmark_matched_count','benchmark_comparison_status']

def load_csv(path, required):
    if not path.exists():
        raise FileNotFoundError(path.name)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        raise ValueError(f'Headerless input ledger: {path.name}')
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f'{path.name} missing columns: {sorted(missing)}')
    return df

def numeric(s):
    return pd.to_numeric(s, errors='coerce')

def build(data_dir=DATA, *, now=None):
    data_dir=Path(data_dir)
    audit=json.loads((data_dir/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS':
        raise ValueError('Tracking integrity audit must PASS')
    snapshots=load_csv(data_dir/'prospective_score_snapshots.csv',
                       ['ticker','run_date','research_score','reference_price'])
    outcomes=load_csv(data_dir/'prospective_price_outcomes.csv',
                      ['ticker','run_date','horizon_days','origin_price','observed_price','observed_price_date','raw_price_change_pct'])
    recovery=load_csv(data_dir/'prospective_comparability_recovery.csv',
                      ['ticker','run_date','recovered_comparability_group'])
    if snapshots.duplicated(['ticker','run_date']).any() or recovery.duplicated(['ticker','run_date']).any():
        raise ValueError('Duplicate snapshot or recovery keys')
    if outcomes.duplicated(['ticker','run_date','horizon_days']).any():
        raise ValueError('Duplicate prospective outcome keys')
    s=snapshots.merge(recovery, on=['ticker','run_date'],how='left',validate='one_to_one',indicator=True)
    if len(s)!=len(snapshots) or (s['_merge']!='both').any() or len(recovery)!=len(s):
        raise ValueError('Recovery coverage must match original snapshot keys exactly')
    s['score_num']=numeric(s['research_score'])
    s['group']=s['recovered_comparability_group'].replace('', 'unknown_comparability')
    if s['group'].isin(['unknown_comparability']).any():
        # Unknown snapshots can be represented but not score-ranked without verified inputs.
        pass
    # Rank only among peers available on the same original snapshot date and same group.
    s['score_bucket']='not_ranked'
    for (_,grp),index in s.groupby(['run_date','group']).groups.items():
        scores=s.loc[index,'score_num']
        valid=scores.dropna()
        if len(valid)<4 or grp=='unknown_comparability':
            continue
        # Rank ties together; describe as within-day *relative* tiers, not fixed score thresholds.
        ranks=valid.rank(pct=True,method='average')
        bucket=pd.Series(np.where(ranks>0.75,'top_quartile',
                      np.where(ranks<=0.25,'bottom_quartile','middle_half')),index=valid.index)
        s.loc[bucket.index,'score_bucket']=bucket
    o=outcomes.copy()
    details=pd.DataFrame(columns=DETAIL_COLUMNS)
    invalid_count=0
    if not o.empty:
        o=o.merge(s[['ticker','run_date','group','score_bucket','score_num','reference_price']],
            on=['ticker','run_date'],how='left',validate='many_to_one',indicator=True)
        if (o['_merge']!='both').any():
            raise ValueError('Outcome references an unknown original snapshot')
        o['hnum']=numeric(o['horizon_days'])
        o['p0']=numeric(o['origin_price'])
        o['p1']=numeric(o['observed_price'])
        o['return']=numeric(o['raw_price_change_pct'])
        due=pd.to_datetime(o['run_date'],utc=True,errors='coerce')+pd.to_timedelta(o['hnum'],unit='D')
        observed=pd.to_datetime(o['observed_price_date'],utc=True,errors='coerce')
        recomputed=(o['p1']/o['p0']-1)*100
        valid=o['hnum'].isin(HORIZONS)&o['p0'].gt(0)&o['p1'].gt(0)&o['return'].notna() & due.notna() & observed.notna()&(observed>=due)&(o['return']-recomputed).abs().le(0.00011)
        # A snapshot may not observe prices before its reference run.
        valid &= (pd.to_datetime(o['run_date'],utc=True,errors='coerce') <= observed)
        invalid_count=int((~valid).sum())
        if invalid_count:
            raise ValueError(f'{invalid_count} prospective outcome rows violate horizon/price consistency')
        details=pd.DataFrame({
            'ticker':o['ticker'],'run_date':o['run_date'],'horizon_days':o['hnum'].astype(int),
            'comparability_group':o['group'],'score_at_snapshot':o['score_num'],
            'score_bucket':o['score_bucket'],'origin_price_date':o.get('origin_price_date',''),
            'observed_price_date':o['observed_price_date'],'raw_price_change_pct':o['return'],
            'benchmark_status':o.get('benchmark_status',''),
            'spy_price_change_pct':numeric(o.get('spy_price_change_pct',pd.Series(index=o.index,dtype=str))),
            'relative_to_spy_pct_points':numeric(o.get('relative_to_spy_pct_points',pd.Series(index=o.index,dtype=str))),
            'validity_status':'valid_observation'
        })
    aggregates=[]
    if not details.empty:
        for (h,g,b), sub in details.groupby(['horizon_days','comparability_group','score_bucket'],dropna=False):
            v=sub.raw_price_change_pct
            aggregates.append(dict(horizon_days=int(h),comparability_group=str(g),score_bucket=str(b),
                observations=len(sub),distinct_tickers=sub.ticker.nunique(),distinct_snapshot_dates=sub.run_date.nunique(),
                mean_raw_price_change_pct=round(float(v.mean()),4),median_raw_price_change_pct=round(float(v.median()),4),
                min_raw_price_change_pct=round(float(v.min()),4),max_raw_price_change_pct=round(float(v.max()),4),
                positive_change_pct=round(float((v>0).mean()*100),2),
                benchmark_matched_count=int(sub.benchmark_status.eq('matched_live_samples').sum()),
                benchmark_comparison_status='WITHHELD_price_adjustment_unverified'))
    summary=pd.DataFrame(aggregates,columns=SUMMARY_COLUMNS)
    status={
        'version':'5.9','run_utc':pd.Timestamp.now(tz='UTC').isoformat() if now is None else str(now),
        'integrity_status':'PASS','snapshot_count':len(s),'outcome_count':len(outcomes),
        'validated_outcome_count':len(details),'invalid_outcome_count':invalid_count,
        'summary_cohort_count':len(summary),'classification_counts':{str(k):int(v) for k,v in s.group.value_counts().items()},
        'horizons':{str(h):{'observations':int(details.horizon_days.eq(h).sum()) if not details.empty else 0,
                  'cohorts_with_observations':int(summary.horizon_days.eq(h).sum()) if not summary.empty else 0}
                  for h in HORIZONS},
        'benchmark_relative_metrics':'WITHHELD_price_adjustment_unverified',
        'interpretation':'NO_FORWARD_PERFORMANCE_DATA' if details.empty else 'DESCRIPTIVE_RAW_PRICE_CHANGES_ONLY',
        'notes':'Immutable, prospective-only analysis. No outcomes are fabricated; no score edits or backfill. Score quartiles are within original run date and comparability group and are NOT trading signals. Multiple snapshots per ticker are dependent samples; do not interpret as independent trade results. Raw prices exclude dividends, corporate-action issues, spread, slippage, commissions and execution. No alpha claim.'
    }
    return details,summary,status

def main():
    details,summary,status=build()
    details.to_csv(DATA/'prospective_performance_details.csv',index=False)
    summary.to_csv(DATA/'prospective_performance_summary.csv',index=False)
    (DATA/'prospective_performance_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))

if __name__=='__main__':
    main()
