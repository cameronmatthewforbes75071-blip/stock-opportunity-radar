"""Stable snapshot freshness and data-source diagnostics. No network calls."""
import pandas as pd


def age_days(values, now=None):
    """Calendar days since supplied dates, using UTC; unknown -> nullable Int64."""
    now = pd.Timestamp.now(tz='UTC') if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize('UTC')
    dates = pd.to_datetime(values, utc=True, errors='coerce')
    return (now.normalize() - dates.dt.normalize()).dt.days.clip(lower=0).astype('Int64')


def add_freshness(df, now=None):
    out = df.copy()
    for label, source in [('Price age (days)', 'as_of'),
                          ('Scanned age (days)', 'last_scanned_utc'),
                          ('Fundamentals checked age (days)', 'fundamentals_last_checked_utc')]:
        if source in out:
            out[label] = age_days(out[source], now=now)
        else:
            out[label] = pd.Series(pd.NA, index=out.index, dtype='Int64')
    # Retrieval date is NOT statement financial period; make distinction visible in UI.
    return out
