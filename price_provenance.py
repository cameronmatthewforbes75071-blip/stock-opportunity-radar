"""V6.2 non-invasive price provenance sidecar.

Metadata is collected AFTER audit; it is not independent price verification.
Existing snapshots are never rewritten. Previous sidecar rows are immutable.
"""
from __future__ import annotations
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

DATA=Path(__file__).resolve().parent/'data'
COLUMNS=['record_type','ticker','run_date','price_date','recorded_price','metadata_captured_utc',
         'capture_relation','source_claim','adjustment_claim','verification_status','corporate_actions_status']


def _read(path):
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _csv_text(path):
    if not path.exists(): return []
    with path.open(newline='', encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise ValueError('Provenance schema mismatch; existing ledger must not be rewritten')
        return list(reader)


def build(data_dir=DATA, *, now=None):
    base=Path(data_dir)
    audit=json.loads((base/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS':
        raise ValueError('Audit must PASS before V6.2 provenance capture')
    snaps=_read(base/'prospective_score_snapshots.csv')
    spy=_read(base/'prospective_spy_observations.csv')
    for df, req, keys in [
        (snaps,['ticker','run_date','reference_price_date','reference_price'],['ticker','run_date']),
        (spy,['run_date','price_date','spy_close'],['run_date'])]:
        if any(c not in df for c in req): raise ValueError('Missing source ledger columns')
        if df.duplicated(keys).any(): raise ValueError('Duplicate source ledger keys')
    if len(snaps)!=int(audit.get('snapshot_count',-1)) or len(spy)!=int(audit.get('spy_entries',-1)):
        raise ValueError('Ledger counts mismatch audit')
    timestamp=str(now or datetime.now(timezone.utc).isoformat())
    today=timestamp[:10]
    target=base/'prospective_price_provenance.csv'
    old=_csv_text(target)
    old_keys=set()
    for r in old:
        k=(r['record_type'],r['ticker'],r['run_date'])
        if k in old_keys: raise ValueError('Duplicate sidecar keys')
        old_keys.add(k)
    source_records={('equity',r['ticker'],r['run_date']):(r['reference_price_date'],r['reference_price']) for r in snaps.to_dict('records')}
    source_records.update({('spy','SPY',r['run_date']):(r['price_date'],r['spy_close']) for r in spy.to_dict('records')})
    # Previous rows must still point to the originally recorded values; never repair silently.
    for r in old:
        key=(r['record_type'],r['ticker'],r['run_date'])
        if key not in source_records or source_records[key]!=(r['price_date'],r['recorded_price']):
            raise ValueError(f'Provenance source changed: {key}')
    added=[]
    for (kind,ticker,run), (pdate,value) in source_records.items():
        key=(kind,ticker,run)
        if key in old_keys: continue
        is_same_day=run==today
        added.append(dict(record_type=kind,ticker=ticker,run_date=run,price_date=pdate,
            recorded_price=value,metadata_captured_utc=timestamp,
            capture_relation='same_utc_date_as_run' if is_same_day else 'retrospective_metadata_only',
            source_claim='UNKNOWN_equity_upstream' if kind=='equity' else 'CODE_REQUEST_yfinance_SPY',
            adjustment_claim='UNKNOWN_not_stored' if kind=='equity' else 'CODE_REQUEST_auto_adjust_true',
            verification_status='UNVERIFIED',corporate_actions_status='NOT_CHECKED'))
    rows=old+added
    status={'version':'6.2','run_utc':timestamp,'integrity_status':'PASS',
            'snapshot_count':len(snaps),'spy_ledger_entries':len(spy),
            'provenance_rows':len(rows),'new_provenance_rows':len(added),
            'same_date_capture_new_rows':sum(r['capture_relation']=='same_utc_date_as_run' for r in added),
            'retrospective_metadata_new_rows':sum(r['capture_relation']=='retrospective_metadata_only' for r in added),
            'verified_price_source_rows':0,'verified_adjustment_rows':0,
            'equity_adjustment_method':'UNVERIFIED',
            'spy_adjustment_method':'CODE_CONFIG_NOT_INDEPENDENTLY_VERIFIED',
            'corporate_actions_validation':'NOT_PERFORMED',
            'benchmark_relative_results':'WITHHELD',
            'interpretation':'PROVENANCE_METADATA_ONLY_NOT_PRICE_CERTIFICATION',
            'notes':'Append-only sidecar; source snapshots and benchmark ledger untouched. Same-date capture is not proof of original source or adjustment. Existing historical records can only receive retrospective metadata; no price backfill.'}
    return rows,status


def main():
    rows,status=build()
    target=DATA/'prospective_price_provenance.csv'
    # Atomically replace sidecar only after all validation succeeds.
    temp=target.with_suffix('.csv.tmp')
    with temp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS)
        writer.writeheader(); writer.writerows(rows)
    temp.replace(target)
    (DATA/'prospective_price_provenance_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))

if __name__=='__main__': main()
