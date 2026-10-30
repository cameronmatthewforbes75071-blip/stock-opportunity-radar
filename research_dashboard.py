"""Read-only candidate financial research dashboard. No trading decisions or data mutations."""
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import streamlit as st

METRICS = {
    'revenue_growth':'Revenue growth',
    'earnings_growth':'Earnings growth',
    'profit_margin':'Profit margin',
    'free_cash_flow':'Free cash flow',
    'trailing_pe':'Trailing P/E',
}

def _read_json(path: Path) -> dict:
    if not path.exists(): return {}
    try:
        with path.open(encoding='utf-8') as f:
            obj=json.load(f)
        return obj if isinstance(obj,dict) else {}
    except (ValueError, OSError): return {}

def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists(): return pd.DataFrame()
    try: return pd.read_csv(path, low_memory=False)
    except (ValueError, OSError, pd.errors.EmptyDataError): return pd.DataFrame()

def _clean(series: pd.Series) -> pd.Series:
    return series.fillna('Not recorded').astype(str).str.strip().replace('', 'Not recorded')

def filter_research(df: pd.DataFrame, *, readiness='All', validation='All', min_metrics=0,
                    require_sec=False, search='', only_flags=False) -> pd.DataFrame:
    """Pure, independently testable filter. A missing column must not crash the dashboard."""
    out=df.copy()
    if readiness!='All' and 'research_readiness' in out:
        out=out[_clean(out['research_readiness'])==readiness]
    if validation!='All' and 'financial_validation_status' in out:
        out=out[_clean(out['financial_validation_status'])==validation]
    if 'available_financial_metrics' in out:
        out=out[pd.to_numeric(out['available_financial_metrics'],errors='coerce').fillna(0)>=min_metrics]
    elif min_metrics: return out.iloc[0:0]
    if require_sec:
        if 'provenance_class' not in out: return out.iloc[0:0]
        out=out[_clean(out['provenance_class']).str.startswith('SEC')]
    if only_flags:
        if 'financial_validation_flags' in out:
            out=out[out['financial_validation_flags'].notna() & (_clean(out['financial_validation_flags'])!='Not recorded')]
        elif 'validation_flags' in out:
            out=out[out['validation_flags'].notna() & (_clean(out['validation_flags'])!='Not recorded')]
        else: return out.iloc[0:0]
    if search and 'ticker' in out:
        out=out[_clean(out['ticker']).str.contains(search.strip(),case=False,regex=False)]
    return out

