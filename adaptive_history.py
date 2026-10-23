"""Research-only, conservative evaluation of stocks with short observed histories.

A limited-history stock never receives a full-history technical or investment score.
"""
import pandas as pd


def evaluate_short_history(close, volume, min_price=5.0, min_avg_volume=500_000):
    close = pd.to_numeric(pd.Series(close), errors='coerce').dropna()
    volume = pd.to_numeric(pd.Series(volume), errors='coerce').dropna()
    n = len(close)
    if n < 20:
        return None, 'insufficient_history_under_20'
    price = float(close.iloc[-1])
    avg_volume = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    if not (price >= min_price and avg_volume is not None and avg_volume >= min_avg_volume):
        return None, 'filtered_price_liquidity'
    tier = 'limited_60_200' if n < 201 and n >= 60 else 'early_20_59' if n < 60 else 'full_201_plus'
    if tier == 'full_201_plus':
        return None, 'use_full_indicators'
    sma50 = float(close.tail(50).mean()) if n >= 50 else None
    ret_1m = (price / float(close.iloc[-22]) - 1) * 100 if n >= 23 and close.iloc[-22] else None
    ret_3m = (price / float(close.iloc[-64]) - 1) * 100 if n >= 64 and close.iloc[-64] else None
    return {
        'history_tier': tier,
        'history_observations': n,
        'price': price,
        'avg_volume_20d': avg_volume,
        'sma50': sma50,
        'sma200': None,
        'return_1m': ret_1m,
        'return_3m': ret_3m,
        'return_6m': None,
        'trend': 'Limited history — 200-day SMA unavailable',
        'research_status': 'Limited technical history' if tier == 'limited_60_200' else 'Early-stage watchlist',
        'score': None,
        'momentum_score': None,
        'data_coverage': None,
        'investment_ready': 'No — research only',
    }, None
