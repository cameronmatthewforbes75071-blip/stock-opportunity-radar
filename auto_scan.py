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
from screen_reliability import classify_failure, is_likely_common_equity, retry_eligible, make_failure_record
from adaptive_history import evaluate_short_history
from coverage_validation import reconcile_run, validate_coverage_ledger
from market_sessions import assess_candle_sessions

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
            for _, record in df.iterrows():
                symbol = str(record.get(ticker_col, '')).strip().upper()
                if is_likely_common_equity(symbol, record.get('Security Name', '')):
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
    """Download symbols in chunks; bisect provider-wide exceptions to isolate failures.

    A successful HTTP response with per-symbol empty prices does NOT trigger a
    second download: these are likely delisted/new/unsupported symbols and repeated
    requests can amplify rate limiting. Counts are logical symbols, not HTTP calls.
    """
    import yfinance as yf
    results, failures, short_records, successful_price = [], [], [], set()
    candle_dates = {}
    chunk_size = max(5, min(50, int(os.environ.get('RADAR_DOWNLOAD_CHUNK', '30'))))
    telemetry = {'download_calls': 0, 'download_fallback_splits': 0,
                 'download_exception_count': 0, 'largest_chunk': 0,
                 'download_batches': 0, 'provider_exception_samples': []}

    def process_frame(sym, frame):
        try:
            if frame is None or frame.empty:
                raise ValueError('No price rows (missing ticker data, unsupported security, or provider returned empty)')
            close = pd.to_numeric(frame['Close'], errors='coerce').dropna()
            volume = pd.to_numeric(frame['Volume'], errors='coerce')
            if close.empty or not pd.notna(close.iloc[-1]):
                raise ValueError('No usable closing prices')
            if volume.dropna().empty:
                raise ValueError('No usable volume data')
            last_candle = pd.Timestamp(close.index[-1])
            if last_candle.tzinfo is None:
                last_candle = last_candle.tz_localize('UTC')
            else:
                last_candle = last_candle.tz_convert('UTC')
            candle_dates[sym] = last_candle.date().isoformat()
            ind = technical_indicators(frame['Close'], frame['Volume'])
            if not ind:
                short, short_reason = evaluate_short_history(frame['Close'], frame['Volume'])
                if short:
                    successful_price.add(sym)
                    short_records.append({'ticker': sym, 'company': sym, **short,
                        'as_of': str(close.index[-1].date()),
                        'source': 'Yahoo Finance via yfinance (unofficial)'})
                    return
                if short_reason == 'filtered_price_liquidity':
                    successful_price.add(sym)
                    return
                raise ValueError('Less than 20 daily observations')
            if ind['price'] < 5 or (ind['avg_volume_20d'] or 0) < 500000:
                successful_price.add(sym)
                return
            successful_price.add(sym)
            results.append({'ticker': sym, 'company': sym, 'sector': 'Unverified',
                'market_cap': None, 'revenue_growth': None, 'earnings_growth': None,
                'profit_margin': None, 'free_cash_flow': None, 'trailing_pe': None,
                **ind, 'as_of': str(close.index[-1].date()),
                'source': 'Yahoo Finance via yfinance (unofficial)',
                'fundamentals_checked': False})
        except Exception as exc:
            failures.append((sym, str(exc)[:160]))

    def download_batch(batch):
        telemetry['download_calls'] += 1
        telemetry['largest_chunk'] = max(telemetry['largest_chunk'], len(batch))
        try:
            raw = yf.download(batch, period='18mo', auto_adjust=True,
                              progress=False, threads=False, group_by='ticker', timeout=30)
        except Exception as exc:
            telemetry['download_exception_count'] += 1
            if len(telemetry['provider_exception_samples']) < 3:
                telemetry['provider_exception_samples'].append(type(exc).__name__ + ': ' + str(exc)[:120])
            if len(batch) > 1:
                telemetry['download_fallback_splits'] += 1
                midpoint = len(batch) // 2
                download_batch(batch[:midpoint])
                download_batch(batch[midpoint:])
            else:
                failures.append((batch[0], f'Provider request failed: {type(exc).__name__}: {str(exc)[:110]}'))
            return
        for sym in batch:
            frame = None
            if isinstance(raw.columns, pd.MultiIndex):
                frame = raw[sym] if sym in raw.columns.get_level_values(0) else None
            elif len(batch) == 1:
                frame = raw
            process_frame(sym, frame)

    for i in range(0, len(symbols), chunk_size):
        telemetry['download_batches'] += 1
        download_batch(symbols[i:i+chunk_size])
    successful_price.difference_update(t for t, _ in failures)
    return results, failures, short_records, successful_price, telemetry, candle_dates


