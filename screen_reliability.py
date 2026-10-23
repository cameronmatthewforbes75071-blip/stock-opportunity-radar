"""V3.8 price-request error classification and retry policy (research only)."""
import re
import pandas as pd

# An absent price history is ambiguous: do not infer a delisting or bad symbol.
def classify_failure(message):
    msg = str(message).lower()
    if any(x in msg for x in ('less than 201', 'insufficient history')):
        return 'insufficient_history'
    if any(x in msg for x in ('429', 'rate limit', 'too many requests', 'timeout', 'timed out', 'connection', '502', '503', '504', '401', 'unauthorized', 'invalid crumb', 'jsondecodeerror')):
        return 'transient_or_provider'
    if any(x in msg for x in ('no price rows', 'no data found', 'possibly delisted', 'empty prices')):
        return 'no_usable_prices'
    return 'unclassified_error'

# Screening the exchange directories is not definitive security classification.
NON_COMMON_NAME_PATTERN = re.compile(r'(?:\bwarrants?\b|\bright(?:s)?\b|\bunits?\b|\bpreferred\b|\bpreference shares?\b|\bdepositary (?:shares?|receipts?)\b|\bnotes due\b|\bexchange.traded fund\b|\bETF\b|\bETN\b)',re.I)

def is_likely_common_equity(symbol, security_name):
    sym=str(symbol).strip().upper()
    name=str(security_name or '')
    if not sym or len(sym)>7 or not sym.replace('-','').isalpha() or '.' in sym or '$' in sym:
        return False
    if NON_COMMON_NAME_PATTERN.search(name):
        return False
    return True

def make_failure_record(ticker, reason):
    return {'ticker':ticker,'reason':str(reason)[:220], 'failure_class':classify_failure(reason)}

def retry_eligible(row, max_attempts=3):
    """Limit repeated Yahoo failures; user can reset by clearing single ticker ledger record."""
    failure=str(row.get('failure_class',''))
    result=str(row.get('last_result',''))
    try: attempts=int(float(row.get('attempt_count',0)))
    except (ValueError,TypeError): attempts=0
    # Historical rows without class can be retried until classified.
    return result=='price_failed_or_unusable' and failure not in ('insufficient_history',) and attempts < max_attempts
