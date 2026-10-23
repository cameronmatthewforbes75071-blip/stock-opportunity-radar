"""Research-only stock radar; no order execution or predictive performance claims."""
from datetime import datetime, timezone
import math
import pandas as pd

DEFAULT_UNIVERSE = [
    'AAPL','MSFT','NVDA','AMZN','GOOGL','META','AVGO','AMD','CRM','ORCL',
    'PLTR','UBER','SHOP','SNOW','CRWD','PANW','NET','DDOG','SOFI','HOOD',
    'TSLA','DELL','MU','AMAT','LRCX','COST','WMT','JPM','V','UNH'
]

def valid_number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None

def technical_indicators(close, volume):
    close = pd.Series(close).dropna().astype(float)
    volume = pd.Series(volume).dropna().astype(float)
    if len(close) < 201:
        return None
    price = float(close.iloc[-1]); sma50 = float(close.tail(50).mean()); sma200 = float(close.tail(200).mean())
    prior_63 = float(close.iloc[-64]) if len(close) >= 64 else None
    prior_126 = float(close.iloc[-127]) if len(close) >= 127 else None
    high_63 = float(close.iloc[-64:-1].max()) if len(close) >= 64 else None
    avg_volume = float(volume.tail(20).mean()) if len(volume) >= 20 else None
    return {'price':price,'sma50':sma50,'sma200':sma200,
            'return_3m':(price / prior_63 - 1)*100 if prior_63 else None,
            'return_6m':(price / prior_126 - 1)*100 if prior_126 else None,
            'breakout_3m':bool(high_63 is not None and price > high_63),
            'avg_volume_20d':avg_volume,
            'trend':'Bullish' if price > sma50 > sma200 else ('Mixed' if price > sma200 else 'Weak')}

def score_candidate(row):
    """Transparent heuristic, NOT a probability or investment recommendation.
    Missing fundamentals remain unscored; category-specific quality signals.
    """
    trend = row.get('trend')
    tech = (20 if trend == 'Bullish' else 10 if trend == 'Mixed' else 0)
    mom = row.get('return_3m')
    momentum = 10 if mom is not None and 5 <= mom <= 50 else 5 if mom is not None and mom > 0 else 0
    if row.get('breakout_3m'): momentum += 5
    growth = 0; growth_possible = 0
    for field,threshold,pts in [('revenue_growth',0.15,13),('earnings_growth',0.15,12)]:
        value = row.get(field)
        if value is not None:
            growth_possible += pts
            if value >= threshold: growth += pts
            elif value > 0: growth += pts//2
    health = 0; health_possible = 0
    for field,pts in [('free_cash_flow',10),('profit_margin',10)]:
        value = row.get(field)
        if value is not None:
            health_possible += pts
            if value > 0: health += pts
    pe = row.get('trailing_pe')
    val = 0; val_possible = 0
    if pe is not None:
        val_possible = 20
        val = 20 if 0 < pe <= 20 else 10 if 20 < pe <= 40 else 0
    # not creating false precision from missing financial information
    possible = 35 + growth_possible + health_possible + val_possible
    return {'score':round(100*(tech+momentum+growth+health+val)/possible) if possible else None,
            'data_coverage':round(100*possible/100), 'raw_points': tech+momentum+growth+health+val}

def categories(row):
    out=[]
    if row.get('revenue_growth') is not None and row['revenue_growth'] >= .15: out.append('Growth')
    if row.get('trailing_pe') is not None and 0 < row['trailing_pe'] <= 20 and (row.get('free_cash_flow') or 0) > 0: out.append('Value')
    if row.get('trend')=='Bullish' and (row.get('return_3m') or 0)>0: out.append('Momentum')
    # Recovery requires improving sequential fundamentals, unavailable in this MVP.
    return ', '.join(out) if out else 'General review'

def fetch_live(universe):
    import yfinance as yf
    results=[]; failures=[]
    # Requests made sequentially to keep prototype rate modest and errors identifiable.
    for symbol in universe:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period='18mo', auto_adjust=True)
            if hist.empty or len(hist)<201: raise ValueError('Insufficient daily history')
            tech = technical_indicators(hist['Close'],hist['Volume'])
            if not tech: raise ValueError('Insufficient technical data')
            info = t.info or {}
            row = {'ticker': symbol, **tech,
                   'company':info.get('shortName') or symbol,
                   'market_cap':valid_number(info.get('marketCap')),
                   'revenue_growth':valid_number(info.get('revenueGrowth')),
                   'earnings_growth':valid_number(info.get('earningsGrowth')),
                   'profit_margin':valid_number(info.get('profitMargins')),
                   'free_cash_flow':valid_number(info.get('freeCashflow')),
                   'trailing_pe':valid_number(info.get('trailingPE')),
                   'sector':info.get('sector') or 'Unknown',
                   'as_of':hist.index[-1].strftime('%Y-%m-%d'),
                   'source':'Yahoo Finance via yfinance (unofficial)'}
            row.update(score_candidate(row));row['categories']=categories(row)
            results.append(row)
        except Exception as exc:
            failures.append((symbol,str(exc)[:150]))
    return pd.DataFrame(results),failures


def demo_data():
    """Explicitly synthetic test records; never mislabeled as market information."""
    samples=[
        ('DEMO-A','Illustrative Growth Co',44,40,34,22,12,.32,.29,.18,90000000,31,'Technology'),
        ('DEMO-B','Illustrative Value Co',24,21,20,8,15,.06,.11,.14,80000000,12,'Industrials'),
        ('DEMO-C','Illustrative Momentum Co',78,69,62,31,42,.09,.12,.07,40000000,42,'Software'),
        ('DEMO-D','Illustrative Weak Co',16,19,21,-9,-17,-.08,-.12,-.04,-3000000,None,'Retail'),
    ]
    rows=[]
    for sym,name,p,s50,s200,r3,r6,rg,eg,pm,fcf,pe,sector in samples:
        row={'ticker':sym,'company':name,'price':p,'sma50':s50,'sma200':s200,
             'return_3m':r3,'return_6m':r6,'revenue_growth':rg,'earnings_growth':eg,
             'profit_margin':pm,'free_cash_flow':fcf,'trailing_pe':pe,'sector':sector,
             'market_cap':2e9,'breakout_3m':sym=='DEMO-C','avg_volume_20d':1500000,
             'trend':'Bullish' if p>s50>s200 else 'Weak','source':'SYNTHETIC DEMO',
             'as_of':'Not actual market data'}
        row.update(score_candidate(row));row['categories']=categories(row);rows.append(row)
    return pd.DataFrame(rows)
