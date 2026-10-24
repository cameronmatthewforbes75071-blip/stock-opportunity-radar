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
    # V4.6: backfill attempted-but-never-verified symbols, then refresh oldest.
    # No repeat requests in a run.  Failed histories remain under retry cooldown
    # when last_result indicates a known failure; the separate retry queue owns them.
    backfill=[]
    if len(picked)<count:
        prior=ledger[ledger.ticker.isin(known)].copy()
        verified_at=pd.to_datetime(prior.get('price_data_verified_utc',pd.Series(index=prior.index,dtype='object')),utc=True,errors='coerce')
        prior['verified_at_parsed']=verified_at
        backfill_set=set(prior.loc[prior.verified_at_parsed.isna(),'ticker'].astype(str))
        # Avoid bypassing the normal cooldown and causing repeated requests for known failures.
        bad=set(prior.loc[prior.get('last_result',pd.Series(index=prior.index,dtype='object')).eq('price_failed_or_unusable'),'ticker'].astype(str))
        backfill=[s for s in rotated if s in backfill_set and s not in bad]
        backfill=backfill[:count-len(picked)]
        picked.extend(backfill)
    if len(picked)<count:
        attempted=ledger[ledger.ticker.isin(known)].copy()
        attempted['parsed']=pd.to_datetime(attempted.get('last_attempt_utc'),utc=True,errors='coerce')
        last_attempt={str(r.ticker):r.parsed for r in attempted.itertuples()}
        position={s:i for i,s in enumerate(rotated)}
        def key(s):
            ts=last_attempt.get(s, pd.NaT)
            return (int(ts.value) if pd.notna(ts) else -1, position[s])
        # Never re-select a symbol already picked or a known failed ticker that
        # remains managed by the dedicated cooldown-aware retry selection.
        failed_symbols=set(attempted.loc[attempted.get('last_result',pd.Series(index=attempted.index,dtype='object')).eq('price_failed_or_unusable'),'ticker'].astype(str))
        already=set(picked)
        fresh_order=sorted((s for s in rotated if s in known and s not in already and s not in failed_symbols),key=key)
        picked.extend(fresh_order[:count-len(picked)])
    return picked, {'unattempted_prior_to_run':len(unseen),
                    'new_discovery_symbols_selected':sum(s not in known for s in picked),
                    'verification_backfill_symbols_selected':len(backfill),
                    'verification_backfill_tickers':backfill,
                    'refresh_symbols_selected':sum(s in known for s in picked)-len(backfill),
                    'selection_policy':'unattempted-first, then unverified (excluding cooldown failures), then oldest attempted'}


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
    candle_dates=pd.to_datetime(df.get('last_price_candle_date',pd.Series(index=df.index,dtype='object')),errors='coerce')
    expected_price_date_count=None
    price_date_unknown_count=int((verified_at.notna() & candle_dates.isna()).sum())
    if expected_session:
        expected_price_date_count=int((verified_at.notna() & (candle_dates.dt.date == pd.Timestamp(expected_session).date())).sum())
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
        'price_candle_current_session_lifetime':expected_price_date_count,
        'price_candle_date_unknown_for_verified':price_date_unknown_count,
        'warning':'Current-session candle counter uses saved candle dates, not retrieval timestamps. Earlier verification rows may lack candle dates until refreshed. Neither confirms independent price accuracy.'
    }
