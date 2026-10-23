"""V3 quality gates and strategy signals. Research heuristics, not recommendations."""
import math
import pandas as pd

FUNDAMENTAL_FIELDS = ('revenue_growth','earnings_growth','profit_margin','free_cash_flow','trailing_pe')

def number(x):
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None

def assess(row):
    r = dict(row)
    values={k:number(r.get(k)) for k in FUNDAMENTAL_FIELDS}
    count=sum(v is not None for v in values.values())
    coverage=20*count
    enriched=count>=3 and values['revenue_growth'] is not None and values['profit_margin'] is not None
    price=number(r.get('price')); sma50=number(r.get('sma50')); sma200=number(r.get('sma200'))
    ret3=number(r.get('return_3m'))
    trend=price is not None and sma50 is not None and sma200 is not None and price>sma50>sma200
    result={'fundamental_fields_present':count,'fundamental_coverage_pct':coverage,
            'research_status':'Fundamentals reviewed (partial)' if enriched else 'Incomplete research',
            'growth_score':None,'value_score':None,'recovery_score':None,'momentum_score':None,
            'fundamental_missing_fields': ', '.join(k for k,v in values.items() if v is None)}
    if enriched:
        revenue=values['revenue_growth']; earnings=values['earnings_growth']; margin=values['profit_margin']; fcf=values['free_cash_flow']; pe=values['trailing_pe']
        if earnings is not None:
            # Absolute scale with explicit missingness gate; capped, not a return forecast.
            result['growth_score']=int(min(100,max(0,40 + 100*revenue + 70*earnings + 30*margin + (10 if fcf is not None and fcf>0 else 0))))
        if pe is not None and fcf is not None and pe>0:
            result['value_score']=int(max(0,min(100, 65 - pe*.7 + 30*margin + (15 if fcf>0 else -15))))
    if ret3 is not None and price is not None and sma50 is not None and sma200 is not None:
        # Technical ranking is independent of missing financial metadata.
        six=number(r.get('return_6m'))
        if six is not None:
            result['momentum_score']=int(max(0,min(100,30 + (20 if trend else 0) + min(25,max(-25,ret3))*.8 + min(20,max(-20,six))*.5 + (10 if str(r.get('breakout_3m')).lower()=='true' else 0))))
    # Recovery needs multi-quarter point-in-time fundamental data; do not manufacture scores.
    result['qualified_strategies']=', '.join(k.title() for k in ('growth','value','momentum') if result[f'{k}_score'] is not None) or 'Not qualified'
    result['investment_ready']='No — research only'
    return result

def enrich_dataframe(df):
    if df is None or df.empty:
        return df
    df=df.copy()
    meta=pd.DataFrame([assess(row) for row in df.to_dict('records')], index=df.index)
    for col in meta:
        df[col]=meta[col]
    return df