def render_research_dashboard(data_dir: Path):
    st.subheader('Candidate financial research')
    st.caption('Read-only V5.1 research reports. Research readiness is not a buy signal, and basic checks are not financial-statement audits. Missing values are not zero.')
    quality=_read_csv(data_dir/'candidate_quality.csv')
    status=_read_json(data_dir/'candidate_quality_status.json')
    audit=_read_csv(data_dir/'financial_validation.csv')
    if not status and quality.empty:
        st.info('No financial research report yet. Run the GitHub Actions workflow that executes candidate_quality.py after SEC enrichment.')
        return
    if status:
        st.caption(f"Last report (UTC): {status.get('run_utc','Unknown')}")
        cols=st.columns(4)
        for col,label,key in zip(cols,('Candidates','Any financial data','Ready for manual review','Basic checks passed'),('candidate_count','with_any_financial_metric',None,None)):
            if key: v=status.get(key,0)
            elif label=='Ready for manual review': v=status.get('research_readiness_counts',{}).get('data_ready_for_manual_review',0)
            else: v=status.get('financial_validation_status_counts',{}).get('basic_checks_passed_not_audited',0)
            col.metric(label, f'{int(v):,}' if isinstance(v,(int,float)) else '—')
        per=status.get('per_metric_coverage',{})
        if per:
            st.markdown('#### Financial metric availability')
            metric_rows=[]
            for key,label in METRICS.items():
                item=per.get(key,{})
                metric_rows.append({'Metric':label,'Available':item.get('available_count',0),
                                    'Missing':item.get('missing_count',0),
                                    'Coverage %':item.get('coverage_pct',0)})
            cov=pd.DataFrame(metric_rows)
            st.dataframe(cov,hide_index=True,width='stretch',column_config={'Coverage %':st.column_config.NumberColumn(format='%.2f%%')})
            st.bar_chart(cov.set_index('Metric')['Coverage %'],horizontal=True)
        status_counts=status.get('financial_validation_status_counts',{})
        if status_counts:
            st.markdown('#### Validation status')
            st.dataframe(pd.DataFrame([{'Status':k.replace('_',' ').title(),'Candidates':v} for k,v in status_counts.items()]),hide_index=True,width='stretch')
        flag_counts=status.get('financial_validation_flag_counts',{})
        if flag_counts:
            with st.expander('Validation warnings by type'):
                st.dataframe(pd.DataFrame([{'Flag':k,'Count':v} for k,v in flag_counts.items()]),hide_index=True,width='stretch')
    if quality.empty:
        st.warning('candidate_quality.csv is missing or unreadable, so individual candidate filtering is unavailable.')
        return
    if 'ticker' not in quality:
        st.warning('The financial research CSV does not have a ticker column.')
        return
    if not audit.empty and 'ticker' in audit.columns:
        # Merge only validation fields that the quality file does not already contain.
        audit=audit.drop_duplicates('ticker',keep='last')
        adds=['ticker']+[c for c in audit.columns if c not in quality.columns and (c.startswith('financial_validation') or c.startswith('validation_'))]
        if len(adds)>1:
            quality=quality.merge(audit[adds],on='ticker',how='left',validate='many_to_one')
    st.markdown('#### Explore researched candidates')
    a,b,c=st.columns(3)
    readiness_opts=['All']+sorted(_clean(quality['research_readiness']).unique().tolist()) if 'research_readiness' in quality else ['All']
    validation_col='financial_validation_status' if 'financial_validation_status' in quality else 'validation_status' if 'validation_status' in quality else None
    validation_opts=['All']+sorted(_clean(quality[validation_col]).unique().tolist()) if validation_col else ['All']
    with a: readiness=st.selectbox('Research readiness',readiness_opts)
    with b: validation=st.selectbox('Financial validation',validation_opts)
    with c: min_metrics=st.slider('Minimum available financial metrics',0,5,0)
    d,e,f=st.columns([1,1,2])
    with d: require_sec=st.checkbox('Require SEC provenance')
    with e: only_flags=st.checkbox('Only flagged records')
    with f: search=st.text_input('Ticker contains',placeholder='e.g. AAPL')
    # Allow compatibility with alternative V5.1 validation_status column.
    if validation_col=='validation_status' and 'financial_validation_status' not in quality:
        quality=quality.rename(columns={'validation_status':'financial_validation_status'})
    view=filter_research(quality,readiness=readiness,validation=validation,min_metrics=min_metrics,
                         require_sec=require_sec,search=search,only_flags=only_flags)
    if 'score_existing' in view: view=view.sort_values('score_existing',ascending=False,na_position='last')
    st.caption(f'{len(view):,} of {len(quality):,} candidate records match these research filters. The technical score is an existing unvalidated heuristic.')
    chosen=['ticker','company','score_existing','research_readiness','financial_validation_status',
            'available_financial_metrics','financial_coverage_pct','provenance_class','latest_known_financial_period',
            'financial_validation_flags','validation_flags','missing_metrics','price','as_of']
    chosen=[c for c in chosen if c in view.columns]
    cfg={'price':st.column_config.NumberColumn(format='$%.2f'),
         'financial_coverage_pct':st.column_config.NumberColumn(format='%.2f%%'),
         'score_existing':st.column_config.NumberColumn(format='%.0f')}
    st.dataframe(view[chosen],hide_index=True,width='stretch',column_config=cfg)
    st.download_button('Download filtered research table',view.to_csv(index=False).encode('utf-8'),
                       file_name='filtered_research.csv',mime='text/csv')
    if not audit.empty:
        with st.expander('Full validation audit report'):
            st.dataframe(audit,hide_index=True,width='stretch')
            st.download_button('Download financial validation CSV',audit.to_csv(index=False).encode('utf-8'),
                               file_name='financial_validation.csv',mime='text/csv')


    render_experimental_scores(data_dir)
    render_forward_performance(data_dir)
    render_tracking_audit(data_dir)
    render_prospective_evaluation(data_dir)

