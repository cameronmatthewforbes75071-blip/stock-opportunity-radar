"""V6.1 read-only audit of recorded price provenance and internal consistency.

No historical bars are fetched, no split/dividend claim is made, and no existing
ledger is rewritten. Does NOT certify adjusted-price comparability.
"""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

DATA=Path(__file__).resolve().parent/'data'
SNAP_COLS=['ticker','run_date','reference_price_date','reference_price','spy_reference_run_date','spy_reference_price_date','spy_reference_price']
SPY_COLS=['run_date','price_date','spy_close']
OUT_COLS=['ticker','run_date','horizon_days','origin_price_date','observed_price_date','origin_price','observed_price']
REPORT_COLS=['ticker','run_date','reference_price_date','spy_reference_run_date','spy_reference_price_date','equity_price_positive','equity_date_valid','spy_baseline_state','spy_ledger_match','flags']

def load(path, cols):
    if not path.is_file(): raise FileNotFoundError(path.name)
    try: df=pd.read_csv(path,dtype=str,keep_default_na=False)
    except pd.errors.EmptyDataError: raise ValueError('Headerless CSV: '+path.name)
    missing=set(cols)-set(df.columns)
    if missing: raise ValueError(f'{path.name} missing columns {sorted(missing)}')
    return df

def price(value):
    try:
        n=float(value)
        return bool(pd.notna(n) and n>0 and n!=float('inf'))
    except (ValueError,TypeError): return False

def day(value):
    if not value: return None
    try:
        parsed=pd.to_datetime(value,errors='coerce',utc=True)
        return parsed.date() if pd.notna(parsed) else None
    except (ValueError,TypeError): return None

def validate(data_dir=DATA, *, now=None):
    p=Path(data_dir)
    audit=json.loads((p/'prospective_audit_status.json').read_text())
    if audit.get('integrity_status')!='PASS': raise ValueError('V5.6 audit must PASS before validation')
    snaps=load(p/'prospective_score_snapshots.csv',SNAP_COLS)
    spy=load(p/'prospective_spy_observations.csv',SPY_COLS)
    outcomes=load(p/'prospective_price_outcomes.csv',OUT_COLS)
    if (len(snaps)!=int(audit.get('snapshot_count',-1)) or len(spy)!=int(audit.get('spy_entries',-1)) or len(outcomes)!=int(audit.get('outcome_count',-1))):
        raise ValueError('Ledger counts differ from audited counts')
    for df,keys in ((snaps,['ticker','run_date']),(spy,['run_date']),(outcomes,['ticker','run_date','horizon_days'])):
        if df.duplicated(keys).any(): raise ValueError('Duplicate ledger keys')
    spy_by_day={r['run_date']:r for r in spy.to_dict('records')}
    rows=[]; issues=0; mismatches=0; baseline=0; absent=0; invalid=0
    for r in snaps.to_dict('records'):
        flags=[]
        run=day(r['run_date']); eq_date=day(r['reference_price_date']); eq_ok=bool(run and eq_date and eq_date<=run)
        eq_price=price(r['reference_price'])
        if not eq_ok: flags.append('equity_date_invalid')
        if not eq_price: flags.append('equity_price_invalid')
        has_spy=any(str(r[x]).strip() for x in ('spy_reference_run_date','spy_reference_price_date','spy_reference_price'))
        match='not_applicable'; state='missing_historical_baseline'
        if has_spy:
            baseline+=1; state='present'
            srun=r['spy_reference_run_date']; sdate=r['spy_reference_price_date']; sprice=r['spy_reference_price']
            ledger=spy_by_day.get(srun)
            if not srun or not sdate or not price(sprice): flags.append('spy_baseline_invalid')
            if not (day(srun) and day(sdate) and day(sdate)<=day(srun)): flags.append('spy_baseline_date_invalid')
            if srun!=r['run_date']: flags.append('spy_run_date_differs_from_snapshot')
            if ledger is None:
                match='ledger_missing'; flags.append('spy_ledger_missing')
            elif (ledger['price_date']!=sdate or ledger['spy_close']!=sprice):
                # An exact-string mismatch is diagnostic, not proof of a price difference.
                try: numerical_same=float(ledger['spy_close'])==float(sprice)
                except (ValueError,TypeError): numerical_same=False
                if numerical_same:
                    match='numeric_match_string_differs'
                    flags.append('spy_ledger_format_difference')
                else:
                    match='value_or_date_mismatch'; flags.append('spy_ledger_value_or_date_mismatch')
            else: match='exact_match'
        else: absent+=1
        if flags: issues+=1
        if match in ('ledger_missing','value_or_date_mismatch'): mismatches+=1
        if not eq_ok or not eq_price: invalid+=1
        rows.append({'ticker':r['ticker'],'run_date':r['run_date'],'reference_price_date':r['reference_price_date'],
            'spy_reference_run_date':r['spy_reference_run_date'],'spy_reference_price_date':r['spy_reference_price_date'],
            'equity_price_positive':eq_price,'equity_date_valid':eq_ok,'spy_baseline_state':state,
            'spy_ledger_match':match,'flags':';'.join(flags)})
    # Validate outcome cross-reference and date ordering, but never claim corporate-action verification.
    outcome_flag_count=0
    keys=set(zip(snaps.ticker,snaps.run_date))
    for o in outcomes.to_dict('records'):
        d0=day(o['origin_price_date']); d1=day(o['observed_price_date']); run=day(o['run_date'])
        try: h=int(o['horizon_days'])
        except (ValueError,TypeError): h=-1
        if ((o['ticker'],o['run_date']) not in keys or not d0 or not d1 or not run or h not in (7,30,90)
            or d1<(run+pd.Timedelta(days=h)) or not price(o['origin_price']) or not price(o['observed_price'])):
            outcome_flag_count+=1
    status={'version':'6.1','run_utc':str(now or pd.Timestamp.now(tz='UTC').isoformat()),
        'integrity_status':'PASS','snapshot_count':len(snaps),'outcome_count':len(outcomes),'spy_ledger_entries':len(spy),
        'snapshot_rows_with_diagnostic_flags':issues,'invalid_equity_price_or_date_rows':invalid,
        'spy_baseline_present':baseline,'spy_baseline_missing':absent,'spy_ledger_missing_or_mismatched':mismatches,
        'outcomes_failing_crosscheck':outcome_flag_count,
        'equity_data_source':'UNVERIFIED_from_recorded_snapshots',
        'equity_adjustment_method':'UNVERIFIED_not_recorded_per_snapshot',
        'spy_source_code_observation':'forward_tracking._fetch_spy requests yfinance download auto_adjust=True; not independent validation of returned prices',
        'spy_adjustment_method':'CODE_CONFIG_ONLY_not_verified_per_observation',
        'split_dividend_corporate_action_validation':'NOT_PERFORMED',
        'price_adjustment_comparability':'UNVERIFIED','benchmark_relative_results':'WITHHELD',
        'interpretation':'STRUCTURAL_DIAGNOSTICS_ONLY_NOT_PRICE_CERTIFICATION',
        'notes':'Read-only. Checks historical price/date/ledger consistency and flags potential issues. Does not fetch history, infer corporate actions, correct past values, or unlock benchmark relative returns. Numeric price comparisons exclude distribution/adjustment validation.'}
    return pd.DataFrame(rows,columns=REPORT_COLS),status

def main():
    result,status=validate()
    result.to_csv(DATA/'prospective_price_validation.csv',index=False)
    (DATA/'prospective_price_validation_status.json').write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))

if __name__=='__main__': main()