def update_coverage_ledger(universe, source, selected, qualified, failures, run_time, short_records=None, successful_price=None):
    """Track attempted vs qualified symbols independently of the candidate table.

    Records an attempt even if a provider rejects data. This is not confirmation of
    a successful price retrieval, and coverage never implies full fundamental review.
    """
    path = DATA / 'scan_coverage.csv'
    fields = ['ticker', 'last_attempt_utc', 'last_qualified_utc', 'last_result', 'attempt_count', 'failure_class', 'failure_reason', 'price_data_verified_utc']
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
    failed_symbols = {ticker: reason for ticker, reason in failures}
    qualified_symbols = {row['ticker'] for row in qualified}
    short_tiers = {row['ticker']:row['history_tier'] for row in (short_records or [])}
    successful_price = set(successful_price or [])
    for ticker in selected:
        old = previous.get(ticker, {})
        try:
            prior_attempts = int(float(old.get('attempt_count', 0)))
        except (ValueError, TypeError):
            prior_attempts = 0
        result = ('qualified' if ticker in qualified_symbols else
                  short_tiers[ticker] if ticker in short_tiers else
                  'price_failed_or_unusable' if ticker in failed_symbols else
                  'did_not_pass_price_liquidity')
        previous[ticker] = {
            'ticker': ticker,
            'last_attempt_utc': run_time,
            'last_qualified_utc': run_time if ticker in qualified_symbols else old.get('last_qualified_utc', ''),
            'last_result': result,
            'attempt_count': prior_attempts + 1,
            'failure_class': classify_failure(failed_symbols[ticker]) if ticker in failed_symbols else '',
            'failure_reason': failed_symbols.get(ticker, ''),
            'price_data_verified_utc': run_time if ticker in successful_price else old.get('price_data_verified_utc', ''),
        }
    frame = pd.DataFrame(sorted(previous.values(), key=lambda r: r['ticker']), columns=fields)
    frame.to_csv(path, index=False)
    attempted_count = len(previous)
    last_qualified = sum(r.get('last_result') == 'qualified' for r in previous.values())
    verified_count = sum(pd.notna(row.get('price_data_verified_utc')) and str(row.get('price_data_verified_utc')).strip() not in ('', 'nan', 'None') for row in previous.values())
    return {
        'universe_coverage_attempted': attempted_count,
        'universe_coverage_pct': round(100.0 * attempted_count / len(universe), 2),
        'last_results_qualified_count': last_qualified,
        'universe_coverage_unattempted': len(universe) - attempted_count,
        'universe_price_data_verified': verified_count,
        'universe_price_data_verified_pct': round(100.0 * verified_count / len(universe), 2),
        'universe_price_data_unverified': len(universe) - verified_count,
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
        failed=failed[failed.apply(retry_eligible,axis=1)]
        # V4.3: repeated failures enter a 7-day cooldown. No historical ledger rows are deleted.
        def cooldown_elapsed(row):
            try:
                n = int(float(row.get('attempt_count', 0)))
                last = pd.to_datetime(row.get('last_attempt_utc'), utc=True, errors='coerce')
                days = 7 if n >= 3 else 1
                return pd.notna(last) and (pd.Timestamp.now(tz='UTC') - last) >= pd.Timedelta(days=days)
            except (TypeError, ValueError):
                return False
        failed=failed[failed.apply(cooldown_elapsed,axis=1)]
        failed=failed.sort_values(['last_attempt_utc','ticker'],na_position='first')
        return failed.ticker.head(max_retries).tolist()
    except (OSError,ValueError,KeyError) as ex:
        print('RETRY LEDGER UNAVAILABLE:',ex)
        return []


def main():
    universe, source = read_universe()
    batch_size = min(1200, max(1, int(os.environ.get('RADAR_BATCH_SIZE', '1000'))))
    cursor_file = DATA / 'cursor.json'
    try:
        cursor = int(json.loads(cursor_file.read_text()).get('cursor', 0))
    except (FileNotFoundError, ValueError, TypeError):
        cursor = 0
    start = cursor % len(universe)
    regular = [universe[(start+i) % len(universe)] for i in range(min(batch_size, len(universe)))]
    retry_limit = min(30,max(0,int(os.environ.get('RADAR_RETRY_LIMIT','20'))))
    retry_symbols=select_retries(universe,regular,retry_limit)
    # Counts only existing known failures that are temporarily held from retries.
    quarantine_count = 0
    try:
        prior_ledger = pd.read_csv(DATA / 'scan_coverage.csv')
        if {'last_result','attempt_count','last_attempt_utc'}.issubset(prior_ledger.columns):
            failed_prior=prior_ledger[prior_ledger.last_result.eq('price_failed_or_unusable')]
            for _, row in failed_prior.iterrows():
                attempts=pd.to_numeric(row.get('attempt_count'),errors='coerce')
                last=pd.to_datetime(row.get('last_attempt_utc'),utc=True,errors='coerce')
                if pd.notna(attempts) and attempts>=3 and pd.notna(last) and (pd.Timestamp.now(tz='UTC')-last)<pd.Timedelta(days=7):
                    quarantine_count+=1
    except (OSError, ValueError, pd.errors.ParserError):
        pass
    selected=regular+retry_symbols
    batch, failed, short_records, successful_price, download_stats, candle_dates = batch_technicals(selected)
    reconciled = reconcile_run(selected, batch, short_records, successful_price, failed, retry_symbols)
    # Calendar-day age is a diagnostic, not trading-day staleness: weekends and holidays matter.
    now_day = datetime.now(timezone.utc).date()
    candle_age = {ticker: max(0, (now_day - datetime.fromisoformat(day).date()).days)
                  for ticker, day in candle_dates.items() if ticker in successful_price}
    stale_threshold = max(1, int(os.environ.get('RADAR_STALE_CALENDAR_DAYS', '5')))
    stale_tickers = sorted(t for t, age in candle_age.items() if age > stale_threshold)
    session_health = assess_candle_sessions(candle_dates, successful_price, now=datetime.now(timezone.utc))
    candle_age_buckets = {'0_1_days':sum(v <= 1 for v in candle_age.values()),
                          '2_3_days':sum(2 <= v <= 3 for v in candle_age.values()),
                          '4_5_days':sum(4 <= v <= 5 for v in candle_age.values()),
                          '6_plus_days':sum(v >= 6 for v in candle_age.values())}
    # Report explicit terminal outcomes. If reconciliation fails, leave existing snapshots untouched.
    failed_map={ticker:reason for ticker,reason in failed}
    retry_recovered=[ticker for ticker in retry_symbols if ticker in successful_price]
    retry_failed=[ticker for ticker in retry_symbols if ticker not in successful_price]
    failure_types={}
    for _,reason in failed:
        key=classify_failure(reason)
        failure_types[key]=failure_types.get(key,0)+1
    pd.DataFrame([make_failure_record(ticker,reason) for ticker,reason in failed], columns=['ticker','reason','failure_class']).to_csv(DATA/'price_failures_last_run.csv',index=False)
    # Short-history stocks go to an explicitly UNRANKED watchlist, never to discovery.csv.
    short_path = DATA / 'limited_history.csv'
    short_df = pd.DataFrame(short_records)
    if short_path.exists():
        try:
            old_short = pd.read_csv(short_path)
            short_df = pd.concat([old_short, short_df], ignore_index=True)
        except (OSError, ValueError, pd.errors.ParserError):
            pass
    if not short_df.empty:
        short_df = short_df.drop_duplicates('ticker', keep='last').sort_values('ticker')
    short_df.to_csv(short_path, index=False)

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
    # Keep cursor stationary only when none of the newly selected symbols returned usable price data.
    advance = bool(set(regular) & successful_price)
    next_cursor = (start + len(regular)) % len(universe) if advance else start
    cursor_file.write_text(json.dumps({'cursor': next_cursor, 'universe_size': len(universe), 'source': source}, indent=2))
    coverage = update_coverage_ledger(universe, source, selected, batch, failed, datetime.now(timezone.utc).isoformat(timespec='seconds'), short_records, successful_price)
    validate_coverage_ledger(DATA / 'scan_coverage.csv', universe)
    status = {'run_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      **coverage,
      'universe_source':source, 'universe_count':len(universe),
      'tickers_attempted':len(selected),'regular_tickers_attempted':len(regular),'retry_tickers_attempted':len(retry_symbols),'retry_symbols':retry_symbols,'retry_recovered_count':len(retry_recovered),'retry_failed_count':len(retry_failed),'failure_types':failure_types,'qualifying_price_liquidity':len(batch),
      'failed_price_requests':len(failed), 'short_history_60_200_qualified':sum(r['history_tier']=='limited_60_200' for r in short_records), 'early_history_20_59_qualified':sum(r['history_tier']=='early_20_59' for r in short_records), 'limited_history_watchlist_size':len(short_df), 'fundamental_enrichments':len(enrichment_lookup),
      'fundamental_enrichment_failures':len(enrichment_failures),
      'fundamental_enrichment_attempts':len(attempted_enrichments),
      'fundamental_enrichment_failed_symbols':[s for s in attempted_enrichments if s not in enrichment_lookup],
      'fundamentals_provider':'SEC 10-K annual + 10-Q quarterly when configured; Yahoo Finance unofficial fallback',
      'sec_success_count':len(sec_results),'sec_failure_details':sec_failures,
      'sec_quarterly_success_count':len(quarterly_results),'sec_quarterly_failure_details':quarterly_failures,
      'sec_configured':bool(os.environ.get('RADAR_SEC_USER_AGENT','').strip()),
      'stored_candidates':len(updated), 'next_cursor':next_cursor,
      'batch_size_configured':batch_size,'retry_limit_configured':retry_limit, 'price_request_success_or_filtered':len(successful_price),
      'last_run_verified_price_symbols':reconciled['verified'],
      'last_run_verified_price_pct':reconciled['verified_pct'],
      'last_run_failed_price_symbols':reconciled['failed'],
      'last_run_outcome_counts':reconciled['outcomes'],
      'last_run_reconciliation_passed':True,
      'download_telemetry':download_stats,
      'benchmark_target_batch_size':batch_size,
      'retry_quarantine_cooldown_days':7,
      'retry_quarantined_prior_failures':quarantine_count,
      'price_candle_age_calendar_day_buckets':candle_age_buckets,
      'price_candle_stale_threshold_calendar_days':stale_threshold,
      'price_candle_stale_symbols':stale_tickers[:100],
      'price_candle_stale_count':len(stale_tickers),
      'price_candle_dates_recorded':len(candle_age),
      'market_session_freshness':session_health,
      'last_run_failure_details':[{'ticker':t,'reason':r,'failure_class':classify_failure(r)} for t,r in failed][:50],
      'last_run_verified_regular_symbols':len(set(regular) & successful_price),
      'last_run_unclassified_failure_symbols':reconciled['failed_symbols'],
      'last_run_filtered_or_unranked':len(successful_price)-len(batch),
      'last_run_verified_retries':len(retry_recovered),
      'note':'V4.4: 1000-symbol benchmark, seven-day cooldown after 3 failed attempts, XNYS session-aware candle diagnostics (plus separate calendar-day age);  per-provider exception fallback; every requested symbol has one terminal result and counts reconcile. Attempted coverage and verified-price coverage are separate. Historical V3.x attempts are not retroactively counted as verified. Universe directory is best-effort and not guaranteed complete. Provider data may be missing, stale, or throttled. SEC work is separate. No live trades.'}
    (DATA / 'status.json').write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))

if __name__ == '__main__':
    main()
