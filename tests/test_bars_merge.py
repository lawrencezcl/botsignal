"""Idempotent parquet merge tests (no network)."""
from __future__ import annotations

import pandas as pd

from flipscope_signal.bars import detect_gaps, merge_idempotent


def _bar(ts: str, run: str = "r1") -> dict:
    t = pd.Timestamp(ts, tz="UTC")
    return {
        "open_time": t,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "volume": 10.0,
        "close_time": t + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1),
        "quote_volume": 10.0,
        "trades": 1,
        "taker_buy_base": 1.0,
        "taker_buy_quote": 1.0,
        "source_run_id": run,
        "gap_filled": False,
        "is_sparse": False,
    }


def test_merge_idempotent_no_dup():
    a = pd.DataFrame([_bar("2026-11-06T00:00:00Z", "r1"), _bar("2026-11-06T00:05:00Z", "r1")])
    b = pd.DataFrame([_bar("2026-11-06T00:05:00Z", "r2"), _bar("2026-11-06T00:10:00Z", "r2")])
    merged, inserted = merge_idempotent(a, b)
    assert len(merged) == 3
    assert inserted == 1
    # first-write wins on duplicate open_time
    row = merged.loc[merged["open_time"] == pd.Timestamp("2026-11-06T00:05:00Z", tz="UTC")].iloc[0]
    assert row["source_run_id"] == "r1"


def test_gap_detect():
    rows = [
        _bar("2026-11-06T00:00:00Z"),
        _bar("2026-11-06T00:05:00Z"),
        _bar("2026-11-06T00:20:00Z"),  # missing 00:10 and 00:15
    ]
    g = detect_gaps(pd.DataFrame(rows))
    assert g["gap_count"] == 1
    assert g["missing_bars_est"] == 2
