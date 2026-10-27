"""Prospective research-score snapshots and subsequent price observations.
No historical backfill: records are created only from today's workflow execution.
Horizon is calendar days, observation is first later observed trading session.
NOT a backtest; returns omit dividends, corporate actions, transaction costs, benchmarks.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

DATA=Path(__file__).resolve().parent/'data'
SNAP=DATA/'prospective_score_snapshots.csv'
OUT=DATA/'prospective_price_outcomes.csv'
STATUS=DATA/'prospective_tracking_status.json'
HORIZONS=(7,30,90)


def run(scores, discovery, today=None, old_snapshots=None, old_outcomes=None):
    now=pd.Timestamp(today or datetime.now(timezone.utc))
    if now.tzinfo is None: now=now.tz_localize('UTC')
    run_date=now.date().isoformat()
    records=[]
    if not discovery.empty and {'ticker','price','as_of'}.issubset(discovery.columns) and not scores.empty:
        d=discovery[['ticker','price','as_of']].copy().drop_duplicates('ticker',keep='last')
        d['as_of']=pd.to_datetime(d.as_of,errors='coerce',utc=True)
        d['price']=pd.to_numeric(d.price,errors='coerce')
        s=scores[scores.experimental_research_score.notna()].merge(d,on='ticker',how='left')
        for r in s.to_dict('records'):
            asof=r.get('as_of')
            if pd.isna(asof) or pd.isna(r.get('price')): continue
            if not (0<float(r['price']) and asof.date()<=now.date()): continue
            # This price is the most recent recorded candle, not an execution price.
            records.append(dict(ticker=str(r['ticker']),recorded_utc=now.isoformat(),run_date=run_date,
                reference_price_date=asof.date().isoformat(),reference_price=float(r['price']),
                research_score=float(r['experimental_research_score']),
                comparability_group=r.get('score_comparability_group',''),
                scoring_coverage_pct=r.get('scoring_coverage_pct',''),
                growth=r.get('growth',''),health=r.get('health',''),valuation=r.get('valuation',''),technical=r.get('technical','')))
    previous=old_snapshots if old_snapshots is not None else pd.DataFrame()
    snaps=pd.concat([previous,pd.DataFrame(records)],ignore_index=True)
    if not snaps.empty:
        snaps=snaps.drop_duplicates(['ticker','run_date'],keep='first')
    old=old_outcomes if old_outcomes is not None else pd.DataFrame()
    outcomes=old.copy()
    prior=set()
    if not old.empty and {'ticker','run_date','horizon_days'}.issubset(old.columns):
        prior=set(zip(old.ticker.astype(str),old.run_date.astype(str),old.horizon_days.astype(int)))
    added=[]
    if not snaps.empty and not discovery.empty and {'ticker','price','as_of'}.issubset(discovery.columns):
        current=discovery.drop_duplicates('ticker',keep='last').set_index('ticker')
        for r in snaps.to_dict('records'):
            sym=str(r['ticker'])
            if sym not in current.index: continue
            price=pd.to_numeric(current.loc[sym].get('price'),errors='coerce')
            dt=pd.to_datetime(current.loc[sym].get('as_of'),errors='coerce',utc=True)
            origin=pd.to_datetime(r.get('reference_price_date'),errors='coerce',utc=True)
            starting=pd.to_numeric(r.get('reference_price'),errors='coerce')
            if pd.isna(price) or pd.isna(dt) or pd.isna(origin) or pd.isna(starting): continue
            if price<=0 or starting<=0 or dt.date()>now.date(): continue
            for h in HORIZONS:
                key=(sym,str(r['run_date']),h)
                if key in prior or dt.date() < (pd.to_datetime(r['run_date'],utc=True)+pd.Timedelta(days=h)).date(): continue
                added.append(dict(ticker=sym,run_date=str(r['run_date']),horizon_days=h,
                    origin_price_date=origin.date().isoformat(),observed_price_date=dt.date().isoformat(),
                    origin_price=float(starting),observed_price=float(price),
                    raw_price_change_pct=round((float(price)/float(starting)-1)*100,5),
                    score_at_snapshot=r.get('research_score'),comparability_group=r.get('comparability_group')))
                prior.add(key)
    outcomes=pd.concat([outcomes,pd.DataFrame(added)],ignore_index=True)
    status=dict(run_utc=now.isoformat(),total_unique_snapshots=len(snaps),new_snapshots=len(snaps)-len(previous),
        total_price_observations=len(outcomes),new_price_observations=len(added),
        notes='Forward observation only. No historical backfill or point-in-time backtest. Price-only changes can be distorted by splits, distributions and stale prices; not total return, alpha, or executable performance.')
    return snaps,outcomes,status


def main():
    scores=pd.read_csv(DATA/'investment_research_scores.csv')
    discovery=pd.read_csv(DATA/'discovery.csv',low_memory=False)
    prev=pd.read_csv(SNAP,low_memory=False) if SNAP.exists() else None
    old=pd.read_csv(OUT,low_memory=False) if OUT.exists() else None
    snaps,outcomes,status=run(scores,discovery,old_snapshots=prev,old_outcomes=old)
    DATA.mkdir(exist_ok=True)
    snaps.to_csv(SNAP,index=False)
    outcomes.to_csv(OUT,index=False)
    STATUS.write_text(json.dumps(status,indent=2)+'\n')
    print(json.dumps(status,indent=2))

if __name__=='__main__':main()
