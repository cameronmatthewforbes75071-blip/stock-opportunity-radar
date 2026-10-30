
"""V5.5 prospective-only observations. NEVER backfill historical snapshots.

Price changes are informational; not alpha, total return, or trade P&L.
SPY is sampled only on live workflow runs. Benchmarks are matched only when
baseline dates and horizon observation dates are recorded in the ledger.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError


DATA = Path(__file__).resolve().parent / "data"

SNAP = DATA / "prospective_score_snapshots.csv"
OUT = DATA / "prospective_price_outcomes.csv"
STATUS = DATA / "prospective_tracking_status.json"
SPY = DATA / "prospective_spy_observations.csv"

HORIZONS = (7, 30, 90)

OUTCOME_COLUMNS = (
    "ticker",
    "run_date",
    "horizon_days",
    "origin_price_date",
    "observed_price_date",
    "origin_price",
    "observed_price",
    "raw_price_change_pct",
    "score_at_snapshot",
    "comparability_group",
    "recorded_utc",
    "benchmark_status",
    "spy_price_change_pct",
    "relative_to_spy_pct_points",
)

SPY_COLUMNS = (
    "run_date",
    "price_date",
    "spy_close",
    "observed_utc",
)


def _read_optional_csv(path):
    """Handle empty legacy CSVs without discarding valid data."""
    if not path.exists():
        return None

try:
    return pd.read_csv(path, dtype=str, keep_default_na=False)    except EmptyDataError:
        if not path.read_bytes().strip():
            print(
                f"Empty CSV without columns, initializing: {path.name}"
            )
            return None

        raise


def _write_csv_with_columns(frame, path, required_columns):
    """Always write a valid header, even when no rows exist."""
    frame = frame.copy()

    for name in required_columns:
        if name not in frame.columns:
            frame[name] = pd.Series(dtype="object")

    frame.to_csv(path, index=False)


def _utc(value):
    return pd.to_datetime(
        value,
        errors="coerce",
        utc=True,
    )


def _valid_price(value):
    v = pd.to_numeric(value, errors="coerce")

    if pd.notna(v) and float(v) > 0:
        return v

    return None


def _lookup_spy(spy_history, run_date):
    if spy_history is None or spy_history.empty:
        return None

    match = spy_history[
        spy_history.run_date.astype(str) == str(run_date)
    ]

    if match.empty:
        return None

    return match.iloc[0]


def run(
    scores,
    discovery,
    today=None,
    old_snapshots=None,
    old_outcomes=None,
    spy_history=None,
    spy_observation=None,
):
    now = _utc(
        today if today is not None
        else datetime.now(timezone.utc)
    )

    if pd.isna(now):
        raise ValueError("Invalid run timestamp")

    today_str = now.date().isoformat()

    previous = (
        old_snapshots.copy()
        if old_snapshots is not None
        else pd.DataFrame()
    )

    prior_outcomes = (
        old_outcomes.copy()
        if old_outcomes is not None
        else pd.DataFrame()
    )

    benchmark = (
        spy_history.copy()
        if spy_history is not None
        else pd.DataFrame(columns=list(SPY_COLUMNS))
    )

    new_spy = 0

    if (
        spy_observation is not None
        and (
            benchmark.empty
            or today_str not in set(
                benchmark.run_date.astype(str)
            )
        )
    ):
        d = _utc(spy_observation.get("price_date"))
        v = _valid_price(
            spy_observation.get("spy_close")
        )

        if (
            pd.notna(d)
            and d.date() <= now.date()
            and v is not None
            and (now.date() - d.date()).days <= 5
        ):
            benchmark = pd.concat(
                [
                    benchmark,
                    pd.DataFrame(
                        [
                            dict(
                                run_date=today_str,
                                price_date=d.date().isoformat(),
                                spy_close=float(v),
                                observed_utc=now.isoformat(),
                            )
                        ]
                    ),
                ],
                ignore_index=True,
            )

            new_spy = 1

    if not benchmark.empty:
        benchmark = benchmark.drop_duplicates(
            ["run_date"],
            keep="first",
        )

    records = []

    if (
        not scores.empty
        and not discovery.empty
        and {"ticker", "price", "as_of"}.issubset(
            discovery.columns
        )
        and {
            "ticker",
            "experimental_research_score",
        }.issubset(scores.columns)
    ):
        d = (
            discovery[
                ["ticker", "price", "as_of"]
            ]
            .copy()
            .drop_duplicates("ticker", keep="last")
        )

        d["as_of"] = pd.to_datetime(
            d.as_of,
            errors="coerce",
            utc=True,
        )

        d["price"] = pd.to_numeric(
            d.price,
            errors="coerce",
        )

        s = scores[
            scores.experimental_research_score.notna()
        ].merge(
            d,
            on="ticker",
            how="left",
        )

        spy_base = _lookup_spy(
            benchmark,
            today_str,
        )

        for r in s.to_dict("records"):
            asof = r.get("as_of")
            price = _valid_price(r.get("price"))

            if (
                pd.isna(asof)
                or price is None
                or asof.date() > now.date()
            ):
                continue

            if (now.date() - asof.date()).days > 5:
                continue

            record = dict(
                ticker=str(r["ticker"]),
                recorded_utc=now.isoformat(),
                run_date=today_str,
                reference_price_date=asof.date().isoformat(),
                reference_price=float(price),
                research_score=float(
                    r["experimental_research_score"]
                ),
                comparability_group=r.get(
                    "score_comparability_group",
                    "",
                ),
                scoring_coverage_pct=r.get(
                    "scoring_coverage_pct",
                    "",
                ),
                growth=r.get("growth", ""),
                health=r.get("health", ""),
                valuation=r.get("valuation", ""),
                technical=r.get("technical", ""),
                spy_reference_run_date=(
                    today_str
                    if spy_base is not None
                    else ""
                ),
                spy_reference_price_date=(
                    str(spy_base.price_date)
                    if spy_base is not None
                    else ""
                ),
                spy_reference_price=(
                    float(spy_base.spy_close)
                    if spy_base is not None
                    else float("nan")
                ),
            )

            records.append(record)

    snaps = pd.concat(
        [previous, pd.DataFrame(records)],
        ignore_index=True,
    )

    if not snaps.empty:
        snaps = snaps.drop_duplicates(
            ["ticker", "run_date"],
            keep="first",
        )

    new_snapshots = len(snaps) - len(previous)

    prior = set()

    if (
        not prior_outcomes.empty
        and {
            "ticker",
            "run_date",
            "horizon_days",
        }.issubset(prior_outcomes.columns)
    ):
        for r in prior_outcomes[
            ["ticker", "run_date", "horizon_days"]
        ].itertuples(
            index=False,
            name=None,
        ):
            try:
                prior.add(
                    (
                        str(r[0]),
                        str(r[1]),
                        int(r[2]),
                    )
                )
            except (ValueError, TypeError):
                pass

    added = []
    eligible_unobserved = 0
    spy_missing = 0

    if (
        not snaps.empty
        and not discovery.empty
        and {
            "ticker",
            "price",
            "as_of",
        }.issubset(discovery.columns)
    ):
        current = (
            discovery
            .drop_duplicates("ticker", keep="last")
            .set_index("ticker")
        )

        spy_current = _lookup_spy(
            benchmark,
            today_str,
        )

        for r in snaps.to_dict("records"):
            sym = str(r["ticker"])

            if sym not in current.index:
                continue

            price = _valid_price(
                current.loc[sym].get("price")
            )

            dt = _utc(
                current.loc[sym].get("as_of")
            )

            origin = _utc(
                r.get("reference_price_date")
            )

            starting = _valid_price(
                r.get("reference_price")
            )

            start_run = _utc(
                r.get("run_date")
            )

            if (
                price is None
                or pd.isna(dt)
                or pd.isna(origin)
                or starting is None
                or pd.isna(start_run)
            ):
                continue

            if (
                dt.date() > now.date()
                or (now.date() - dt.date()).days > 5
            ):
                continue

            if origin.date() > start_run.date():
                continue

            for h in HORIZONS:
                key = (
                    sym,
                    str(r["run_date"]),
                    h,
                )

                if key in prior:
                    continue

                due = (
                    start_run + pd.Timedelta(days=h)
                ).date()

                if now.date() < due:
                    continue

                if dt.date() < due:
                    eligible_unobserved += 1
                    continue

                row = dict(
                    ticker=sym,
                    run_date=str(r["run_date"]),
                    horizon_days=h,
                    origin_price_date=origin.date().isoformat(),
                    observed_price_date=dt.date().isoformat(),
                    origin_price=float(starting),
                    observed_price=float(price),
                    raw_price_change_pct=round(
                        (
                            float(price) / float(starting)
                            - 1
                        ) * 100,
                        5,
                    ),
                    score_at_snapshot=r.get(
                        "research_score"
                    ),
                    comparability_group=r.get(
                        "comparability_group"
                    ),
                    recorded_utc=now.isoformat(),
                    benchmark_status="unavailable",
                    spy_price_change_pct=float("nan"),
                    relative_to_spy_pct_points=float("nan"),
                )

                spy_origin = _valid_price(
                    r.get("spy_reference_price")
                )

                if (
                    spy_origin is not None
                    and spy_current is not None
                ):
                    spy_now = _valid_price(
                        spy_current.spy_close
                    )

                    spy_date = _utc(
                        spy_current.price_date
                    )

                    if (
                        spy_now is not None
                        and pd.notna(spy_date)
                        and spy_date.date() >= due
                    ):
                        spy_move = (
                            float(spy_now)
                            / float(spy_origin)
                            - 1
                        ) * 100

                        row.update(
                            benchmark_status="matched_live_samples",
                            spy_price_change_pct=round(
                                spy_move, 5
                            ),
                            relative_to_spy_pct_points=round(
                                row["raw_price_change_pct"]
                                - spy_move,
                                5,
                            ),
                        )
                    else:
                        spy_missing += 1
                else:
                    spy_missing += 1

                added.append(row)
                prior.add(key)

    outcomes = pd.concat(
        [
            prior_outcomes,
            pd.DataFrame(added),
        ],
        ignore_index=True,
    )

    if not outcomes.empty:
        outcomes = outcomes.drop_duplicates(
            ["ticker", "run_date", "horizon_days"],
            keep="first",
        )

    status = dict(
        run_utc=now.isoformat(),
        total_unique_snapshots=len(snaps),
        new_snapshots=new_snapshots,
        total_price_observations=len(outcomes),
        new_price_observations=len(added),
        spy_ledger_entries=len(benchmark),
        new_spy_ledger_entries=new_spy,
        new_outcomes_without_matching_spy=spy_missing,
        eligible_but_price_not_mature=eligible_unobserved,
        tracked_horizons_calendar_days=list(HORIZONS),
        notes=(
            "Prospective-only. Existing snapshot and outcome "
            "rows retained. SPY is sampled live; no retroactive "
            "benchmark reconstruction. Raw price comparisons "
            "ignore distributions, split discrepancies, costs, "
            "liquidity, execution, and risk. No demonstrated alpha."
        ),
    )

    return snaps, outcomes, benchmark, status


def _fetch_spy():
    """Fetch optional current SPY observation."""
    try:
        import yfinance as yf

        h = yf.download(
            "SPY",
            period="12d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )

        if h is None or h.empty:
            return None

        close = h["Close"]

        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        close = close.dropna()

        if close.empty:
            return None

        d = _utc(close.index[-1])
        v = _valid_price(close.iloc[-1])

        if pd.isna(d) or v is None:
            return None

        return dict(
            price_date=d.date().isoformat(),
            spy_close=float(v),
        )

    except Exception as e:
        print(
            "SPY sample unavailable:",
            type(e).__name__,
            str(e)[:150],
        )

        return None


def main():
    DATA.mkdir(exist_ok=True)

    scores = pd.read_csv(
        DATA / "investment_research_scores.csv",
        low_memory=False,
    )

    discovery = pd.read_csv(
        DATA / "discovery.csv",
        low_memory=False,
    )

    prev = _read_optional_csv(SNAP)
    old = _read_optional_csv(OUT)
    spy = _read_optional_csv(SPY)

    snaps, outcomes, benchmark, status = run(
        scores,
        discovery,
        old_snapshots=prev,
        old_outcomes=old,
        spy_history=spy,
        spy_observation=_fetch_spy(),
    )

    snaps.to_csv(
        SNAP,
        index=False,
    )

    _write_csv_with_columns(
        outcomes,
        OUT,
        OUTCOME_COLUMNS,
    )

    _write_csv_with_columns(
        benchmark,
        SPY,
        SPY_COLUMNS,
    )

    STATUS.write_text(
        json.dumps(status, indent=2) + "\n"
    )

    print(
        json.dumps(status, indent=2)
    )


if __name__ == "__main__":
    main()
