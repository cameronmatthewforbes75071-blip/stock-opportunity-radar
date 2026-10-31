"""V6.5 read-only release gate and precise compatibility conflict triage.

No external verification, historical price amendment, or silent reconciliation.
REVIEW_REQUIRED is an honest release state, not a workflow failure.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
FIELDS = ['priority','record_type','ticker','price_date','issue','run_dates','recorded_prices','absolute_difference','relative_difference_pct','previous_price_date','previous_price','current_price','step_pct','disposition']

def read_csv(path, fields):
    if not path.exists(): raise FileNotFoundError(path)
    with path.open(newline='',encoding='utf-8') as f:
        reader=csv.DictReader(f)
        if not reader.fieldnames or not set(fields).issubset(reader.fieldnames):
            raise ValueError(f'Invalid columns in {path.name}')
        return list(reader)

def as_number(s):
    try:
        v=float(s)
        return v if math.isfinite(v) and v>0 else None
    except (ValueError, TypeError): return None

def build(base=DATA, *, now=None):
    base=Path(base)
    audit=json.loads((base/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS': raise ValueError('Original ledger audit must PASS')
    comp=json.loads((base/'prospective_price_compatibility_status.json').read_text())
    if comp.get('integrity_status')!='PASS': raise ValueError('V6.4 compatibility status must PASS')
    snap=read_csv(base/'prospective_score_snapshots.csv',['ticker','run_date','reference_price_date','reference_price'])
    spy=read_csv(base/'prospective_spy_observations.csv',['run_date','price_date','spy_close'])
    diag=read_csv(base/'prospective_price_compatibility.csv', ['record_type','ticker','run_date','price_date','recorded_price','price_step_flag','previous_price_date','previous_price','step_pct'])
    if len(snap)!=int(audit.get('snapshot_count',-1)) or len(spy)!=int(audit.get('spy_entries',-1)):
        raise ValueError('Audit count mismatch')
    if len(diag)!=len(snap)+len(spy) or len(diag)!=int(comp.get('diagnostic_rows',-1)):
        raise ValueError('V6.4 diagnostic coverage mismatch')
    source={}
    for r in snap:
        k=('equity',r['ticker'],r['run_date']); v=(r['reference_price_date'],r['reference_price'])
        if k in source: raise ValueError('Duplicate equity key')
        source[k]=v
    for r in spy:
        k=('spy','SPY',r['run_date']);v=(r['price_date'],r['spy_close'])
        if k in source: raise ValueError('Duplicate SPY key')
        source[k]=v
    by_price_date=defaultdict(list); large=[]; flags=Counter(); keys=set()
    for r in diag:
        k=(r['record_type'],r['ticker'],r['run_date'])
        if k not in source or k in keys or source[k]!=(r['price_date'],r['recorded_price']):
            raise ValueError(f'Diagnostic row not matching audited source: {k}')
        keys.add(k); flags[r['price_step_flag']]+=1
        by_price_date[(r['record_type'],r['ticker'],r['price_date'])].append(r)
        if r['price_step_flag']=='LARGE_STEP_REVIEW': large.append(r)
    if keys!=set(source):raise ValueError('Diagnostic source keys incomplete')
    results=[]; cases=Counter(); review_keys=set()
    for (kind,ticker,date),group in sorted(by_price_date.items()):
        prices=sorted(set(r['recorded_price'] for r in group))
        if len(prices)<2: continue
        numeric=[as_number(x) for x in prices]
        if any(x is None for x in numeric): raise ValueError('Invalid price in diagnostic')
        lo,hi=min(numeric),max(numeric)
        d=hi-lo; relative=100*d/lo
        precision=all(math.isclose(numeric[0],x,rel_tol=1e-10,abs_tol=1e-8) for x in numeric[1:])
        issue='POSSIBLE_PRECISION_VARIATION' if precision else 'SAME_DATE_PRICE_DISAGREEMENT'
        cases[issue]+=1;review_keys.add((kind,ticker,date))
        results.append(dict(priority='LOW' if precision else 'HIGH',record_type=kind,ticker=ticker,price_date=date,issue=issue,
          run_dates=';'.join(sorted(set(r['run_date'] for r in group))),recorded_prices=';'.join(prices),
          absolute_difference=format(d,'.12g'),relative_difference_pct=format(relative,'.8f'),
          previous_price_date='',previous_price='',current_price='',step_pct='',
          disposition='PRESERVED_NOT_RECONCILED'))
    if len(review_keys)!=int(comp.get('conflicting_same_date_groups',-1)):
        raise ValueError('Conflict group count does not match V6.4')
    for r in large:
        results.append(dict(priority='HIGH',record_type=r['record_type'],ticker=r['ticker'],price_date=r['price_date'],
          issue='LARGE_DISTINCT_DATE_PRICE_STEP',run_dates=r['run_date'],recorded_prices=r['recorded_price'],
          absolute_difference='',relative_difference_pct='',previous_price_date=r['previous_price_date'],
          previous_price=r['previous_price'],current_price=r['recorded_price'],step_pct=r['step_pct'],
          disposition='PRESERVED_REQUIRES_INDEPENDENT_CORPORATE_ACTION_REVIEW'))
    if len(large)!=int(comp.get('large_price_steps_requiring_review',-1)):
        raise ValueError('Large-step count does not match V6.4')
    results.sort(key=lambda r:(r['priority']!='HIGH',r['record_type'],r['ticker'],r['price_date'],r['issue']))
    report=dict(version='6.5',run_utc=now or datetime.now(timezone.utc).isoformat(),integrity_status='PASS',
      release_status='REVIEW_REQUIRED' if results else 'LIMITED_RELEASE_UNVERIFIED_PRICE_METHOD',
      snapshots=len(snap),spy_entries=len(spy),diagnostic_rows=len(diag),
      same_date_conflicting_groups=sum(cases.values()),precision_variation_groups=cases['POSSIBLE_PRECISION_VARIATION'],
      material_price_disagreement_groups=cases['SAME_DATE_PRICE_DISAGREEMENT'],
      large_steps_to_review=len(large),total_review_items=len(results),
      high_priority_review_items=sum(r['priority']=='HIGH' for r in results),
      affected_tickers=sorted(set(r['ticker'] for r in results)),
      large_step_details=[dict(ticker=r['ticker'],record_type=r['record_type'],run_date=r['run_date'],price_date=r['price_date'],
                                previous_price_date=r['previous_price_date'],previous_price=r['previous_price'],
                                recorded_price=r['recorded_price'],step_pct=r['step_pct']) for r in large],
      unverified_price_adjustments=True,corporate_actions_independently_verified=False,
      spy_relative_metrics='WITHHELD',investment_performance_certified=False,
      original_source_files_modified=False,
      limitations='Observed same-date prices may differ for multiple reasons; no cause is assigned. Flags are not proof of splits, dividends or bad ticks. The same-date 1e-10 relative tolerance is a triage heuristic, not independent verification. Original snapshots, benchmarks, provenance and audit fingerprints are never rewritten. No profitability claim.')
    return results,report

def main():
    rows, report=build()
    DATA.mkdir(parents=True, exist_ok=True)
    path=DATA/'prospective_release_review.csv'; temp=path.with_suffix('.csv.tmp')
    with temp.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    temp.replace(path)
    (DATA/'prospective_release_validation_status.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
