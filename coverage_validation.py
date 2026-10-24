"""Strict conservation-of-symbols checks for each automated scan."""
from collections import Counter
import pandas as pd


def reconcile_run(selected, qualified, short_records, successful_price, failures, retries):
    selected = list(selected)
    if len(selected) != len(set(selected)):
        raise ValueError('Duplicate symbols in selected batch; abort status publication')
    universe = set(selected)
    verified = set(successful_price)
    failed = {s for s, _ in failures}
    full = {r['ticker'] for r in qualified}
    short = {r['ticker'] for r in short_records}
    if not (verified | failed) <= universe:
        raise ValueError('Unexpected ticker in outcomes')
    if verified & failed:
        raise ValueError('Symbol simultaneously marked verified and failed')
    if full & short:
        raise ValueError('Symbol marked full-history and limited-history')
    if not (full | short) <= verified:
        raise ValueError('Qualified ticker without verified price history')
    unspecified = universe - verified - failed
    if unspecified:
        raise ValueError(f'Symbols without terminal outcome: {sorted(unspecified)[:10]}')
    filtered = verified - full - short
    n = len(selected)
    outcomes = {
        'full_history_qualified': len(full),
        'limited_history_qualified': len(short),
        'price_valid_filtered_out': len(filtered),
        'price_failed': len(failed),
    }
    if sum(outcomes.values()) != n:
        raise ValueError('Reconciliation did not sum to selected symbols')
    return {
        'verified': len(verified),
        'failed': len(failed),
        'verified_pct': round(100*len(verified)/n,2) if n else 0.0,
        'outcomes': outcomes,
        'failed_symbols': sorted(failed),
        'retry_verified': len(set(retries) & verified),
    }


def validate_coverage_ledger(path, universe):
    ledger = pd.read_csv(path, dtype={'ticker':str})
    if ledger.ticker.isna().any() or ledger.ticker.duplicated().any():
        raise ValueError('Coverage ledger has missing or duplicated ticker values')
    if not set(ledger.ticker).issubset(set(universe)):
        raise ValueError('Coverage ledger contains symbols outside current universe')
    if 'price_data_verified_utc' not in ledger.columns:
        raise ValueError('Missing verified-price column in coverage ledger')
    return True
