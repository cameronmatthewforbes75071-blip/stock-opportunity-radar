"""V4.7 candidate research coverage audit. No trading signals or return claims.

Run after sec_enrich.py. Retains source data unchanged; empty/invalid fields stay missing.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
METRICS = ('market_cap', 'revenue_growth', 'earnings_growth', 'profit_margin',
           'free_cash_flow', 'trailing_pe')
FINANCIAL_METRICS = ('revenue_growth','earnings_growth','profit_margin','free_cash_flow','trailing_pe')
QUARTERLY = ('quarterly_revenue','quarterly_net_income','quarterly_revenue_yoy_growth',
             'quarterly_earnings_yoy_growth','quarterly_profit_margin')


def number_present(value):
    val = pd.to_numeric(pd.Series([value]), errors='coerce').iloc[0]
    return bool(pd.notna(val) and pd.Series([val]).map(lambda x: abs(x) != float('inf')).iloc[0])


def date_age(value, now):
    ts = pd.to_datetime(value, utc=True, errors='coerce')
    if pd.isna(ts):
        return None
    return max(0, (pd.Timestamp(now) - ts).days)


def extract_report_period(row, key):
    raw = row.get(key)
    if raw is None or pd.isna(raw) or str(raw).strip() in ('', 'nan'):
        return ''
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        # SEC structure may be nested by metric; collect only ISO-like dates in values.
        found=[]
        def walk(x):
            if isinstance(x, dict):
                for y in x.values(): walk(y)
            elif isinstance(x, list):
                for y in x: walk(y)
            elif isinstance(x, str) and len(x)>=10:
                ts=pd.to_datetime(x[:10],errors='coerce')
                if pd.notna(ts) and x[:10][:4].isdigit(): found.append(str(ts.date()))
        walk(parsed)
        return max(found) if found else ''
    except (TypeError, ValueError):
        return ''


def audit_candidates(frame, now=None):
    now = now or datetime.now(timezone.utc)
    if frame.empty or 'ticker' not in frame.columns:
        return pd.DataFrame(), {'candidate_count': 0, 'with_any_financial_metric': 0,
            'with_all_five_financial_metrics': 0, 'with_sec_annual_provenance': 0,
            'with_sec_quarterly_provenance': 0, 'fundamental_check_older_than_90d': 0,
            'fundamental_check_date_missing': 0, 'financial_metric_coverage_pct': 0.0}
    # Don't overwrite original source columns; audit is a derived artifact.
    df=frame.copy().drop_duplicates('ticker', keep='last')
    records=[]
    for _,r in df.iterrows():
        present={k:number_present(r.get(k)) for k in METRICS}
        financial=sum(present[k] for k in FINANCIAL_METRICS)
        annual_age=date_age(r.get('sec_last_retrieved_utc'),now)
        quarterly_age=date_age(r.get('sec_quarterly_last_retrieved_utc'),now)
        checked_age=date_age(r.get('fundamentals_last_checked_utc'),now)
        annual_provenance=annual_age is not None
        quarterly_provenance=quarterly_age is not None
        # Do not imply SEC coverage from provider labels alone.
        if annual_provenance or quarterly_provenance:
            provenance='SEC annual and quarterly' if annual_provenance and quarterly_provenance else ('SEC annual' if annual_provenance else 'SEC quarterly')
        else:
            provenance='Non-SEC / not established'
        missing=[k for k in METRICS if not present[k]]
        annual_period=extract_report_period(r,'sec_periods_json')
        quarterly_period=extract_report_period(r,'sec_quarterly_periods_json')
        has_period=bool(annual_period or quarterly_period)
        period_reference=max(x for x in (annual_period,quarterly_period) if x) if has_period else ''
        period_age=date_age(period_reference,now) if has_period else None
        fresh_period=period_age is not None and period_age <= 460
        # Research readiness is data sufficiency, NOT an investment score.
        if financial>=3 and has_period and fresh_period:
            readiness='data_ready_for_manual_review'
        elif financial>0 or annual_provenance or quarterly_provenance:
            readiness='partial_financial_research'
        else:
            readiness='technical_discovery_only'
        pe_state=('numeric_reported' if present['trailing_pe'] else
                  'unavailable_or_not_meaningful_requires_review')
        records.append({
            'ticker':r['ticker'], 'company':r.get('company'), 'as_of':r.get('as_of'),
            'price':r.get('price'), 'score_existing':r.get('score'),
            'fundamentals_source':r.get('fundamentals_source'),
            'fundamentals_fetch_status':r.get('fundamentals_fetch_status'),
            'available_financial_metrics':financial,
            'research_readiness':readiness,
            'latest_known_financial_period':period_reference,
            'latest_known_financial_period_age_days':period_age,
            'financial_period_recent_460d':fresh_period,
            'pe_availability_interpretation':pe_state,
            'sec_reporting_class':r.get('sec_reporting_class',''),
            'sec_reporting_form':r.get('sec_reporting_form',''),
            'financial_metrics_possible':len(FINANCIAL_METRICS),
            'financial_coverage_pct':round(100*financial/len(FINANCIAL_METRICS),1),
            'market_cap_available':present['market_cap'],
            'revenue_growth_available':present['revenue_growth'],
            'earnings_growth_available':present['earnings_growth'],
            'profit_margin_available':present['profit_margin'],
            'free_cash_flow_available':present['free_cash_flow'],
            'trailing_pe_available':present['trailing_pe'],
            'quarterly_metrics_available':sum(number_present(r.get(k)) for k in QUARTERLY),
            'missing_metrics':'; '.join(missing),
            'fundamentals_last_checked_utc':r.get('fundamentals_last_checked_utc'),
            'fundamental_check_age_days':checked_age,
            'fundamental_check_older_than_90d':checked_age is not None and checked_age>90,
            'annual_sec_data_recorded':annual_provenance,
            'quarterly_sec_data_recorded':quarterly_provenance,
            'sec_annual_retrieved_age_days':annual_age,
            'sec_quarterly_retrieved_age_days':quarterly_age,
            'latest_annual_period_in_cached_metadata':extract_report_period(r,'sec_periods_json'),
            'latest_quarterly_period_in_cached_metadata':extract_report_period(r,'sec_quarterly_periods_json'),
            'latest_quarterly_filing_date_in_cached_metadata':extract_report_period(r,'sec_quarterly_filed_json'),
            'provenance_class':provenance,
            'quality_audit_label': ('financial_data_complete' if financial==5 else
                                    'financial_data_partial' if financial>=3 else
                                    'financial_data_sparse'),
        })
    out=pd.DataFrame(records).sort_values(['financial_coverage_pct','ticker'],ascending=[False,True])
    status={
        'candidate_count':len(out),
        'with_any_financial_metric':int((out.available_financial_metrics>0).sum()),
        'with_all_five_financial_metrics':int((out.available_financial_metrics==5).sum()),
        'with_sec_annual_provenance':int(out.annual_sec_data_recorded.sum()),
        'with_sec_quarterly_provenance':int(out.quarterly_sec_data_recorded.sum()),
        'with_quarterly_metrics':int((out.quarterly_metrics_available>0).sum()),
        'fundamental_check_older_than_90d':int(out.fundamental_check_older_than_90d.sum()),
        'fundamental_check_date_missing':int(out.fundamental_check_age_days.isna().sum()),
        'research_readiness_counts':out.research_readiness.value_counts().to_dict(),
        'financial_period_recent_460d_count':int(out.financial_period_recent_460d.sum()),
        'financial_period_missing_count':int(out.latest_known_financial_period.fillna('').eq('').sum()),
        'sec_reporting_class_counts':out.sec_reporting_class.fillna('unknown').replace('','unknown').value_counts().to_dict(),
        'financial_metric_coverage_pct':round(100*out.available_financial_metrics.sum()/(len(out)*len(FINANCIAL_METRICS)),2),
        'per_metric_coverage':{k:{'available_count':int(out[k+'_available'].sum()),
             'missing_count':int(len(out)-out[k+'_available'].sum()),
             'coverage_pct':round(100*out[k+'_available'].mean(),2)} for k in FINANCIAL_METRICS},
        'quarterly_filing_date_recorded':int(out.latest_quarterly_filing_date_in_cached_metadata.fillna('').ne('').sum()),
        'annual_reporting_period_recorded':int(out.latest_annual_period_in_cached_metadata.fillna('').ne('').sum()),
        'quarterly_reporting_period_recorded':int(out.latest_quarterly_period_in_cached_metadata.fillna('').ne('').sum()),
        'note':'V5.0 readiness is data sufficiency only, not an investment score. Coverage counts fields with numeric values, not financial accuracy or timeliness. SEC provenance uses saved retrieval timestamps; reported periods are separately extracted. Missing fields are not zero; no new investment score or performance claim.'
    }
    return out,status


def main():
    DATA.mkdir(exist_ok=True)
    source=DATA/'discovery.csv'
    if not source.exists():
        raise FileNotFoundError(f'{source} missing; scan and SEC enrich first')
    original=pd.read_csv(source,low_memory=False)
    report,status=audit_candidates(original)
    # Snapshot tracks only dated observations actually available in latest saved discovery rows.
    snapshot_columns=['ticker','as_of','price','score_existing','financial_coverage_pct']
    snapshot=report.reindex(columns=snapshot_columns).copy()
    snapshot['snapshot_created_utc']=datetime.now(timezone.utc).isoformat(timespec='seconds')
    snapshot['price_source_note']='Historical as_of from existing discovery row; not guaranteed current or point-in-time fundamentals'
    snapshot_file=DATA/'candidate_snapshots.csv'
    if snapshot_file.exists():
        previous=pd.read_csv(snapshot_file,low_memory=False)
        snapshot=pd.concat([previous,snapshot],ignore_index=True)
    snapshot=snapshot.drop_duplicates(['ticker','as_of'],keep='first')
    report.to_csv(DATA/'candidate_quality.csv',index=False)
    snapshot.to_csv(snapshot_file,index=False)
    payload={'run_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),**status}
    (DATA/'candidate_quality_status.json').write_text(json.dumps(payload,indent=2))
    print(json.dumps(payload,indent=2))

if __name__=='__main__':
    main()
