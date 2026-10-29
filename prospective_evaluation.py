"""V5.7 read-only prospective cohort and eligibility diagnostics.

No backfilled outcomes, no investment recommendations, no scoring changes.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
HORIZONS = (7, 30, 90)


def read_ledger(path: Path, columns):
    if not path.exists():
        raise FileNotFoundError(f'Missing tracking ledger: {path.name}')
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        if not path.read_bytes().strip():
            raise ValueError(f'Headerless tracking ledger: {path.name}')
        raise
    missing = set(columns).difference(df.columns)
    if missing:
        raise ValueError(f'{path.name} missing columns: {sorted(missing)}')
    return df


def ts(series):
    return pd.to_datetime(series, errors='coerce', utc=True)


def group_label(val):
    v = str(val or '').strip()
    return v if v else 'unknown_comparability'


def build(data_dir=DATA, today=None):
    data_dir = Path(data_dir)
    now = pd.Timestamp(today) if today is not None else pd.Timestamp.now(tz='UTC')
    now = now.tz_localize('UTC') if now.tzinfo is None else now.tz_convert('UTC')
    snapshots = read_ledger(data_dir/'prospective_score_snapshots.csv', ['ticker','run_date','reference_price_date','reference_price'])
    outcomes = read_ledger(data_dir/'prospective_price_outcomes.csv', ['ticker','run_date','horizon_days','observed_price_date'])
    spy = read_ledger(data_dir/'prospective_spy_observations.csv', ['run_date','price_date','spy_close'])
    audit_path = data_dir/'prospective_audit_status.json'
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else {}
    if audit.get('integrity_status') != 'PASS':
        raise ValueError('V5.6 integrity audit must PASS before publishing evaluation')
    duplicates = int(snapshots.duplicated(['ticker','run_date']).sum()) + int(outcomes.duplicated(['ticker','run_date','horizon_days']).sum())
    if duplicates:
        raise ValueError(f'Duplicate prospective tracking keys: {duplicates}')
    if snapshots.empty:
        raise ValueError('No prospective snapshots to evaluate')
    s = snapshots.copy()
    s['run_ts'] = ts(s['run_date'])
    s['price_ts'] = ts(s['reference_price_date'])
    s['baseline_spy'] = pd.to_numeric(s.get('spy_reference_price',pd.Series(index=s.index,dtype=str)), errors='coerce').gt(0)
    s['group'] = s.get('comparability_group',pd.Series(index=s.index,dtype=str)).map(group_label)
    o = outcomes.copy()
    if not o.empty:
        o['horizon_num'] = pd.to_numeric(o['horizon_days'],errors='coerce')
    rows=[]
    for horizon in HORIZONS:
        due = s['run_ts'] + pd.Timedelta(days=horizon)
        matured = due.notna() & (due <= now)
        keys = set()
        matched_spy_keys = set()
        if not o.empty:
            sub = o[o['horizon_num'].eq(horizon)]
            keys = set(zip(sub['ticker'],sub['run_date']))
            matched_spy_keys = set(zip(sub.loc[sub.get('benchmark_status',pd.Series(index=sub.index,dtype=str)).eq('matched_live_samples'),'ticker'], sub.loc[sub.get('benchmark_status',pd.Series(index=sub.index,dtype=str)).eq('matched_live_samples'),'run_date']))
        recorded = pd.Series(list(zip(s['ticker'],s['run_date'])),index=s.index).isin(keys)
        matched = pd.Series(list(zip(s['ticker'],s['run_date'])),index=s.index).isin(matched_spy_keys)
        for cohort,idx in [('all',s.index)]+[(g,s.index[s['group'].eq(g)]) for g in sorted(s['group'].unique())]:
            elig = matured.loc[idx]
            obs = recorded.loc[idx]
            n_eligible=int(elig.sum())
            n_recorded=int((elig & obs).sum())
            n_spy=int((elig & matched.loc[idx]).sum())
            rows.append(dict(horizon_calendar_days=horizon,comparability_group=cohort,snapshots=int(len(idx)),matured_eligible=n_eligible,pending_not_due=int((~elig).sum()),observed=n_recorded,matured_missing_observation=n_eligible-n_recorded,observations_with_spy=n_spy,baseline_spy_present=int(s.loc[idx,'baseline_spy'].sum()),completion_pct=round(100*n_recorded/n_eligible,2) if n_eligible else None))
    report=pd.DataFrame(rows)
    status=dict(run_utc=now.isoformat(),evaluation_version='5.7',snapshot_count=int(len(s)),outcome_count=int(len(o)),spy_ledger_entries=int(len(spy)),integrity_status=audit.get('integrity_status'),snapshot_comparability_groups={str(k):int(v) for k,v in s['group'].value_counts().items()},spy_baseline_present=int(s['baseline_spy'].sum()),spy_baseline_missing=int((~s['baseline_spy']).sum()),horizons={str(h):dict(report.loc[(report.horizon_calendar_days==h)&(report.comparability_group=='all')].iloc[0].dropna().to_dict()) for h in HORIZONS},notes='Prospective-only cohorts. Completion is observation coverage, not investment return. No retrospective benchmarks, scoring changes, total-return estimates, or demonstrated alpha.')
    return report, status


def main():
    report,status=build()
    report.to_csv(DATA/'prospective_evaluation.csv',index=False)
    (DATA/'prospective_evaluation_status.json').write_text(json.dumps(status,indent=2,default=str)+'\n')
    print(json.dumps(status,indent=2,default=str))

if __name__=='__main__': main()