def render_experimental_scores(data_dir: Path):
    st.markdown('#### V5.4 experimental research scores and comparability')
    st.caption('Unvalidated research heuristics only, not buy/sell signals. Only SEC-period candidates passing basic checks are scored. No sector-relative valuation or historical performance test.')
    scores=_read_csv(data_dir/'investment_research_scores.csv')
    status=_read_json(data_dir/'investment_research_status.json')
    if scores.empty:
        st.info('No V5.3 score report found. Run the updated GitHub workflow.')
        return
    if status:
        c1,c2,c3=st.columns(3)
        c1.metric('Candidates',status.get('candidate_count',0))
        c2.metric('Experimental scored',status.get('experimental_scored_count',0))
        c3.metric('Unranked',status.get('unranked_count',0))
    show_only=st.checkbox('Show only scored research candidates',value=True,key='v53_scored_only')
    view=scores.copy()
    if show_only: view=view[view.experimental_research_score.notna()]
    st.caption('Complete four-component and partial composite scores are NOT directly comparable. Scoring is experimental and not backtested; missing inputs are not scored as zero.')
    if 'score_comparability_group' in view:
        groups=['All']+sorted(view['score_comparability_group'].dropna().unique().tolist())
        selected=st.selectbox('Score comparability group',groups,key='v54_comparability')
        if selected!='All': view=view[view['score_comparability_group']==selected]
    cols=[k for k in ['ticker','experimental_research_score','growth','health','valuation','technical','scoring_coverage_pct','component_count','missing_components','score_comparability_group','financial_fields_present','scoring_status','data_confidence_tier'] if k in view]
    st.dataframe(view[cols],hide_index=True,width='stretch')
    st.download_button('Download experimental scoring report',view.to_csv(index=False).encode('utf-8'),file_name='investment_research_scores.csv',mime='text/csv',key='v53_download')


# The existing dashboard remains read-only; future price observations are diagnostic only.


def render_forward_performance(data_dir: Path):
    st.markdown('#### V5.5 prospective performance observations')
    st.caption('Observations begin only after snapshots were recorded. Raw price changes are not total returns or demonstrated alpha. SPY comparisons are shown only if both benchmark samples were saved during live runs.')
    status=_read_json(data_dir/'prospective_tracking_status.json')
    if not status:
        st.info('Prospective tracking status unavailable. Run the updated GitHub workflow.')
        return
    a,b,c=st.columns(3)
    a.metric('Original snapshots',status.get('total_unique_snapshots',0))
    b.metric('Price observations',status.get('total_price_observations',0))
    c.metric('SPY ledger dates',status.get('spy_ledger_entries',0))
    outs=_read_csv(data_dir/'prospective_price_outcomes.csv')
    if outs.empty:
        st.info('No observation windows have matured yet. Initial zero observations is expected.')
        return
    options=['All']+sorted(outs.horizon_days.dropna().astype(int).unique().tolist()) if 'horizon_days' in outs else ['All']
    h=st.selectbox('Observation horizon (calendar days)',options,key='v55_horizon')
    if h!='All': outs=outs[outs.horizon_days==h]
    if 'comparability_group' in outs:
        names=['All']+sorted(outs.comparability_group.dropna().astype(str).unique().tolist())
        g=st.selectbox('Snapshot score comparability group',names,key='v55_group')
        if g!='All': outs=outs[outs.comparability_group==g]
    cols=[x for x in ['ticker','run_date','horizon_days','score_at_snapshot','comparability_group','origin_price_date','observed_price_date','raw_price_change_pct','benchmark_status','spy_price_change_pct','relative_to_spy_pct_points'] if x in outs]
    st.dataframe(outs[cols],hide_index=True,width='stretch')
    st.download_button('Download prospective observations',outs.to_csv(index=False).encode('utf-8'),file_name='prospective_price_outcomes.csv',mime='text/csv',key='v55_outcomes_download')


