"""Daily incremental discovery, research-only. Not a complete exchange sweep per run."""
import io
import json
import os
from datetime import datetime, timezone, timedelta
import time
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd

from scanner import technical_indicators, fetch_live, score_candidate, categories
from quality import enrich_dataframe
from sec_fundamentals import fetch_sec_annual, fetch_sec_quarterly

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
UNIVERSE_URLS = {
    'nasdaq': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
    'other': 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
}


def read_universe():
    symbols = set()
    for kind, url in UNIVERSE_URLS.items():
        try:
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0 StockResearchRadar/2.0'})
            with urlopen(req, timeout=25) as response:
                contents = response.read().decode('utf-8', 'replace')
            df = pd.read_csv(io.StringIO(contents), sep='|', dtype=str, on_bad_lines='skip')
            ticker_col = 'Symbol' if kind == 'nasdaq' else 'ACT Symbol'
            if ticker_col not in df.columns:
                raise ValueError(f'Missing {ticker_col}')
            if 'Test Issue' in df.columns:
                df = df[df['Test Issue'].fillna('Y') == 'N']
            if 'ETF' in df.columns:
                df = df[df['ETF'].fillna('Y') == 'N']
            if 'Security Name' in df.columns:
                # Exclude many trust, preferred, warrants, rights and units; some uncommon securities may remain.
                excluded = r'(?i)(warrant|depositary|depositary share|preferred| preference | units?\b|right to|notes due|etf|exchange.traded fund)'
                df = df[~df['Security Name'].fillna('').str.contains(excluded, regex=True)]
            for symbol in df[ticker_col].dropna():
                symbol = symbol.strip().upper()
                if symbol and len(symbol) <= 7 and symbol.replace('-', '').isalpha() and '.' not in symbol and '$' not in symbol:
                    symbols.add(symbol)
        except Exception as ex:
            print(f'UNIVERSE SOURCE FAILED [{kind}]: {type(ex).__name__}: {ex}')
    if not symbols:
        # Never silently imply full-market coverage on fallback.
        from scanner import DEFAULT_UNIVERSE
        return sorted(DEFAULT_UNIVERSE), 'fallback-default-universe'
    return sorted(symbols), 'nasdaqtrader-symbol-directory (best-effort common-equity filter)'


def load_existing():
    path = DATA / 'discovery.csv'
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            pass
    return pd.DataFrame()


def batch_technicals(symbols):
    import yfinance as yf
    results, failures = [], []
    for i in range(0, len(symbols), 25):
        batch = symbols[i:i+25]
        try:
            raw = yf.download(batch, period='18mo', auto_adjust=True, progress=False, threads=False, group_by='ticker', timeout=30)
            for sym in batch:
                try:
                    if isinstance(raw.columns, pd.MultiIndex):
                        frame = raw[sym] if sym in raw.columns.get_level_values(0) else None
                    else:
                        frame = raw if len(batch) == 1 else None
                    if frame is None or frame.empty: raise ValueError('No price rows')
                    ind = technical_indicators(frame['Close'], frame['Volume'])
                    if not ind: raise ValueError('Less than 201 daily observations')
                    if ind['price'] < 5 or (ind['avg_volume_20d'] or 0) < 500000:
                        continue
                    results.append({'ticker':sym, 'company':sym, 'sector':'Unverified',
                         'market_cap':None, 'revenue_growth':None, 'earnings_growth':None,
                         'profit_margin':None,'free_cash_flow':None,'trailing_pe':None,
                         **ind,'as_of':str(frame['Close'].dropna().index[-1].date()),
                         'source':'Yahoo Finance via yfinance (unofficial)',
                         'fundamentals_checked':False})
                except Exception as ex:
                    failures.append((sym, str(ex)[:80]))
        except Exception as ex:
            failures.extend((sym, str(ex)[:80]) for sym in batch)
    return results, failures


