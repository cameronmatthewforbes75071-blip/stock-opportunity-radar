# Stock Opportunity Radar — v1 (research prototype)

A mobile-friendly Streamlit research dashboard for **position-trading candidate discovery**. This is the first working stage of a larger automated discovery + sourced AI research site. It does not run unattended yet.

## Start on Mac

```bash
cd stock_opportunity_radar_v1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local address shown by Streamlit. To view on phone, deploy the app to Streamlit Community Cloud.

## Deploy online (no server configuration)

1. Upload `app.py`, `scanner.py`, and `requirements.txt` to a GitHub repository.
2. Open https://share.streamlit.io/ and sign in with GitHub.
3. Select **Create app → existing app**, choose the repository and `app.py`, then deploy.
4. Open the resulting Streamlit URL on your phone. Protect the app or keep it private if you prefer.

## Data modes

- **Demo (default)**: All prices, companies and scores are made-up and clearly marked.
- **Live**: Pulls price history and some fundamentals for **up to 30 user-supplied tickers** using `yfinance`. It does **not** scan all listed equities yet. Live mode requires outbound network access and the source can fail, rate-limit, or return stale data.
- `yfinance` is an unofficial research library; its authors note Yahoo Finance API data is intended for personal use. Check data licensing before publishing a service that redistributes data.

## What is implemented

- Daily 50/200 SMA trend; rolling 3- and 6-month price changes and breakout flag.
- Fundamental fields (if available) including revenue/earnings growth, P/E, FCF, margins.
- Transparent category labels (Growth, Value, Momentum).
- Experimental normalized research score + data coverage warning.
- Filterable results, CSV export, single-company detail, TradingView link.

## Important missing pieces (next iterations)

1. Licensed scalable full-US equity coverage including delisted names and share-class cleanup.
2. Reliable normalized quarterly statements via primary filings and point-in-time fundamentals.
3. Dedicated Value, Growth, Recovery, Momentum ranking models and peer comparisons.
4. Durable DB (Supabase), automated after-close worker, upsert, historical run logs, and duplicate suppression.
5. Sourced filings/news, AI research summaries with citation and hallucination controls.
6. Paper-portfolio tracking, forward testing, out-of-sample evaluation vs SPY with survivorship / look-ahead safeguards.
7. Notifications, user access controls, security / license review before publication.

**Trading is disabled by design. Scores are not buy signals.**
