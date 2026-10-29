"""V5.6 read-only validation of prospective ledgers; no retroactive returns.

Writes an append-only (by key) fingerprint manifest for NEW records; reports changes to
previously fingerprinted records. It NEVER changes the original tracking CSVs.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

DATA = Path(__file__).resolve().parent / 'data'
MANIFEST = DATA / 'prospective_audit_manifest.json'
REPORT = DATA / 'prospective_audit_status.json'
FILES = {
    'snapshots': ('prospective_score_snapshots.csv', ['ticker', 'run_date']),
    'outcomes': ('prospective_price_outcomes.csv', ['ticker', 'run_date', 'horizon_days']),
    'spy': ('prospective_spy_observations.csv', ['run_date']),
}

def read_table(path):
    if not path.exists():
        raise FileNotFoundError(f'Required prospective ledger missing: {path.name}')
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        if not path.read_bytes().strip():
            raise ValueError(f'Headerless empty ledger: {path.name}; run corrected forward_tracking.py')
        raise

def numeric(x):
    try:
        v = float(x)
        return v if pd.notna(v) else None
    except (TypeError, ValueError):
        return None

def date(x):
    v = pd.to_datetime(x, errors='coerce', utc=True)
    return None if pd.isna(v) else v.date()

def fingerprint(row):
    # Preserve exact stored field names and values; order CSV columns independently.
    payload=json.dumps({k:str(v) for k,v in sorted(row.items())},sort_keys=True,separators=(',',':'))
    return hashlib.sha256(payload.encode()).hexdigest()

def audit(data_dir=DATA, now=None):
    data_dir=Path(data_dir)
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz='UTC')
    if now.tzinfo is None: now=now.tz_localize('UTC')
    now=now.tz_convert('UTC')
    today=now.date()
    manifest_path=data_dir/MANIFEST.name
    old=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'version':1,'hashes':{}}
    if old.get('version')!=1 or not isinstance(old.get('hashes'),dict):
        raise ValueError('Unknown or malformed immutable fingerprint manifest; refusing reset')
    previous=old['hashes']
    updated={name: dict(previous.get(name,{})) for name in FILES}
    details={}
    violations=[]
    frames={}
    for name,(filename,keycols) in FILES.items():
        df=read_table(data_dir/filename)
        frames[name]=df
        absent=[c for c in keycols if c not in df.columns]
        if absent: raise ValueError(f'{filename}: missing key columns {absent}')
        keys=df[keycols].astype(str).agg('|'.join,axis=1)
        duplicates=int(keys.duplicated().sum())
        changed=[]; added=0; disappeared=sorted(set(updated[name])-set(keys))
        for key, rec in zip(keys, df.to_dict('records')):
            if not key.strip('|'): violations.append(f'{name}: blank key')
            h=fingerprint(rec)
            if key in updated[name]:
                if updated[name][key]!=h: changed.append(key)
            else:
                updated[name][key]=h
                added+=1
        if duplicates: violations.append(f'{name}: {duplicates} duplicate keys')
        if changed: violations.append(f'{name}: {len(changed)} previously fingerprinted rows changed')
        if disappeared: violations.append(f'{name}: {len(disappeared)} previously fingerprinted rows disappeared')
        details[name]={'rows':len(df),'new_fingerprints':added,'previous_fingerprints':len(previous.get(name,{})),
                       'duplicate_keys':duplicates,'changed_prior_rows':len(changed),'missing_prior_rows':len(disappeared),
                       'examples_changed':changed[:5],'examples_missing':disappeared[:5]}

    snaps=frames['snapshots']; outs=frames['outcomes']; spy=frames['spy']
    baseline_eligible=0; baseline_present=0; baseline_missing=0
    for rec in snaps.to_dict('records'):
        base=numeric(rec.get('spy_reference_price'))
        if base is not None and base>0: baseline_present+=1
        else: baseline_missing+=1
        if rec.get('run_date') and date(rec.get('run_date')): baseline_eligible+=1
    invalid_prices=0; invalid_dates=0; stale_snapshots=0; future_dates=0
    for rec in snaps.to_dict('records'):
        ref=numeric(rec.get('reference_price'))
        start=date(rec.get('run_date')); pricedate=date(rec.get('reference_price_date'))
        if ref is None or ref<=0: invalid_prices+=1
        if start is None or pricedate is None: invalid_dates+=1
        else:
            if pricedate>start or start>today: future_dates+=1
            if (start-pricedate).days>5: stale_snapshots+=1
    outcome_invalid=0; premature=0; spy_matched=0
    for rec in outs.to_dict('records'):
        base=numeric(rec.get('origin_price')); final=numeric(rec.get('observed_price'))
        start=date(rec.get('run_date')); end=date(rec.get('observed_price_date'))
        try: horizon=int(float(rec.get('horizon_days','')))
        except (ValueError,TypeError): horizon=-1
        if base is None or final is None or base<=0 or final<=0: outcome_invalid+=1
        if start is None or end is None or horizon not in (7,30,90) or (end and start and (end-start).days<horizon): premature+=1
        if rec.get('benchmark_status')=='matched_live_samples': spy_matched+=1
    spy_invalid=sum(1 for r in spy.to_dict('records') if not date(r.get('price_date')) or not date(r.get('run_date')) or (numeric(r.get('spy_close')) or 0)<=0)
    if invalid_prices or invalid_dates or future_dates or premature or outcome_invalid or spy_invalid:
        violations.append('Invalid or premature price/date data detected')
    # Stale source price is a warning: do not rewrite historical observations.
    result={
        'run_utc':now.isoformat(),'audit_version':'5.6','baseline_initialized_this_run':not manifest_path.exists(),
        'integrity_status':'FAIL' if violations else 'PASS', 'integrity_violations':violations,
        'ledger_details':details,'snapshot_count':len(snaps),'snapshot_spy_baseline_present':baseline_present,
        'snapshot_spy_baseline_missing':baseline_missing,'spy_baseline_coverage_pct':round(100*baseline_present/len(snaps),2) if len(snaps) else 0,
        'snapshot_invalid_prices':invalid_prices,'snapshot_invalid_dates':invalid_dates,'snapshot_future_or_out_of_order_dates':future_dates,
        'snapshot_stale_reference_price_gt_5_calendar_days':stale_snapshots,
        'outcome_count':len(outs),'outcome_invalid_prices':outcome_invalid,'outcome_premature_or_invalid_horizon':premature,
        'outcome_spy_matched_count':spy_matched,'outcome_spy_unmatched_count':len(outs)-spy_matched,
        'spy_entries':len(spy),'spy_invalid_rows':spy_invalid,
        'notes':'First manifest establishes integrity baseline only; it cannot verify unchanged historical data predating V5.6. No historical backfill or return claim. Benchmark price adjustments may differ from equity price adjustments.'}
    # Fail CLOSED without updating trusted manifest if previous records changed.
    (data_dir/REPORT.name).write_text(json.dumps(result,indent=2)+'\n')
    if not violations:
        manifest_path.write_text(json.dumps({'version':1,'hashes':updated},sort_keys=True,indent=2)+'\n')
    return result

if __name__=='__main__':
    report=audit()
    print(json.dumps(report,indent=2))
    if report['integrity_status']!='PASS': raise SystemExit('Prospective ledger integrity check failed; inspect report. Original CSVs untouched.')
