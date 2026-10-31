"""V6.3 read-only readiness diagnostics; NEVER generate or repair outcomes."""
from __future__ import annotations
import csv
import json
from collections import Counter
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
HORIZONS = (7, 30, 90)
COLUMNS = ['ticker','run_date','horizon_days','due_date','reference_price_date','latest_price_date','outcome_price_date','readiness_status','reason','provenance_status']


def rows(path, required, *, optional=False):
    if optional and not path.exists():
        return []
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            if optional and not path.read_bytes().strip():
                return []
            raise ValueError(f'Missing CSV headers in {path.name}')
        if not set(required).issubset(reader.fieldnames):
            raise ValueError(f'Missing columns in {path.name}: {set(required)-set(reader.fieldnames)}')
        return list(reader)


def date_value(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def price_ok(value):
    try:
        v=float(value)
        return v > 0 and v < float('inf')
    except (ValueError, TypeError):
        return False


def keyed(items, columns, *, label):
    output={}
    for row in items:
        key=tuple(row.get(x,'') for x in columns)
        if key in output:
            raise ValueError(f'Duplicate {label} key {key}')
        output[key]=row
    return output


def build(base=DATA, *, now=None):
    base=Path(base)
    timestamp=now or datetime.now(timezone.utc)
    if isinstance(timestamp,str):
        timestamp=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
    if timestamp.tzinfo is None:
        raise ValueError('now must have a timezone')
    today=timestamp.astimezone(timezone.utc).date()
    audit=json.loads((base/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS':
        raise ValueError('V6.3 refuses non-PASS integrity audit')
    snaps=rows(base/'prospective_score_snapshots.csv',['ticker','run_date','reference_price_date','reference_price'])
    outcomes=rows(base/'prospective_price_outcomes.csv',['ticker','run_date','horizon_days','observed_price_date'],optional=True)
    discovery=rows(base/'discovery.csv',['ticker','as_of','price'])
    provenance=rows(base/'prospective_price_provenance.csv',['record_type','ticker','run_date','recorded_price','price_date'])
    spy=rows(base/'prospective_spy_observations.csv',['run_date','price_date','spy_close'])
    if len(snaps)!=int(audit.get('snapshot_count',-1)) or len(outcomes)!=int(audit.get('outcome_count',-1)) or len(spy)!=int(audit.get('spy_entries',-1)):
        raise ValueError('Ledger row counts disagree with audit')
    snap_by=keyed(snaps,['ticker','run_date'],label='snapshot')
    outcomes_by=keyed(outcomes,['ticker','run_date','horizon_days'],label='outcome')
    prov_by=keyed(provenance,['record_type','ticker','run_date'],label='provenance')
    # Discovery can legitimately repeat a ticker; use the last saved row.
    latest={r['ticker']:r for r in discovery}
    # Provenance is metadata, not an independent price-method certificate.
    for s in snaps:
        p=prov_by.get(('equity',s['ticker'],s['run_date']))
        if not p or p['recorded_price']!=s['reference_price'] or p['price_date']!=s['reference_price_date']:
            raise ValueError(f'Provenance mismatch/missing: {s["ticker"]}|{s["run_date"]}')
    details=[]
    for s in snaps:
        origin=date_value(s['run_date'])
        ref=date_value(s['reference_price_date'])
        if not origin or not ref or ref>origin or not price_ok(s['reference_price']):
            raise ValueError(f'Invalid audited snapshot origin: {s["ticker"]}|{s["run_date"]}')
        d=latest.get(s['ticker'])
        latest_date=date_value(d['as_of']) if d else None
        for h in HORIZONS:
            due=origin+timedelta(days=h)
            key=(s['ticker'],s['run_date'],str(h))
            outcome=outcomes_by.get(key)
            status=''; reason=''
            if outcome is not None:
                od=date_value(outcome['observed_price_date'])
                if not od or od<due or od>today:
                    status='INVALID_EXISTING_OUTCOME';reason='Observation date is outside allowed horizon'
                else:
                    status='OBSERVED';reason='Existing forward outcome saved'
            elif today<due:
                status='NOT_DUE';reason='Calendar horizon has not matured'
            elif d is None:
                status='DUE_MISSING_TICKER';reason='Ticker missing from current discovery'
            elif not latest_date or not price_ok(d['price']):
                status='DUE_INVALID_CURRENT_PRICE';reason='Latest discovery date or price invalid'
            elif latest_date>today:
                status='DUE_FUTURE_DATED_PRICE';reason='Price dated after current UTC date'
            elif latest_date>=today:
                status='DUE_SAME_DAY_BAR_UNCONFIRMED';reason='Same-day price not independently confirmed as completed'
            elif (today-latest_date).days>5:
                status='DUE_STALE_PRICE';reason='Last recorded price older than five calendar days'
            elif latest_date<due:
                status='DUE_PRICE_PREDATES_HORIZON';reason='Latest completed bar predates due date'
            else:
                status='DUE_POTENTIAL_MISSING_OUTCOME';reason='Recorded prior-day price passes simple freshness/date checks; investigate tracking run, no automatic backfill'
            details.append(dict(ticker=s['ticker'],run_date=s['run_date'],horizon_days=str(h),due_date=due.isoformat(),reference_price_date=s['reference_price_date'],latest_price_date=(d.get('as_of','') if d else ''),outcome_price_date=(outcome.get('observed_price_date','') if outcome else ''),readiness_status=status,reason=reason,provenance_status='METADATA_ONLY_UNVERIFIED'))
    counts=Counter(r['readiness_status'] for r in details)
    report={ 'version':'6.3','run_utc':timestamp.astimezone(timezone.utc).isoformat(), 'integrity_status':'PASS','snapshot_count':len(snaps),'outcome_count':len(outcomes),'evaluated_snapshot_horizons':len(details), 'status_counts':dict(sorted(counts.items())), 'horizons':{str(h):dict(Counter(r['readiness_status'] for r in details if r['horizon_days']==str(h))) for h in HORIZONS}, 'potential_missing_outcomes':counts['DUE_POTENTIAL_MISSING_OUTCOME'],'invalid_existing_outcomes':counts['INVALID_EXISTING_OUTCOME'], 'historical_snapshots_modified':0,'source_or_adjustment_verified':False,'interpretation':'READ_ONLY_OBSERVATION_READINESS_NOT_PERFORMANCE_CERTIFICATION', 'notes':'Uses recorded discovery dates/prices only. Same-day bar is conservatively unconfirmed. Calendar-day horizons may land on non-trading days. Flags are diagnostic and do not create outcomes or price backfills. Provenance/adjustments remain unverified.'}
    return details,report


def main():
    details,report=build()
    DATA.mkdir(exist_ok=True)
    target=DATA/'prospective_observation_readiness.csv'
    tmp=target.with_suffix('.csv.tmp')
    with tmp.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS)
        writer.writeheader();writer.writerows(details)
    tmp.replace(target)
    (DATA/'prospective_observation_readiness_status.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
