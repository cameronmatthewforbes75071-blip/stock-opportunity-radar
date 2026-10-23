import streamlit as st
import pandas as pd
from scanner import DEFAULT_UNIVERSE, demo_data, fetch_live

st.set_page_config(page_title='Stock Opportunity Radar',page_icon='📡',layout='wide')
st.markdown('''<style>
.stApp {background: #0c1421; color: #eaf2ff}
[data-testid="stMetric"] {background: #152236; border:1px solid #304462; border-radius:12px;padding:14px}
h1,h2,h3 {color:#f3f7ff}
</style>''', unsafe_allow_html=True)
st.title('📡 Stock Opportunity Radar')
st.caption('V1 • Research-only candidate discovery • US equities • Long/position trading')

with st.sidebar:
    st.header('Scan controls')
    mode=st.radio('Data mode',['Demo (synthetic)','Live (limited universe)'],index=0,
                  help='Demo is illustrative only. Live pulls from unofficial Yahoo Finance data.')
    symbols=st.text_area('Tickers (comma separated)',', '.join(DEFAULT_UNIVERSE[:12]),height=110)
    min_score=st.slider('Minimum research score',0,100,0)
    min_coverage=st.slider('Minimum data coverage (%)',0,100,35)
    st.caption('Live scanning is limited to 30 tickers per run to avoid heavy unofficial requests. This does NOT scan the entire stock market.')
    clicked=st.button('🔎 Run scan',type='primary',use_container_width=True)

@st.cache_data(ttl=3600, show_spinner=False)
def load_live(names):
    return fetch_live(list(names))

if 'scan_data' not in st.session_state:
    st.session_state.scan_data=demo_data()
    st.session_state.scan_errors=[]
    st.session_state.current_mode='Demo (synthetic)'
if clicked:
    if mode.startswith('Demo'):
        st.session_state.scan_data=demo_data();st.session_state.scan_errors=[];st.session_state.current_mode=mode
    else:
        names=tuple(dict.fromkeys(v.strip().upper() for v in symbols.split(',') if v.strip()))[:30]
        if not names: st.error('Enter at least one ticker.')
        else:
            with st.spinner('Retrieving price history and financial metrics…'):
                df,errors=load_live(names)
            st.session_state.scan_data=df;st.session_state.scan_errors=errors;st.session_state.current_mode=mode

df=st.session_state.scan_data.copy()
if st.session_state.current_mode.startswith('Demo'):
    st.warning('DEMO MODE: ALL CANDIDATES AND METRICS ARE FICTIONAL. Press Run scan in Live mode for current third-party data.')
else:
    st.info('Live pilot scans only the tickers entered, not the whole US market. Yahoo/yfinance data is unofficial and may be missing or stale. Validate company filings before making decisions.')
if st.session_state.scan_errors:
    with st.expander(f'{len(st.session_state.scan_errors)} tickers unavailable / failed'):
        st.write(pd.DataFrame(st.session_state.scan_errors,columns=['Ticker','Reason']))

if df.empty:
    st.error('No usable records. Try fewer tickers, or Demo mode. No candidates have been fabricated.')
    st.stop()

total=len(df)
filtered=df[(df.score>=min_score)&(df.data_coverage>=min_coverage)].copy().sort_values(['score','data_coverage'],ascending=False)
a,b,c,d=st.columns(4)
a.metric('Tickers analyzed',total)
b.metric('Candidates displayed',len(filtered))
c.metric('Bullish trends',int((df.trend=='Bullish').sum()))
d.metric('Average data coverage',f'{df.data_coverage.mean():.0f}%')
st.divider()
tab1,tab2,tab3=st.tabs(['🎯 Opportunities','🔬 Stock research','ℹ️ Methodology'])
with tab1:
    st.subheader('Research candidates')
    st.caption('Scores are unvalidated heuristics—not return forecasts or buy signals. Missing data lowers coverage.')
    if filtered.empty: st.write('No stocks meet these filters. Reduce the score or data-coverage threshold.')
    else:
        show=filtered[['ticker','company','categories','score','data_coverage','price','trend','return_3m','return_6m','as_of']].rename(columns={'ticker':'Ticker','company':'Company','categories':'Category','score':'Score / 100','data_coverage':'Coverage %','price':'Price','trend':'Trend','return_3m':'3M %','return_6m':'6M %','as_of':'Price as of'})
        st.dataframe(show,use_container_width=True,hide_index=True)
        st.download_button('Download CSV',filtered.to_csv(index=False).encode(),file_name='stock_radar_results.csv',mime='text/csv')
with tab2:
    selected=st.selectbox('Select ticker to inspect',df.ticker.tolist())
    row=df[df.ticker==selected].iloc[0]
    st.header(f"{row.company} ({row.ticker})")
    st.write(f"**Category:** {row.categories} · **Sector:** {row.sector} · **Source:** {row.source}")
    st.write(f"**Research score:** {row.score}/100 · **Data coverage:** {row.data_coverage}%")
    u,v,w=st.columns(3)
    u.metric('Price',f"${row.price:,.2f}")
    v.metric('3-month price change',f"{row.return_3m:.1f}%")
    w.metric('6-month price change',f"{row.return_6m:.1f}%")
    st.markdown('#### Price signals')
    st.write(f"Trend: **{row.trend}** · 50-day average: **${row.sma50:.2f}** · 200-day average: **${row.sma200:.2f}**")
    st.markdown('#### Business and valuation signals')
    for col,label,percent in [('revenue_growth','Revenue growth',True),('earnings_growth','Earnings growth',True),('profit_margin','Profit margin',True),('trailing_pe','Trailing P/E',False),('free_cash_flow','Free cash flow',False)]:
        value=row[col]; display='Unavailable' if pd.isna(value) else (f'{value*100:.1f}%' if percent else f'{value:,.2f}')
        st.write(f'- **{label}:** {display}')
    st.markdown('#### Verification and research')
    st.write('Check the latest 10-Q / 10-K, management guidance, peer valuations, share count, dilution, and risk factors before forming an investment thesis.')
    if not selected.startswith('DEMO'):
        st.link_button('Chart on TradingView',f'https://www.tradingview.com/symbols/{selected}/')
        st.link_button('SEC filings search',f'https://www.sec.gov/edgar/search/#/q={selected}')
with tab3:
    st.markdown('''
**Prototype design.** Daily closing prices feed a 50/200-day trend signal and 3/6-month returns. Financial values are requested from an unofficial research data source.

**Score:** up to 20 trend points, 15 momentum points, 25 growth points, 20 financial health points, and 20 valuation points. Score is normalized only over available scored categories. **Coverage %** shows how many of 100 possible points had the required data; don't compare low-coverage stocks blindly.

**Limitations:** No point-in-time fundamentals, market-wide symbol ingestion, survivorship-bias controls, true sector-relative valuation, earnings event detection, backtesting, SEC financial validation, or AI research yet. The recovery scanner remains unimplemented until historical sequential financials are available. No autonomous daily job is deployed in V1.

**No automatic trading.** Nothing in this project places orders, connects to a brokerage, or promises profitability.
''')