def update_coverage_ledger(universe, source, selected, qualified, failures, run_time):
    """Track attempted vs qualified symbols independently of the candidate table.

    Records an attempt even if a provider rejects data. This is not confirmation of
    a successful price retrieval, and coverage never implies full fundamental review.
    """
    path = DATA / 'scan_coverage.csv'
    fields = ['ticker', 'last_attempt_utc', 'last_qualified_utc', 'last_result', 'attempt_count']
    if path.exists():
        try:
            prior = pd.read_csv(path, dtype={'ticker': str})
            prior = prior.drop_duplicates('ticker', keep='last')
            previous = {str(r['ticker']): r for r in prior.to_dict('records')}
        except Exception:
            previous = {}
    else:
        previous = {}
    universe_set = set(universe)
    previous = {ticker: row for ticker, row in previous.items() if ticker in universe_set}
    failed_symbols = {ticker for ticker, _ in failures}
    qualified_symbols = {row['ticker'] for row in qualified}
    for ticker in selected:
        old = previous.get(ticker, {})
        try:
            prior_attempts = int(float(old.get('attempt_count', 0)))
        except (ValueError, TypeError):
            prior_attempts = 0
        result = ('qualified' if ticker in qualified_symbols else
                  'price_failed_or_unusable' if ticker in failed_symbols else
                  'did_not_pass_price_liquidity')
        previous[ticker] = {
            'ticker': ticker,
            'last_attempt_utc': run_time,
            'last_qualified_utc': run_time if ticker in qualified_symbols else old.get('last_qualified_utc', ''),
            'last_result': result,
            'attempt_count': prior_attempts + 1,
        }
    frame = pd.DataFrame(sorted(previous.values(), key=lambda r: r['ticker']), columns=fields)
    frame.to_csv(path, index=False)
    attempted_count = len(previous)
    last_qualified = sum(r.get('last_result') == 'qualified' for r in previous.values())
    return {
        'universe_coverage_attempted': attempted_count,
        'universe_coverage_pct': round(100.0 * attempted_count / len(universe), 2),
        'last_results_qualified_count': last_qualified,
        'universe_coverage_unattempted': len(universe) - attempted_count,
        'coverage_scope': f'Attempted from current universe source: {source}; some requests may have failed',
    }


def select_retries(universe, regular, max_retries=20):
    """Retry last-run failures, excluding symbols already in the regular batch.
    Old failure rows remain eligible; an attempt is not counted as success.
    """
    path=DATA/'scan_coverage.csv'
    if max_retries<=0 or not path.exists(): return []
    try:
        ledger=pd.read_csv(path,dtype={'ticker':str})
        if not {'ticker','last_result','last_attempt_utc'}.issubset(ledger.columns): return []
        failed=ledger[ledger.last_result.eq('price_failed_or_unusable')].copy()
        failed=failed[failed.ticker.isin(set(universe)-set(regular))]
        failed=failed.sort_values(['last_attempt_utc','ticker'],na_position='first')
        return failed.ticker.head(max_retries).tolist()
    except (OSError,ValueError,KeyError) as ex:
        print('RETRY LEDGER UNAVAILABLE:',ex)
        return []


