"""MT5 read-only universe snapshot and 5m bar archive (M-1a).

Quotes come from the desktop MetaTrader 5 terminal or from JSON it exported.
A Linux VPS does not run the terminal; rsync ``data/`` after archival.
This module never calls order or trade-request APIs.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from . import paths as paths_mod
from .bars import (
    MS_5M,
    detect_gaps,
    drop_forming_candle,
    load_existing,
    merge_idempotent,
)
from .paths import ensure_data_dirs

log = logging.getLogger("flipscope_signal.mt5_bars")

INTERVAL = "5m"
# Symbol property values from the terminal. Reading them is not an order.
_MODE_DISABLED = 0
_MODE_CLOSEONLY = 3
_LEGACY_TRADEABLE = Path("/workspace/st_ema_scan/mt5_all_tradeable.txt")

MT5_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "source_run_id",
    "gap_filled",
    "is_sparse",
]

DynamicFetcher = Callable[[], list[str]]


def mt5_parquet_path(symbol: str, interval: str = INTERVAL) -> Path:
    safe = validate_symbol(symbol)
    return paths_mod.MT5_BARS_DIR / safe / f"{interval}.parquet"


def validate_symbol(symbol: str) -> str:
    s = (symbol or "").strip()
    if not s or s in {".", ".."} or "\\" in s or "/" in s or "\x00" in s or ".." in s:
        raise ValueError(f"unsafe MT5 symbol: {symbol!r}")
    return s


def resolve_static_tradeable_path() -> Path:
    """Repo fixture, unless FLIPSCOPE_MT5_TRADEABLE or a legacy scan file is set."""
    env = os.environ.get("FLIPSCOPE_MT5_TRADEABLE")
    if env:
        return Path(env)
    src = paths_mod.MT5_TRADEABLE_SRC
    if src.exists():
        return src
    if _LEGACY_TRADEABLE.exists():
        return _LEGACY_TRADEABLE
    return src


def read_symbol_file(path: Path) -> list[str]:
    symbols: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        symbols.append(validate_symbol(s))
    return symbols


def parse_server_time_as_utc(value: Any) -> pd.Timestamp:
    """Treat terminal server time as UTC (M-1a). DST reconciliation is later."""
    if isinstance(value, pd.Timestamp):
        ts = value
    elif isinstance(value, datetime):
        ts = pd.Timestamp(value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        unit = "ms" if abs(float(value)) > 10_000_000_000 else "s"
        return pd.to_datetime(float(value), unit=unit, utc=True)
    else:
        ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _tick_volume(bar: dict[str, Any]) -> float:
    if bar.get("tick_volume") is not None:
        return float(bar["tick_volume"])
    if bar.get("volume") is not None:
        return float(bar["volume"])
    return 0.0


def history_to_frame(history: list[dict[str, Any]], source_run_id: str) -> pd.DataFrame:
    """Map export bars onto the MT5 parquet schema. volume is tick_volume."""
    rows: list[dict[str, Any]] = []
    for bar in history:
        if not isinstance(bar, dict):
            raise ValueError("history item is not an object")
        open_time = parse_server_time_as_utc(bar["time"])
        vol = _tick_volume(bar)
        rows.append(
            {
                "open_time": open_time,
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": vol,
                "close_time": open_time + pd.Timedelta(milliseconds=MS_5M - 1),
                "source_run_id": source_run_id,
                "gap_filled": False,
                "is_sparse": vol <= 0,
            }
        )
    if not rows:
        return pd.DataFrame(columns=MT5_COLS)
    return pd.DataFrame(rows)[MT5_COLS]


def load_history_json(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    """Read ``{ok, symbol?, history:[{time,open,high,low,close,tick_volume}, ...]}``."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a JSON object")
    if doc.get("ok") is False:
        raise ValueError(f"{path} ok=false")
    history = doc.get("history")
    if not isinstance(history, list):
        raise ValueError(f"{path} missing history list")
    symbol = doc.get("symbol")
    return (str(symbol).strip() if symbol else None), history


