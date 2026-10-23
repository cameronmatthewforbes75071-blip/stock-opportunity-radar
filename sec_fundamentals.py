"""Best-effort SEC XBRL annual fundamentals; no estimates or real-time data."""
import json, os, time
from urllib.request import Request, urlopen
from pathlib import Path
from datetime import datetime, timezone

CACHE=Path(__file__).resolve().parent/'data'/'sec_cache'
BASE='https://data.sec.gov'
TICKERS='https://www.sec.gov/files/company_tickers.json'

def _get(url, agent):
    req=Request(url,headers={'User-Agent':agent,'Accept':'application/json','Accept-Encoding':'identity'})
    with urlopen(req,timeout=18) as response:
        return json.load(response)

def _cached(url, agent, path, hours):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time()-path.stat().st_mtime < hours*3600:
        return json.loads(path.read_text())
    response=_get(url,agent)
    path.write_text(json.dumps(response))
    time.sleep(.15)
    return response

def _annual(facts, tags, unit='USD'):
    for tag in tags:
        rows=facts.get('us-gaap',{}).get(tag,{}).get('units',{}).get(unit,[])
        years={}
        for r in rows:
            if r.get('form') not in ('10-K','10-K/A') or r.get('fp')!='FY': continue
            if not all(k in r for k in ('start','end','filed','val')): continue
            try:
                span=(datetime.fromisoformat(r['end'])-datetime.fromisoformat(r['start'])).days
            except ValueError: continue
            if not 330<=span<=380: continue
            fy=int(r.get('fy',0)); y=int(r['end'][:4])
            if fy!=y: continue
            # Prefer earlier filed 10-K for each fiscal period over later restatements.
            current=years.get(r['end'])
            if current is None or r['filed']<current['filed']:
                years[r['end']]={'val':r['val'],'filed':r['filed'],'tag':tag,'end':r['end']}
        if years: return sorted(years.values(),key=lambda x:x['end'],reverse=True)
    return []

def fetch_sec_annual(ticker,agent=None):
    agent=agent or os.getenv('RADAR_SEC_USER_AGENT','').strip()
    if not agent or '@' not in agent: return {},'SEC not configured (set RADAR_SEC_USER_AGENT)'
    try:
        directory=_cached(TICKERS,agent,CACHE/'company_tickers.json',24*7)
        matching=[v for v in directory.values() if str(v.get('ticker','')).upper()==ticker.upper()]
        if not matching: return {},'Ticker not present in SEC company directory'
        cik=int(matching[0]['cik_str'])
        data=_cached(f'{BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json',agent,CACHE/f'{cik}.json',24)
        facts=data.get('facts',{})
        revenue=_annual(facts,['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','RevenueFromContractWithCustomerIncludingAssessedTax'])
        earnings=_annual(facts,['NetIncomeLoss'])
        op_cash=_annual(facts,['NetCashProvidedByUsedInOperatingActivities'])
        capex=_annual(facts,['PaymentsToAcquirePropertyPlantAndEquipment'])
        by_end=lambda vals:{v['end']:v for v in vals}
        vals={}; periods={}; tags={}
        for key,items in [('revenue',revenue),('net_income',earnings),('operating_cash_flow',op_cash),('capex',capex)]:
            if items:
                vals[key]=items[0]['val']; periods[key]=items[0]['end']; tags[key]=items[0]['tag']
        if len(revenue)>=2 and revenue[1]['val']>0:
            vals['revenue_growth']=(revenue[0]['val']/revenue[1]['val'])-1
        if len(earnings)>=2 and earnings[1]['val']>0:
            vals['earnings_growth']=(earnings[0]['val']/earnings[1]['val'])-1
        # Do not mix reporting periods when constructing derived ratios.
        if revenue and earnings and revenue[0]['end']==earnings[0]['end'] and revenue[0]['val']>0:
            vals['profit_margin']=earnings[0]['val']/revenue[0]['val']
        if op_cash and capex and op_cash[0]['end']==capex[0]['end']:
            vals['free_cash_flow']=op_cash[0]['val']-capex[0]['val']
        if not vals: return {},'No usable annual SEC XBRL metrics'
        return {'metrics':vals,'periods':periods,'tags':tags,'cik':cik,'sec_last_retrieved_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'), 'source':'SEC XBRL companyfacts (annual 10-K)'},'OK'
    except Exception as e:
        return {}, f'{type(e).__name__}: {str(e)[:140]}'
