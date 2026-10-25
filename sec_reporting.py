"""Diagnostic only: report SEC filing structure, never generate investment metrics."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
from urllib.request import Request, urlopen

FOREIGN_FORMS={'20-F','20-F/A','40-F','40-F/A','6-K','6-K/A'}
DOMESTIC_FORMS={'10-K','10-K/A','10-Q','10-Q/A'}

def classify_reason(reason: str) -> str:
    r=(reason or '').lower()
    if 'not present in sec company directory' in r:return 'not_in_sec_ticker_directory'
    if 'no usable' in r:return 'sec_facts_no_supported_metrics'
    if 'http error 429' in r or 'too many requests' in r:return 'provider_rate_limited'
    if 'http error' in r or 'timed out' in r or 'urlerror' in r:return 'provider_request_error'
    return 'other_or_unclassified'

def inspect_sec_filing_type(ticker: str, agent: str, cache_dir: Path) -> dict:
    """Best-effort SEC submissions lookup for a failed ticker, capped by caller.

    Uses cached SEC ticker directory, obtained via sec_fundamentals caching.
    Only recent submissions forms are inspected; foreign report availability does
    not guarantee supported XBRL tags or comparable metrics.
    """
    try:
        from sec_fundamentals import _cached, TICKERS
        mapping=_cached(TICKERS,agent,cache_dir/'company_tickers.json',24*7)
        items=[v for v in mapping.values() if str(v.get('ticker','')).upper()==str(ticker).upper()]
        if not items:return {'filing_type_class':'not_in_sec_ticker_directory','most_recent_relevant_form':'','diagnostic_status':'no_sec_ticker_match'}
        cik=int(items[0]['cik_str'])
        url=f'https://data.sec.gov/submissions/CIK{cik:010d}.json'
        data=_cached(url,agent,cache_dir/f'submissions_{cik}.json',24)
        recent=data.get('filings',{}).get('recent',{})
        forms=recent.get('form',[]) or []
        filed=recent.get('filingDate',[]) or []
        relevant=[(f,filed[i] if i<len(filed) else '') for i,f in enumerate(forms) if f in FOREIGN_FORMS or f in DOMESTIC_FORMS]
        if not relevant: cls='other_or_no_recent_supported_form'
        elif relevant[0][0] in FOREIGN_FORMS:cls='foreign_issuer_form_observed'
        else:cls='domestic_form_observed_but_metrics_missing'
        return {'filing_type_class':cls,'most_recent_relevant_form':relevant[0][0] if relevant else '',
                'most_recent_relevant_filing_date':relevant[0][1] if relevant else '', 'diagnostic_status':'ok'}
    except Exception as exc:
        return {'filing_type_class':'unknown_diagnostic_unavailable','most_recent_relevant_form':'',
                'diagnostic_status':f'{type(exc).__name__}: {str(exc)[:110]}'}
