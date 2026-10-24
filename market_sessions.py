"""Market-session-aware daily-bar diagnostics for U.S. listed equity discovery.

Uses XNYS sessions, close times, weekends and exchange holidays; does not
assume that the request timestamp means the last data candle is current.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pandas as pd


def assess_candle_sessions(candle_dates, verified_symbols, now=None, settle_minutes=90):
    import exchange_calendars as xcals
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("Current time must be timezone-aware")
    clock = clock.astimezone(timezone.utc)
    cal = xcals.get_calendar('XNYS')
    today = pd.Timestamp(clock.date())
    session = cal.date_to_session(today, direction='previous')
    # Wait for the daily candle to be reasonably available after the session close.
    close = cal.session_close(session).to_pydatetime().astimezone(timezone.utc)
    if clock < close + timedelta(minutes=settle_minutes):
        session = cal.previous_session(session)
    expected = session.date().isoformat()
    expected_idx = cal.sessions.get_loc(session)
    buckets = {'current_session': 0, 'one_session_behind': 0,
               'two_or_more_sessions_behind': 0, 'future_or_unexpected': 0, 'missing': 0}
    stale, behind_one, unexpected, missing = [], [], [], []
    for symbol in sorted(set(verified_symbols)):
        value = candle_dates.get(symbol)
        if not value:
            buckets['missing'] += 1; missing.append(symbol); continue
        try:
            day = pd.Timestamp(str(value)).normalize()
            if day > session:
                buckets['future_or_unexpected'] += 1; unexpected.append(symbol); continue
            if not cal.is_session(day):
                # A date that isn't an exchange session is suspect, not fresh.
                buckets['future_or_unexpected'] += 1; unexpected.append(symbol); continue
            lag = expected_idx - cal.sessions.get_loc(day)
        except (ValueError, KeyError, IndexError, TypeError):
            buckets['future_or_unexpected'] += 1; unexpected.append(symbol); continue
        if lag == 0: buckets['current_session'] += 1
        elif lag == 1:
            buckets['one_session_behind'] += 1; behind_one.append(symbol)
        else:
            buckets['two_or_more_sessions_behind'] += 1; stale.append(symbol)
    if sum(buckets.values()) != len(set(verified_symbols)):
        raise AssertionError('Session freshness counts do not reconcile')
    return {'exchange_calendar': 'XNYS', 'expected_latest_completed_session': expected,
        'settlement_grace_minutes': settle_minutes,
        'verified_symbols_evaluated': len(set(verified_symbols)),
        'session_age_buckets': buckets, 'stale_two_plus_sessions_count': len(stale),
        'stale_two_plus_sessions_symbols': stale[:100],
        'one_session_behind_symbols': behind_one[:50],
        'unexpected_candle_date_symbols': unexpected[:50],
        'missing_candle_date_symbols': missing[:50],
        'note': 'XNYS session lag is a price-date diagnostic, not a proof of quote accuracy or an indicator that all prices have been adjusted correctly.'}
