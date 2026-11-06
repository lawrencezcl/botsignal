"""Incremental 5m bar archival to parquet (idempotent upsert by open_time)."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .http_client import HttpClient
from .paths import BARS_DIR, RUNS_DIR, UNIVERSE_DIR, ensure_data_dirs
from .universe import load_snapshot, symbol_list_from_snapshot

log = logging.getLogger("flipscope_signal.bars")

INTERVAL = "5m"
KLINE_LIMIT = 1000  # max per request
MS_5M = 5 * 60 * 1000

COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "source_run_id",
    "gap_filled",
    "is_sparse",
]


def parquet_path(symbol: str, interval: str = INTERVAL) -> Path:
    return BARS_DIR / symbol / f"{interval}.parquet"


def _raw_to_df(raw: list, source_run_id: str) -> pd.DataFrame:
    rows = []
    for k in raw:
        rows.append(
            {
                "open_time": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": pd.to_datetime(int(k[6]), unit="ms", utc=True),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_base": float(k[9]),
                "taker_buy_quote": float(k[10]),
                "source_run_id": source_run_id,
                "gap_filled": False,
                "is_sparse": False,
            }
        )
    return pd.DataFrame(rows)


def drop_forming_candle(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Keep only closed bars (close_time <= now)."""
    if df.empty:
        return df
    now = now or datetime.now(timezone.utc)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    return df.loc[df["close_time"] <= now_ts].copy()


def detect_gaps(df: pd.DataFrame, interval_ms: int = MS_5M) -> dict[str, Any]:
    if df.empty or len(df) < 2:
        return {"gap_count": 0, "missing_bars_est": 0, "first_gap_open_time": None}
    times = df["open_time"].sort_values().reset_index(drop=True)
    deltas = times.diff().dt.total_seconds() * 1000
    # expected step = interval_ms; allow tiny float noise
    gap_mask = deltas > (interval_ms * 1.5)
    gap_idxs = deltas[gap_mask].index.tolist()
    missing = 0
    first = None
    for i in gap_idxs:
        miss = int(round((deltas.iloc[i] / interval_ms) - 1))
        missing += max(miss, 0)
        if first is None:
            first = str(times.iloc[i - 1])
    return {
        "gap_count": len(gap_idxs),
        "missing_bars_est": missing,
        "first_gap_open_time": first,
    }


def load_existing(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    columns = list(COLS if columns is None else columns)
    if not path.exists():
        return pd.DataFrame(columns=columns)
    df = pd.read_parquet(path)
    if "open_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    if "close_time" in df.columns:
        df["close_time"] = pd.to_datetime(df["close_time"], utc=True)
    return df


def merge_idempotent(existing: pd.DataFrame, new: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Upsert by open_time; keep existing source_run_id on conflict (first-write wins)."""
    if new.empty:
        return existing, 0
    if existing.empty:
        out = new.drop_duplicates(subset=["open_time"], keep="first").sort_values("open_time")
        return out.reset_index(drop=True), len(out)
    # Prefer existing rows on duplicate open_time
    combined = pd.concat([existing, new], ignore_index=True)
    before = len(existing)
    combined = combined.drop_duplicates(subset=["open_time"], keep="first")
    combined = combined.sort_values("open_time").reset_index(drop=True)
    inserted = len(combined) - before
    return combined, max(inserted, 0)


def fetch_klines_page(
    client: HttpClient,
    symbol: str,
    *,
    interval: str = INTERVAL,
    limit: int = KLINE_LIMIT,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list:
    params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    return client.fapi_get("/klines", params)


def archive_symbol(
    client: HttpClient,
    symbol: str,
    *,
    source_run_id: str,
    lookback_bars: int = 500,
    interval: str = INTERVAL,
) -> dict[str, Any]:
    ensure_data_dirs()
    path = parquet_path(symbol, interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing(path)

    start_ms = None
    if not existing.empty:
        last_open = existing["open_time"].max()
        # fetch from last known open (overlap 1 bar for idempotency)
        start_ms = int(last_open.timestamp() * 1000)

    # Initial / catch-up: if empty or short history, pull lookback
    if existing.empty or start_ms is None:
        raw = fetch_klines_page(client, symbol, interval=interval, limit=min(lookback_bars, KLINE_LIMIT))
    else:
        raw = fetch_klines_page(
            client, symbol, interval=interval, limit=KLINE_LIMIT, start_ms=start_ms
        )

    new = drop_forming_candle(_raw_to_df(raw, source_run_id)) if raw else pd.DataFrame(columns=COLS)
    merged, inserted = merge_idempotent(existing, new)
    gaps = detect_gaps(merged)
    if not merged.empty:
        merged.to_parquet(path, index=False)

    return {
        "symbol": symbol,
        "path": str(path),
        "rows_before": len(existing),
        "rows_after": len(merged),
        "inserted": inserted,
        "fetched_raw": len(raw) if raw else 0,
        "gaps": gaps,
        "ok": True,
    }


def latest_universe_doc() -> dict[str, Any] | None:
    ensure_data_dirs()
    files = sorted(UNIVERSE_DIR.glob("????-??-??.json"), reverse=True)
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        if doc.get("symbols"):
            return doc
    return None


def run_archive_bars(
    *,
    symbols: list[str] | None = None,
    limit: int | None = None,
    lookback_bars: int = 500,
    client: HttpClient | None = None,
    snapshot_date: str | None = None,
) -> dict[str, Any]:
    ensure_data_dirs()
    client = client or HttpClient()
    source_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]

    if symbols is None:
        if snapshot_date:
            from datetime import date as date_cls

            doc = load_snapshot(date_cls.fromisoformat(snapshot_date))
            if doc is None:
                raise FileNotFoundError(f"no universe snapshot for {snapshot_date}")
        else:
            doc = latest_universe_doc()
            if doc is None:
                raise FileNotFoundError("no universe snapshot; run snapshot_universe first")
        symbols = symbol_list_from_snapshot(doc)
    if limit is not None:
        symbols = symbols[: max(0, limit)]

    results = []
    errors = []
    for i, sym in enumerate(symbols):
        try:
            r = archive_symbol(
                client, sym, source_run_id=source_run_id, lookback_bars=lookback_bars
            )
            results.append(r)
            log.info(
                "[%d/%d] %s inserted=%s rows=%s gaps=%s",
                i + 1,
                len(symbols),
                sym,
                r["inserted"],
                r["rows_after"],
                r["gaps"].get("gap_count"),
            )
        except Exception as e:
            log.exception("archive failed %s: %s", sym, e)
            errors.append({"symbol": sym, "error": str(e)})
            results.append({"symbol": sym, "ok": False, "error": str(e)})

    summary = {
        "source_run_id": source_run_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "symbol_count": len(symbols),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "error_count": len(errors),
        "weight_1m": client.weight.used_1m,
        "last_endpoint": client.weight.last_endpoint,
        "results": results,
        "errors": errors,
        "gap_summary": {
            "symbols_with_gaps": sum(
                1 for r in results if r.get("ok") and (r.get("gaps") or {}).get("gap_count", 0) > 0
            ),
            "total_missing_bars_est": sum(
                (r.get("gaps") or {}).get("missing_bars_est", 0) for r in results if r.get("ok")
            ),
        },
    }
    run_path = RUNS_DIR / f"archive_bars_{source_run_id}.json"
    run_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    log.info("run summary → %s ok=%s err=%s", run_path, summary["ok_count"], summary["error_count"])
    return summary
