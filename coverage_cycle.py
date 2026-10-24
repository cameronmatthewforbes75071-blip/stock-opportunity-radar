"""Coverage-cycle scheduling and reporting; does not imply full-market fundamental analysis."""
from pathlib import Path
import pandas as pd


def _ledger(path):
    path=Path(path)
    if not path.exists():
        return pd.DataFrame(columns=['ticker','last_attempt_utc','price_data_verified_utc'])
    try:
        df=pd.read_csv(path,dtype={'ticker':str}).drop_duplicates('ticker',keep='last')
        if 'ticker' not in df: raise ValueError('No ticker column')
        return df
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame(columns=['ticker','last_attempt_utc','price_data_verified_utc'])


def choose_regular_symbols(universe, path, batch_size, cursor=0):
    """First complete unique initial attempts; then refresh oldest attempted symbols.

    Does not select the same symbol twice within the same run. Keeps the original
    universe order for initial discovery, and rotates it with the stored cursor.
    """
    if not universe: raise ValueError('Empty stock universe')
    symbols=list(dict.fromkeys(universe))
    ledger=_ledger(path)
    known=set(ledger.ticker.dropna().astype(str)) & set(symbols)
    n=len(symbols)
    rotated=symbols[cursor % n:] + symbols[:cursor % n]
    unseen=[s for s in rotated if s not in known]
    count=min(max(1,int(batch_size)),n)
    picked=unseen[:count]
    if len(picked)<count:
        attempted=ledger[ledger.ticker.isin(known)].copy()
        attempted['parsed']=pd.to_datetime(attempted.get('last_attempt_utc'),utc=True,errors='coerce')
        last_attempt={str(r.ticker):r.parsed for r in attempted.itertuples()}
        # Missing timestamp first. Tie break by rotated position to avoid alphabetic bias.
        def key(s):
            ts=last_attempt.get(s, pd.NaT)
            return (int(ts.value) if pd.notna(ts) else -1, rotated.index(s))
        seen_order=sorted((s for s in rotated if s in known),key=key)
        picked.extend(seen_order[:count-len(picked)])
    return picked, {'unattempted_prior_to_run':len(unseen),
                    'new_discovery_symbols_selected':sum(s not in known for s in picked),
                    'refresh_symbols_selected':sum(s in known for s in picked),
                    'selection_policy':'unattempted-first, then oldest last attempt'}


def summarize_coverage_cycle(path, universe, expected_session=None):
    """Separate lifetime successful retrievals from the number screened today.

    Daily coverage is counted by UTC scan date, NOT assumed from candle dates.
    When a session date is known, verified rows must have been retrieved on or
    after that session date, but that is still not proof of valid current prices.
    """
    symbols=set(universe)
    df=_ledger(path)
    df=df[df.ticker.isin(symbols)].copy()
    attempted=set(df.ticker)
    verified=df.get('price_data_verified_utc',pd.Series(index=df.index,dtype='object'))
    verified_at=pd.to_datetime(verified,utc=True,errors='coerce')
    attempt_at=pd.to_datetime(df.get('last_attempt_utc',pd.Series(index=df.index,dtype='object')),utc=True,errors='coerce')
    today=pd.Timestamp.now(tz='UTC').date()
    attempted_today=int((attempt_at.dt.date==today).sum())
    verified_today=int((verified_at.dt.date==today).sum())
    on_or_after_session=None
    if expected_session:
        try:
            session_date=pd.Timestamp(expected_session).date()
            on_or_after_session=int((verified_at.dt.date>=session_date).sum())
        except (TypeError,ValueError):pass
    return {
        'universe_size':len(symbols),
        'lifetime_unique_attempted':len(attempted),
        'lifetime_unique_price_verified':int(verified_at.notna().sum()),
        'first_discovery_pass_attempted_all':len(attempted)==len(symbols),
        'remaining_never_attempted':len(symbols-attempted),
        'attempted_today_utc':attempted_today,
        'price_verified_today_utc':verified_today,
        'price_verified_at_or_after_expected_session':on_or_after_session,
        'expected_session_date':expected_session,
        'warning':'Today and session counters reflect retrieval timestamps, not full re-analysis or independent price accuracy.'
    }