def main():
    universe, source = read_universe()
    batch_size = min(300, max(1, int(os.environ.get('RADAR_BATCH_SIZE', '200'))))
    cursor_file = DATA / 'cursor.json'
    try:
        cursor = int(json.loads(cursor_file.read_text()).get('cursor', 0))
    except (FileNotFoundError, ValueError, TypeError):
        cursor = 0
    start = cursor % len(universe)
    regular = [universe[(start+i) % len(universe)] for i in range(min(batch_size, len(universe)))]
    retry_limit = min(30,max(0,int(os.environ.get('RADAR_RETRY_LIMIT','20'))))
    retry_symbols=select_retries(universe,regular,retry_limit)
    selected=regular+retry_symbols
    batch, failed = batch_technicals(selected)
    # Enrich a few strongest preliminary technical candidates, but avoid large bursts of metadata requests.
    ranked = sorted(batch, key=lambda r:(r['trend'] == 'Bullish', r['return_3m'] or -999), reverse=True)
    existing = load_existing()
    old_lookup = {str(r['ticker']):r for r in existing.to_dict('records')} if not existing.empty else {}
    # Prefer high-momentum names that have no real fundamentals or haven't been checked recently.
    def needs_enrichment(r):
        old=old_lookup.get(r['ticker'], {})
        filled=sum(pd.notna(old.get(k)) for k in ('revenue_growth','earnings_growth','profit_margin','free_cash_flow','trailing_pe'))
        prev=pd.to_datetime(old.get('fundamentals_last_checked_utc'),utc=True,errors='coerce')
        expired=pd.isna(prev) or (pd.Timestamp.now(tz='UTC')-prev).days>=14
        return filled<3 or expired
    priority=[r['ticker'] for r in ranked if needs_enrichment(r)]
    limit=max(0,min(10,int(os.environ.get('RADAR_ENRICH_LIMIT','5'))))
    enrich_rows=[]; enrichment_failures=[]
    attempted_enrichments=[]
    for ticker in priority[:limit]:
        attempted_enrichments.append(ticker)
        # Independent failures should not terminate scanning or erase older fundamentals.
        success=pd.DataFrame()
        for attempt in range(2):
            success, errors=fetch_live([ticker])
            if not success.empty: break
            enrichment_failures.extend(errors)
            if attempt==0: time.sleep(2)
        if not success.empty: enrich_rows.extend(success.to_dict('records'))
        time.sleep(.4)
    enrichment_lookup={r['ticker']:r for r in enrich_rows}
    # SEC filings are authoritative for reported annual amounts; limit requests per run.
    sec_limit=max(0,min(10,int(os.environ.get('RADAR_SEC_LIMIT','0'))))
    sec_results={};sec_failures={};quarterly_results={};quarterly_failures={}
    if os.environ.get('RADAR_SEC_USER_AGENT','').strip():
        sec_choices=[r['ticker'] for r in ranked[:max(10,sec_limit*5)]]
        for ticker in sec_choices[:sec_limit]:
            result,message=fetch_sec_annual(ticker)
            if result: sec_results[ticker]=result
            else: sec_failures[ticker]=message
            quarterly,qmessage=fetch_sec_quarterly(ticker)
            if quarterly: quarterly_results[ticker]=quarterly
            else: quarterly_failures[ticker]=qmessage

    financial_keys=('company','sector','market_cap','revenue_growth','earnings_growth','profit_margin','free_cash_flow','trailing_pe')
    for r in batch:
        old=old_lookup.get(r['ticker'], {})
        # A technical-only scan must not wipe previously collected fundamentals.
        for k in financial_keys:
            if pd.isna(r.get(k)) or r.get(k) in ('Unverified','Unknown'):
                oldval=old.get(k)
                if oldval is not None and pd.notna(oldval): r[k]=oldval
        r['fundamentals_last_checked_utc']=old.get('fundamentals_last_checked_utc')
        r['fundamentals_source']=old.get('fundamentals_source', 'Unknown / historical')
        r['fundamentals_fetch_status']=old.get('fundamentals_fetch_status', 'Not requested this run')
        e=enrichment_lookup.get(r['ticker'])
        if e:
            for k in financial_keys:
                if k in e and e[k] is not None and pd.notna(e[k]): r[k]=e[k]
            r['fundamentals_last_checked_utc']=datetime.now(timezone.utc).isoformat(timespec='seconds')
            r['fundamentals_source']='Yahoo Finance via yfinance (unofficial)'
            r['fundamentals_fetch_status']='Succeeded (metadata returned; completeness varies)'
        if r['ticker'] in attempted_enrichments and not e:
            r['fundamentals_fetch_status']='Failed or no usable response (previous fundamentals retained)'
        # Overlay verified annual metrics only. Never overwrite metrics with nulls.
        sec=sec_results.get(r['ticker'])
        if sec:
            for key in ('revenue_growth','earnings_growth','profit_margin','free_cash_flow'):
                if key in sec['metrics']: r[key]=sec['metrics'][key]
            r['sec_periods_json']=json.dumps(sec['periods'],sort_keys=True)
            r['sec_tags_json']=json.dumps(sec['tags'],sort_keys=True)
            r['sec_last_retrieved_utc']=sec['sec_last_retrieved_utc']
            r['fundamentals_source']='SEC XBRL annual 10-K (reported metrics); Yahoo for any remaining fields'
            r['fundamentals_last_checked_utc']=sec['sec_last_retrieved_utc']
            r['fundamentals_fetch_status']='SEC annual metrics available; other fields may be Yahoo or missing'
        else:
            for key in ('sec_periods_json','sec_tags_json','sec_last_retrieved_utc'):
                r[key]=old.get(key)
        # Preserve the original annual fields and store 10-Q metrics separately.
        quarterly=quarterly_results.get(r['ticker'])
        if quarterly:
            for key,value in quarterly['metrics'].items(): r[key]=value
            r['sec_quarterly_periods_json']=json.dumps(quarterly['periods'],sort_keys=True)
            r['sec_quarterly_filed_json']=json.dumps(quarterly['filed_dates'],sort_keys=True)
            r['sec_quarterly_tags_json']=json.dumps(quarterly['tags'],sort_keys=True)
            r['sec_quarterly_last_retrieved_utc']=quarterly['sec_last_retrieved_utc']
        else:
            for key in ('quarterly_revenue','quarterly_net_income','quarterly_revenue_yoy_growth',
                        'quarterly_earnings_yoy_growth','quarterly_profit_margin',
                        'sec_quarterly_periods_json','sec_quarterly_filed_json','sec_quarterly_tags_json',
                        'sec_quarterly_last_retrieved_utc'):
                r[key]=old.get(key)
        r['fundamentals_checked']=sum(pd.notna(r.get(k)) for k in financial_keys[3:])>0
        r.update(score_candidate(r))
        r['categories'] = categories(r)
        r['last_scanned_utc'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    updated = pd.concat([existing, pd.DataFrame(batch)], ignore_index=True)
    if not updated.empty:
        updated = updated.drop_duplicates('ticker', keep='last')
        updated = enrich_dataframe(updated)
        updated = updated.sort_values(['fundamental_coverage_pct','score'], ascending=False)
        updated.to_csv(DATA / 'discovery.csv', index=False)
    # Keep cursor stationary if ALL price requests fail, allowing retry next time.
    advance = bool(batch) or len(failed) < len(selected)
    next_cursor = (start + len(regular)) % len(universe) if advance else start
    cursor_file.write_text(json.dumps({'cursor': next_cursor, 'universe_size': len(universe), 'source': source}, indent=2))
    coverage = update_coverage_ledger(universe, source, selected, batch, failed, datetime.now(timezone.utc).isoformat(timespec='seconds'))
    status = {'run_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      **coverage,
      'universe_source':source, 'universe_count':len(universe),
      'tickers_attempted':len(selected),'regular_tickers_attempted':len(regular),'retry_tickers_attempted':len(retry_symbols),'retry_symbols':retry_symbols,'qualifying_price_liquidity':len(batch),
      'failed_price_requests':len(failed),'fundamental_enrichments':len(enrichment_lookup),
      'fundamental_enrichment_failures':len(enrichment_failures),
      'fundamental_enrichment_attempts':len(attempted_enrichments),
      'fundamental_enrichment_failed_symbols':[s for s in attempted_enrichments if s not in enrichment_lookup],
      'fundamentals_provider':'SEC 10-K annual + 10-Q quarterly when configured; Yahoo Finance unofficial fallback',
      'sec_success_count':len(sec_results),'sec_failure_details':sec_failures,
      'sec_quarterly_success_count':len(quarterly_results),'sec_quarterly_failure_details':quarterly_failures,
      'sec_configured':bool(os.environ.get('RADAR_SEC_USER_AGENT','').strip()),
      'stored_candidates':len(updated), 'next_cursor':next_cursor,
      'batch_size_configured':batch_size,'retry_limit_configured':retry_limit, 'price_request_success_or_filtered':len(selected)-len(failed),
      'note':'Dedicated SEC enrichment runs separately after technical screening. Coverage counts attempted symbols, not reliable financial research or full daily market screening. Partial coverage; scan timestamps differ from underlying prices and financial-report periods; unavailable fields must not be treated as zero. No trading.'}
    (DATA / 'status.json').write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))

if __name__ == '__main__':
    main()
