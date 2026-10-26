"""Conservative financial-data audit. Does not establish accounting accuracy.

No missing value is replaced by zero; no investment score is created.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone
import pandas as pd

AMOUNT_FIELDS=('market_cap','free_cash_flow','quarterly_revenue','quarterly_net_income')
RATIO_FIELDS=('revenue_growth','earnings_growth','profit_margin','trailing_pe')

def numeric(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):
        return None

def parse_date(v):
    try:
        result=pd.to_datetime(v,utc=True,errors='coerce')
        return None if pd.isna(result) else result
    except (TypeError,ValueError,OverflowError):
        return None

def check_financial_row(row, now=None):
    now=pd.Timestamp(now or datetime.now(timezone.utc))
    flags=[]
    annual=parse_date(row.get('latest_annual_period_in_cached_metadata'))
    quarterly=parse_date(row.get('latest_quarterly_period_in_cached_metadata'))
    filed=parse_date(row.get('latest_quarterly_filing_date_in_cached_metadata'))
    checked=parse_date(row.get('fundamentals_last_checked_utc'))
    source=str(row.get('provenance_class') or '')
    if annual is not None and annual>now:flags.append('annual_period_in_future')
    if quarterly is not None and quarterly>now:flags.append('quarterly_period_in_future')
    if filed is not None and filed>now:flags.append('filing_date_in_future')
    if quarterly is not None and filed is not None and filed<quarterly:flags.append('filing_date_before_period_end')
    if checked is not None and checked>now+pd.Timedelta(days=1):flags.append('retrieval_date_in_future')
    if annual is not None and (now-annual).days>550:flags.append('annual_period_old')
    if quarterly is not None and (now-quarterly).days>240:flags.append('quarterly_period_old')
    if not source.startswith('SEC '):flags.append('sec_provenance_not_established')
    # Spot obvious unit or scaling problems; these are warnings, not repairs.
    for key in AMOUNT_FIELDS:
        val=numeric(row.get(key))
        if val is not None and abs(val)>1e18: flags.append(key+'_unit_or_scale_review')
    margin=numeric(row.get('profit_margin'))
    if margin is not None and abs(margin)>10:flags.append('profit_margin_scale_review')
    pe=numeric(row.get('trailing_pe'))
    if pe is not None and pe<=0:flags.append('nonpositive_pe_review')
    # Comparing multiple SEC tags and fiscal durations is not supported yet.
    has_metrics=numeric(row.get('available_financial_metrics')) or 0
    if has_metrics==0:return 'no_financial_data',flags
    if any(f for f in flags if f!='sec_provenance_not_established'):
        return 'manual_validation_needed',flags
    if 'sec_provenance_not_established' in flags:
        return 'unverified_provider_metadata',flags
    if annual is None and quarterly is None:
        return 'period_unknown',flags
    return 'basic_checks_passed_not_audited',flags

def prioritize_candidates(frame):
    """Research queue only, never a trading signal. Unchecked first; within equal
    retrieval priority prefer existing technical score, with stable ticker tie-break.
    """
    df=frame.copy()
    if 'score' not in df.columns:df['score']=pd.NA
    df['_research_priority_score']=pd.to_numeric(df.score,errors='coerce').fillna(-1)
    return df.sort_values(['_research_priority_score','ticker'],ascending=[False,True],kind='stable')