def _stem_symbol(path: Path) -> str:
    stem = path.stem
    for suffix in (".5m", ".m5", ".M5"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return validate_symbol(stem)


def index_json_dir(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"MT5 JSON dir not found: {directory}")
    index: dict[str, Path] = {}
    for path in sorted(directory.glob("*.json")):
        symbol = _stem_symbol(path)
        index[symbol.casefold()] = path
    return index


def _write_tradeable(
    snapshot_date: date,
    symbols: list[str],
    *,
    source: str,
    degraded_from_static: bool,
    alert: str | None,
    static_path: str | None,
) -> Path:
    if not symbols:
        raise RuntimeError("refusing to write empty MT5 tradeable list")
    ensure_data_dirs()
    dest = paths_mod.MT5_SNAPSHOT_DIR / f"{snapshot_date.isoformat()}.txt"
    lines = [
        f"# source: {source}",
        f"# degraded_from_static: {str(degraded_from_static).lower()}",
        f"# fetched_at: {datetime.now(timezone.utc).isoformat()}",
        f"# symbol_count: {len(symbols)}",
    ]
    if static_path:
        lines.append(f"# static_path: {static_path}")
    if alert:
        lines.append("# alert: " + alert.replace("\n", " "))
    lines.extend(symbols)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(
        "wrote MT5 tradeable %s count=%d source=%s degraded=%s",
        dest,
        len(symbols),
        source,
        degraded_from_static,
    )
    return dest


def _load_static_symbols() -> tuple[list[str], Path]:
    path = resolve_static_tradeable_path()
    if not path.exists():
        raise RuntimeError(f"static MT5 tradeable file missing: {path}")
    symbols = read_symbol_file(path)
    if not symbols:
        raise RuntimeError(f"static MT5 tradeable file empty: {path}")
    return symbols, path


def fetch_dynamic_tradeable() -> list[str]:
    """Read-only terminal symbol list. Never places or modifies orders."""
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as e:
        raise RuntimeError("MetaTrader5 package not installed") from e

    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
    try:
        infos = mt5.symbols_get()
        if not infos:
            raise RuntimeError(f"symbols_get empty: {mt5.last_error()}")
        names: list[str] = []
        for info in infos:
            mode = int(getattr(info, "trade_mode", _MODE_DISABLED))
            if mode in (_MODE_DISABLED, _MODE_CLOSEONLY):
                continue
            name = getattr(info, "name", None)
            if name:
                names.append(validate_symbol(str(name)))
        if not names:
            raise RuntimeError("no symbols with a trade mode that allows new positions")
        return sorted(set(names))
    finally:
        mt5.shutdown()


def run_snapshot_mt5(
    *,
    snapshot_date: date | None = None,
    force_static: bool = False,
    dynamic_fetcher: DynamicFetcher | None = None,
) -> dict[str, Any]:
    """Write ``data/mt5_snapshots/YYYY-MM-DD.txt``.

    Prefer a live read-only symbol list. On failure, reuse the static
    ``mt5_all_tradeable`` file and warn. Never write an empty list.
    """
    ensure_data_dirs()
    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    fetcher = dynamic_fetcher or fetch_dynamic_tradeable
    source = "metatrader5"
    degraded = False
    alert: str | None = None
    static_path: str | None = None

    if force_static:
        symbols, path = _load_static_symbols()
        source = "static"
        static_path = str(path)
    else:
        try:
            symbols = [validate_symbol(s) for s in fetcher()]
            if not symbols:
                raise RuntimeError("dynamic tradeable list empty")
            # Stable, de-duplicated order
            symbols = sorted(set(symbols))
        except Exception as e:
            reason = str(e)
            log.warning("dynamic MT5 tradeable list failed: %s", reason)
            try:
                symbols, path = _load_static_symbols()
            except Exception as static_err:
                raise RuntimeError(
                    f"dynamic MT5 list failed ({reason}) and static fallback failed: {static_err}"
                ) from e
            source = "static"
            degraded = True
            static_path = str(path)
            alert = (
                f"MT5_STATIC_FALLBACK: dynamic tradeable list failed ({reason}); "
                f"using {path}"
            )
            log.warning("%s", alert)
            print(alert, file=sys.stderr, flush=True)

    dest = _write_tradeable(
        snapshot_date,
        symbols,
        source=source,
        degraded_from_static=degraded,
        alert=alert,
        static_path=static_path,
    )
    return {
        "path": str(dest),
        "snapshot_date": snapshot_date.isoformat(),
        "symbol_count": len(symbols),
        "source": source,
        "degraded_from_static": degraded,
        "alert": alert,
        "static_path": static_path,
        "sample_symbols": symbols[:5],
        "symbols": symbols,
    }


def _rates_to_history(rates: Any) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for bar in rates:
        history.append(
            {
                "time": int(bar["time"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "tick_volume": float(bar["tick_volume"]),
            }
        )
    return history


def fetch_live_m5(symbol: str, count: int) -> list[dict[str, Any]]:
    """Read-only M5 rates. ``time`` is stored as UTC for M-1a."""
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "no live MT5 terminal (MetaTrader5 not installed). "
            "Pass --from-json-dir with exported JSON. "
            "Linux VPS does not run the MT5 terminal; rsync data/ from the desktop."
        ) from e
    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize failed: {mt5.last_error()}")
    try:
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, int(count))
        if rates is None:
            raise RuntimeError(f"copy_rates_from_pos failed: {mt5.last_error()}")
        return _rates_to_history(rates)
    finally:
        mt5.shutdown()


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def archive_frame(
    symbol: str,
    frame: pd.DataFrame,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Idempotent upsert of already-mapped bars. Keeps the first source_run_id."""
    ensure_data_dirs()
    safe = validate_symbol(symbol)
    path = mt5_parquet_path(safe)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_existing(path, MT5_COLS)
    closed = drop_forming_candle(frame, now=now) if not frame.empty else frame
    merged, inserted = merge_idempotent(existing, closed)
    if not merged.empty:
        merged = merged[MT5_COLS].sort_values("open_time").reset_index(drop=True)
        merged.to_parquet(path, index=False)
    gaps = detect_gaps(merged)
    return {
        "symbol": safe,
        "path": str(path),
        "rows_before": int(len(existing)),
        "rows_after": int(len(merged)),
        "inserted": int(inserted),
        "dropped_forming": int(len(frame) - len(closed)) if not frame.empty else 0,
        "gaps": gaps,
        "ok": True,
        "sparse_rows": int(merged["is_sparse"].sum()) if not merged.empty else 0,
    }


def _symbols_for_archive(
    *,
    symbols: list[str] | None,
    limit: int | None,
    json_index: dict[str, Path] | None,
    snapshot_date: str | None,
) -> list[str]:
    if symbols is None:
        if json_index is not None:
            chosen = []
            for key in sorted(json_index):
                path = json_index[key]
                file_symbol = _stem_symbol(path)
                chosen.append(file_symbol)
            symbols = chosen
        else:
            symbols = _symbols_from_snapshot_or_static(snapshot_date)
    else:
        symbols = [validate_symbol(s) for s in symbols]
    if limit is not None:
        symbols = symbols[: max(0, limit)]
    return symbols


def _symbols_from_snapshot_or_static(snapshot_date: str | None) -> list[str]:
    ensure_data_dirs()
    if snapshot_date:
        path = paths_mod.MT5_SNAPSHOT_DIR / f"{snapshot_date}.txt"
        if not path.exists():
            raise FileNotFoundError(f"no MT5 snapshot for {snapshot_date}")
        return read_symbol_file(path)
    files = sorted(paths_mod.MT5_SNAPSHOT_DIR.glob("????-??-??.txt"), reverse=True)
    for path in files:
        symbols = read_symbol_file(path)
        if symbols:
            return symbols
    symbols, _ = _load_static_symbols()
    return symbols


def run_archive_mt5_bars(
    *,
    symbols: list[str] | None = None,
    limit: int | None = None,
    from_json_dir: Path | str | None = None,
    lookback_bars: int = 500,
    now: datetime | None = None,
    snapshot_date: str | None = None,
    live_fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Archive closed 5m bars to ``data/bars/mt5/{SYMBOL}/5m.parquet``.

    ``--from-json-dir`` reads one JSON per symbol. Without it, bars are read
    from a local MetaTrader 5 terminal (read-only). Linux VPS has no terminal.
    """
    ensure_data_dirs()
    source_run_id = _new_run_id()
    json_dir = Path(from_json_dir) if from_json_dir else None
    json_index = index_json_dir(json_dir) if json_dir is not None else None
    symbols = _symbols_for_archive(
        symbols=symbols,
        limit=limit,
        json_index=json_index,
        snapshot_date=snapshot_date,
    )
    if json_dir is None and live_fetcher is None:
        try:
            import MetaTrader5  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "no live MT5 terminal (MetaTrader5 not installed). "
                "Pass --from-json-dir with exported JSON. "
                "Linux VPS does not run the MT5 terminal; rsync data/ from the desktop."
            ) from e
        fetch_live = fetch_live_m5
    else:
        fetch_live = live_fetcher or fetch_live_m5

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for i, symbol in enumerate(symbols):
        try:
            if json_index is not None:
                path = json_index.get(symbol.casefold())
                if path is None:
                    raise FileNotFoundError(f"no JSON for {symbol} in {json_dir}")
                declared, history = load_history_json(path)
                if declared and declared.casefold() != symbol.casefold():
                    raise ValueError(
                        f"{path.name} symbol {declared!r} does not match filename {symbol!r}"
                    )
                if declared:
                    symbol = validate_symbol(declared)
            else:
                history = fetch_live(symbol, lookback_bars)
            frame = history_to_frame(history, source_run_id)
            result = archive_frame(symbol, frame, now=now)
            result["fetched_raw"] = len(history)
            results.append(result)
            log.info(
                "[%d/%d] %s inserted=%s rows=%s gaps=%s",
                i + 1,
                len(symbols),
                symbol,
                result["inserted"],
                result["rows_after"],
                result["gaps"].get("gap_count"),
            )
        except Exception as e:
            log.exception("mt5 archive failed %s: %s", symbol, e)
            errors.append({"symbol": symbol, "error": str(e)})
            results.append({"symbol": symbol, "ok": False, "error": str(e)})

    summary = {
        "source_run_id": source_run_id,
        "market": "mt5",
        "interval": INTERVAL,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "from_json_dir": str(json_dir) if json_dir else None,
        "symbol_count": len(symbols),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "error_count": len(errors),
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
    run_path = paths_mod.RUNS_DIR / f"archive_mt5_bars_{source_run_id}.json"
    run_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    log.info(
        "mt5 run summary → %s ok=%s err=%s",
        run_path,
        summary["ok_count"],
        summary["error_count"],
    )
    return summary
