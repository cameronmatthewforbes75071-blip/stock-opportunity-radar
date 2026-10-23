"""Daily incremental discovery, research-only. Not a complete exchange sweep per run."""
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd

from scanner import technical_indicators, fetch_live, score_candidate, categories

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


def main():
    universe, source = read_universe()
    batch_size = min(500, max(1, int(os.environ.get('RADAR_BATCH_SIZE', '100'))))
    cursor_file = DATA / 'cursor.json'
    try:
        cursor = int(json.loads(cursor_file.read_text()).get('cursor', 0))
    except (FileNotFoundError, ValueError, TypeError):
        cursor = 0
    start = cursor % len(universe)
    selected = [universe[(start+i) % len(universe)] for i in range(min(batch_size, len(universe)))]
    batch, failed = batch_technicals(selected)
    # Enrich a few strongest preliminary technical candidates, but avoid large bursts of metadata requests.
    ranked = sorted(batch, key=lambda r:(r['trend'] == 'Bullish', r['return_3m'] or -999), reverse=True)
    enriched, enrichment_failures = fetch_live([r['ticker'] for r in ranked[:min(5, len(ranked))]]) if ranked else (pd.DataFrame(), [])
    enrichment_lookup = {r['ticker']: r for r in enriched.to_dict('records')} if not enriched.empty else {}
    for r in batch:
        e = enrichment_lookup.get(r['ticker'])
        if e:
            r.update(e)
            r['fundamentals_checked'] = True
        r.update(score_candidate(r))
        r['categories'] = categories(r)
        r['last_scanned_utc'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    existing = load_existing()
    updated = pd.concat([existing, pd.DataFrame(batch)], ignore_index=True)
    if not updated.empty:
        updated = updated.drop_duplicates('ticker', keep='last').sort_values(['score', 'data_coverage'], ascending=False)
        updated.to_csv(DATA / 'discovery.csv', index=False)
    # Keep cursor stationary if ALL price requests fail, allowing retry next time.
    advance = bool(batch) or len(failed) < len(selected)
    next_cursor = (start + len(selected)) % len(universe) if advance else start
    cursor_file.write_text(json.dumps({'cursor': next_cursor, 'universe_size': len(universe), 'source': source}, indent=2))
    status = {'run_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
      'universe_source':source, 'universe_count':len(universe),
      'tickers_attempted':len(selected),'qualifying_price_liquidity':len(batch),
      'failed_price_requests':len(failed),'fundamental_enrichments':len(enrichment_lookup),
      'stored_candidates':len(updated), 'next_cursor':next_cursor,
      'note':'Incremental, partial coverage; scores are not return forecasts. Stored rows may be stale. No trading.'}
    (DATA / 'status.json').write_text(json.dumps(status, indent=2))
    print(json.dumps(status, indent=2))

if __name__ == '__main__':
    main()
