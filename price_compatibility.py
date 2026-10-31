"""V6.4 read-only corporate-action and price-method compatibility safeguards.

Flags anomalous observed price steps for review, NOT confirmed splits/dividends.
Historical prices, snapshots, outcomes, and benchmark are never rewritten.
"""
from __future__ import annotations
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
COLUMNS = ['record_type','ticker','run_date','price_date','recorded_price','method_status',
           'price_step_flag','previous_price_date','previous_price','step_pct','reason']

def read_csv(path, fields):
    if not path.exists(): raise FileNotFoundError(path)
    with path.open(newline='',encoding='utf-8') as f:
        r=csv.DictReader(f)
        if not r.fieldnames or not set(fields).issubset(r.fieldnames):
            raise ValueError(f'Invalid schema in {path.name}')
        return list(r)

def valid(v):
    try:
        n=float(v)
        return n if math.isfinite(n) and n>0 else None
    except (TypeError,ValueError): return None

def build(base=DATA, *, now=None):
    base=Path(base)
    audit=json.loads((base/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS': raise ValueError('Audit is not PASS')
    snaps=read_csv(base/'prospective_score_snapshots.csv',['ticker','run_date','reference_price_date','reference_price'])
    spy=read_csv(base/'prospective_spy_observations.csv',['run_date','price_date','spy_close'])
    provenance=read_csv(base/'prospective_price_provenance.csv',['record_type','ticker','run_date','price_date','recorded_price','verification_status','adjustment_claim'])
    if len(snaps)!=int(audit.get('snapshot_count',-1)) or len(spy)!=int(audit.get('spy_entries',-1)):
        raise ValueError('Ledger counts do not match audit')
    sources={}
    for item in snaps:
        k=('equity',item['ticker'],item['run_date'])
        if k in sources: raise ValueError('Duplicate snapshot key')
        sources[k]=(item['reference_price_date'],item['reference_price'])
    for item in spy:
        k=('spy','SPY',item['run_date'])
        if k in sources: raise ValueError('Duplicate SPY key')
        sources[k]=(item['price_date'],item['spy_close'])
    prov={}
    for item in provenance:
        k=(item['record_type'],item['ticker'],item['run_date'])
        if k in prov: raise ValueError('Duplicate provenance key')
        prov[k]=item
    if set(sources)!=set(prov): raise ValueError('Provenance coverage differs from audited sources')
    for k,(pdate,price) in sources.items():
        if (prov[k]['price_date'],prov[k]['recorded_price'])!=(pdate,price):
            raise ValueError('Provenance value mismatch: '+str(k))
    # Compare distinct price dates only. Repeated old bars on different run dates
    # must not be interpreted as new observations or price changes.
    by_name=defaultdict(list)
    for (kind,ticker,run),(pdate,price) in sources.items():
        by_name[(kind,ticker)].append((pdate,run,price))
    step_info={}
    checked_steps=0
    large_steps=0
    for name,items in by_name.items():
        unique={}
        for pdate,run,price in sorted(items,key=lambda x:(x[0],x[1])):
            if pdate in unique and unique[pdate]!=price:
                raise ValueError(f'Different prices for the same recorded price date: {name} {pdate}')
            unique[pdate]=price
        prev=None
        for pdate in sorted(unique):
            value=unique[pdate]
            v=valid(value)
            if v is None: raise ValueError(f'Invalid audited recorded price: {name} {pdate}')
            if prev is None:
                step_info[(name,pdate)]=('NO_PRIOR_DISTINCT_DATE','','','')
            else:
                pct=(v/prev[1]-1)*100
                flag='LARGE_STEP_REVIEW' if abs(pct)>=35 else 'NO_LARGE_STEP_DETECTED'
                checked_steps+=1
                if flag=='LARGE_STEP_REVIEW': large_steps+=1
                step_info[(name,pdate)]=(flag,prev[0],unique[prev[0]],format(pct,'.8f'))
            prev=(pdate,v)
    rows=[]
    for (kind,ticker,run),(pdate,price) in sorted(sources.items()):
        item=prov[(kind,ticker,run)]
        flag,prev_date,prev_price,percent=step_info[((kind,ticker),pdate)]
        # No source is independently verified by the V6.2 sidecar.
        # Even a future provenance assertion must not silently release returns.
        reason=('Price step >=35% across recorded distinct dates; could be market move, split, or data issue; independent review required' if flag=='LARGE_STEP_REVIEW'
                else 'Only recorded dates checked; no split/dividend feed or adjustment verification')
        rows.append(dict(record_type=kind,ticker=ticker,run_date=run,price_date=pdate,
            recorded_price=price,method_status='UNVERIFIED',price_step_flag=flag,
            previous_price_date=prev_date,previous_price=prev_price,step_pct=percent,reason=reason))
    timestamp=(now or datetime.now(timezone.utc).isoformat())
    report=dict(version='6.4',run_utc=str(timestamp),integrity_status='PASS',
        snapshot_count=len(snaps),spy_ledger_entries=len(spy),diagnostic_rows=len(rows),
        distinct_price_date_steps_checked=checked_steps,large_price_steps_requiring_review=large_steps,
        row_flags=dict(Counter(x['price_step_flag'] for x in rows)),
        equity_adjustment_verified=False,spy_adjustment_verified=False,
        corporate_actions_independently_checked=False,price_method_comparability='UNVERIFIED',
        return_metric_status='RAW_DESCRIPTIVE_ONLY_WITH_UNVERIFIED_ADJUSTMENTS',
        benchmark_relative_results='WITHHELD',
        interpretation='DIAGNOSTIC_ONLY_NO_PRICE_CERTIFICATION',
        notes='Read-only; 35% step threshold is a review heuristic, not a split detector. Repeated identical price dates are deduplicated for change detection. No historical backfill, modification, external data verification, or total return claim.')
    return rows,report

def main():
    rows,report=build()
    target=DATA/'prospective_price_compatibility.csv'
    temp=target.with_suffix('.csv.tmp')
    with temp.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS)
        writer.writeheader();writer.writerows(rows)
    temp.replace(target)
    (DATA/'prospective_price_compatibility_status.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
