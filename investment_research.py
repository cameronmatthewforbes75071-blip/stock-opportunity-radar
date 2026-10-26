"""V5.3 experimental research heuristics — NEVER trading advice or backtest evidence.

Requires existing research files. Does not mutate any source data. Missing values stay missing;
scored candidates have recent SEC period, >=3 metrics, and no manual validation flags.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def clamp(x, lo=0., hi=100.):
    return min(hi, max(lo, x))


def growth_score(revenue, earnings):
    """Revenue/earnings growth inputs are fractional ratios, e.g. 0.12 = 12%."""
    vals=[]
    r=num(revenue)
    if r is not None and abs(r)<=10: vals.append(clamp(50 + 120*r))
    e=num(earnings)
    if e is not None and abs(e)<=10: vals.append(clamp(50 + 60*e))
    return round(sum(vals)/len(vals),1) if vals else None


def health_score(margin, fcf):
    """FCF score measures positive/negative only, NOT sector-adjusted financial health."""
    vals=[]
    m=num(margin)
    if m is not None and abs(m)<=10: vals.append(clamp(50 + 150*m))
    cash=num(fcf)
    if cash is not None: vals.append(70. if cash>0 else (35. if cash==0 else 15.))
    return round(sum(vals)/len(vals),1) if vals else None


def valuation_score(pe):
    """Illustrative positive trailing P/E band, not industry-relative fair value."""
    x=num(pe)
    if x is None or x<=0 or x>300: return None
    return round(clamp(100 - max(0,x-10)*2.5),1)


def technical_score(score):
    x=num(score)
    return round(clamp(x),1) if x is not None and 0<=x<=100 else None


def score_row(row):
    """Do not emit a composite without SEC period, three financial fields and clean audit."""
    info={
        'growth':growth_score(row.get('revenue_growth'),row.get('earnings_growth')),
        'health':health_score(row.get('profit_margin'),row.get('free_cash_flow')),
        'valuation':valuation_score(row.get('trailing_pe')),
        'technical':technical_score(row.get('score')),
    }
    validation=str(row.get('financial_validation_status') or '')
    readiness=str(row.get('research_readiness') or '')
    available=num(row.get('available_financial_metrics')) or 0
    sec=str(row.get('provenance_class') or '').startswith('SEC ')
    period=str(row.get('financial_period_recent_460d')).strip().lower() in ('true','1','yes')
    eligible=(readiness=='data_ready_for_manual_review' and available>=3 and sec and period
              and validation=='basic_checks_passed_not_audited')
    weights={'growth':0.30,'health':0.30,'valuation':0.15,'technical':0.25}
    covered=sum(weights[k] for k,v in info.items() if v is not None)
    # Missing valuation remains missing and lowers coverage/confidence; never counts as zero.
    eligible=eligible and info['growth'] is not None and info['health'] is not None and info['technical'] is not None
    composite=round(sum(weights[k]*v for k,v in info.items() if v is not None)/covered,1) if eligible and covered else None
    financial_fields=int(available)
    info.update({'experimental_research_score':composite,
                 'scoring_status':'experimental_scored_not_validated' if composite is not None else 'unranked_insufficient_verified_data',
                 'scoring_coverage_pct':round(100*covered,1),
                 'data_confidence_tier':('SEC_period_basic_checks_only' if eligible else
                                         'not_sufficiently_verified'),
                 'financial_fields_present':financial_fields})
    return info


def build_scores(discovery, quality, now=None):
    if quality.empty or 'ticker' not in quality:
        return pd.DataFrame(), {'candidate_count':0,'experimental_scored_count':0,'unranked_count':0}
    q=quality.copy().drop_duplicates('ticker',keep='last')
    needed=['ticker','revenue_growth','earnings_growth','profit_margin','free_cash_flow','trailing_pe','score']
    if discovery.empty or 'ticker' not in discovery:
        d=pd.DataFrame({'ticker':q.ticker})
    else:
        d=discovery.drop_duplicates('ticker',keep='last')
    d=d[[k for k in needed if k in d.columns]]
    for col in needed[1:]:
        if col in q.columns: q=q.drop(columns=[col])
    joined=q.merge(d,on='ticker',how='left',validate='one_to_one')
    scoring=pd.DataFrame([score_row(r) for r in joined.to_dict('records')])
    out=pd.concat([joined[['ticker']].reset_index(drop=True), scoring],axis=1)
    out=out.sort_values(['experimental_research_score','ticker'],ascending=[False,True],na_position='last')
    scored=out.experimental_research_score.notna()
    status={'run_utc':(now or datetime.now(timezone.utc)).isoformat(),
            'candidate_count':len(out),'experimental_scored_count':int(scored.sum()),
            'unranked_count':int((~scored).sum()),
            'with_all_four_components':int(out[['growth','health','valuation','technical']].notna().all(axis=1).sum()),
            'note':'Experimental unvalidated relative research heuristic, not investment advice, fair value, or tested alpha. Only SEC period plus basic data checks required. No point-in-time validation, sector normalization, or backtest. Missing inputs are excluded rather than treated as zero.'}
    return out,status


def main():
    discovery=pd.read_csv(DATA/'discovery.csv',low_memory=False) if (DATA/'discovery.csv').exists() else pd.DataFrame()
    quality=pd.read_csv(DATA/'candidate_quality.csv',low_memory=False) if (DATA/'candidate_quality.csv').exists() else pd.DataFrame()
    result,status=build_scores(discovery,quality)
    DATA.mkdir(parents=True,exist_ok=True)
    result.to_csv(DATA/'investment_research_scores.csv',index=False)
    (DATA/'investment_research_status.json').write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(status,indent=2))

if __name__=='__main__': main()