def render_tracking_audit(data_dir: Path):
    st.markdown('#### V5.6 tracking integrity and benchmark coverage')
    report=_read_json(data_dir/'prospective_audit_status.json')
    if not report:
        st.info('V5.6 audit not generated yet. Run the updated GitHub workflow.')
        return
    st.caption('An integrity PASS checks records against hashes saved since the first V5.6 audit. It is not a financial audit or a historical data verification.')
    a,b,c=st.columns(3)
    a.metric('Integrity status',report.get('integrity_status','unknown'))
    b.metric('SPY baselines',report.get('snapshot_spy_baseline_present',0))
    c.metric('SPY coverage',str(report.get('spy_baseline_coverage_pct',0))+'%')
    st.caption(f"Snapshots: {report.get('snapshot_count',0):,} · Outcomes: {report.get('outcome_count',0):,} · SPY rows: {report.get('spy_entries',0):,}")
    if report.get('integrity_violations'):
        st.error('Tracking integrity warnings: '+'; '.join(report['integrity_violations']))
    if report.get('baseline_initialized_this_run'):
        st.warning('This is the FIRST audit: it establishes a baseline; prior row contents cannot be verified retroactively.')
    with st.expander('Detailed tracking audit'):
        st.json(report)


def render_prospective_evaluation(data_dir: Path):
    st.markdown('#### V5.8 prospective cohort completion')
    st.caption('This is observation coverage, NOT strategy performance. Complete and partial research scores are displayed separately. No retroactive benchmarks.')
    report=_read_json(data_dir/'prospective_evaluation_status.json')
    table=_read_csv(data_dir/'prospective_evaluation.csv')
    if not report or table.empty:
        st.info('V5.7 prospective evaluation report is not yet available. Run the updated workflow.')
        return
    if report.get('integrity_status')!='PASS':
        st.error('Tracking audit has not passed. Do not interpret evaluation output.')
        return
    x,y,z=st.columns(3)
    x.metric('Tracked snapshots',report.get('snapshot_count',0))
    y.metric('Recorded forward outcomes',report.get('outcome_count',0))
    z.metric('SPY baselines',report.get('spy_baseline_present',0))
    if 'horizon_calendar_days' in table:
        h=st.selectbox('Cohort observation horizon (calendar days)',[7,30,90],key='v57_eval_horizon')
        table=table[pd.to_numeric(table.horizon_calendar_days,errors='coerce')==h]
    cols=[c for c in ['comparability_group','snapshots','matured_eligible','observed','matured_missing_observation','pending_not_due','observations_with_spy','baseline_spy_present','completion_pct'] if c in table]
    st.dataframe(table[cols],hide_index=True,width='stretch')
    st.download_button('Download cohort coverage CSV',table.to_csv(index=False).encode('utf-8'),file_name='prospective_evaluation.csv',mime='text/csv',key='v57_eval_download')
    recovery=_read_json(data_dir/'prospective_comparability_status.json')
    if recovery:
        st.caption('Comparability evidence from immutable snapshot fields (no retrospective scoring).')
        st.json({'recovered_group_counts': recovery.get('recovered_group_counts', {}),
                 'evidence_counts': recovery.get('evidence_counts', {})})
    with st.expander('Evaluation methodology and diagnostics'):
        st.json(report)
