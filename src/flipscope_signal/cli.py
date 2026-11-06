"""CLI entrypoints: snapshot_universe / archive_bars / connectivity_probe."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone


def _setup_logging(verbose: bool) -> None:
    import time

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stderr,
    )
    logging.Formatter.converter = time.gmtime


def cmd_connectivity_probe(args: argparse.Namespace) -> int:
    from .connectivity import run_connectivity_probe

    rec = run_connectivity_probe()
    print(json.dumps({k: rec[k] for k in rec if k != "probes"}, ensure_ascii=False, indent=2))
    print(f"probes={len(rec['probes'])} primary={rec['primary_reachable']} backup={rec['backup_reachable']}")
    return 0 if (rec["primary_reachable"] or rec["backup_reachable"]) else 2


def cmd_snapshot_universe(args: argparse.Namespace) -> int:
    from .universe import run_snapshot_universe

    d = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    path = run_snapshot_universe(
        snapshot_date=d,
        top_n=args.top_n,
        include_mt5=not args.no_mt5,
        force_fail=args.force_fail,
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "path": str(path),
                "snapshot_date": doc["snapshot_date"],
                "symbol_count": doc["symbol_count"],
                "degraded_from_prev": doc["degraded_from_prev"],
                "alert": doc.get("alert"),
                "sample_symbols": [s["symbol"] if isinstance(s, dict) else s for s in doc["symbols"][:5]],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_snapshot_mt5(args: argparse.Namespace) -> int:
    from .mt5_bars import run_snapshot_mt5

    d = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    try:
        doc = run_snapshot_mt5(snapshot_date=d, force_static=args.static_only)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "path": doc["path"],
                "snapshot_date": doc["snapshot_date"],
                "symbol_count": doc["symbol_count"],
                "source": doc["source"],
                "degraded_from_static": doc["degraded_from_static"],
                "alert": doc.get("alert"),
                "sample_symbols": doc["sample_symbols"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_archive_mt5_bars(args: argparse.Namespace) -> int:
    from .mt5_bars import run_archive_mt5_bars

    symbols = None
    if args.symbol:
        symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    try:
        summary = run_archive_mt5_bars(
            symbols=symbols,
            limit=args.limit,
            from_json_dir=args.from_json_dir,
            lookback_bars=args.lookback,
            snapshot_date=args.snapshot_date,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    brief = {
        "source_run_id": summary["source_run_id"],
        "ok_count": summary["ok_count"],
        "error_count": summary["error_count"],
        "from_json_dir": summary["from_json_dir"],
        "gap_summary": summary["gap_summary"],
        "sample": [
            {
                "symbol": r.get("symbol"),
                "inserted": r.get("inserted"),
                "rows_after": r.get("rows_after"),
                "ok": r.get("ok"),
                "path": r.get("path"),
            }
            for r in summary["results"][:5]
        ],
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0 if summary["error_count"] == 0 else 1


def cmd_archive_bars(args: argparse.Namespace) -> int:
    from .bars import run_archive_bars

    symbols = None
    if args.symbol:
        symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
    summary = run_archive_bars(
        symbols=symbols,
        limit=args.limit,
        lookback_bars=args.lookback,
        snapshot_date=args.snapshot_date,
    )
    brief = {
        "source_run_id": summary["source_run_id"],
        "ok_count": summary["ok_count"],
        "error_count": summary["error_count"],
        "weight_1m": summary["weight_1m"],
        "gap_summary": summary["gap_summary"],
        "sample": [
            {
                "symbol": r.get("symbol"),
                "inserted": r.get("inserted"),
                "rows_after": r.get("rows_after"),
                "ok": r.get("ok"),
            }
            for r in summary["results"][:5]
        ],
    }
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0 if summary["error_count"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flipscope_signal",
        description="M-1a internal data accumulation (universe, Binance/MT5 bars, connectivity)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    p_conn = sub.add_parser("connectivity_probe", help="Probe Binance dual endpoints")
    p_conn.set_defaults(func=cmd_connectivity_probe)

    p_uni = sub.add_parser("snapshot_universe", help="Write daily universe snapshot (F-M8)")
    p_uni.add_argument("--date", help="UTC snapshot date YYYY-MM-DD (default: today UTC)")
    p_uni.add_argument("--top-n", type=int, default=150)
    p_uni.add_argument("--no-mt5", action="store_true", help="Skip MT5 tradeable copy")
    p_uni.add_argument(
        "--force-fail",
        action="store_true",
        help="Inject failure to exercise degraded_from_prev path",
    )
    p_uni.set_defaults(func=cmd_snapshot_universe)

    p_bars = sub.add_parser("archive_bars", help="Incremental 5m parquet archive")
    p_bars.add_argument("--limit", type=int, default=None, help="Only first N symbols from universe")
    p_bars.add_argument("--symbol", help="Comma-separated symbols (bypass universe)")
    p_bars.add_argument("--lookback", type=int, default=500, help="Bars to pull when empty")
    p_bars.add_argument("--snapshot-date", help="Use this universe snapshot date")
    p_bars.set_defaults(func=cmd_archive_bars)

    p_mt5 = sub.add_parser(
        "snapshot_mt5",
        help="Write daily MT5 tradeable list (dynamic, else static fallback)",
    )
    p_mt5.add_argument("--date", help="UTC snapshot date YYYY-MM-DD (default: today UTC)")
    p_mt5.add_argument(
        "--static-only",
        action="store_true",
        help="Skip the terminal and copy the static mt5_all_tradeable file",
    )
    p_mt5.set_defaults(func=cmd_snapshot_mt5)

    p_mt5_bars = sub.add_parser(
        "archive_mt5_bars",
        help="Archive closed MT5 5m bars to parquet (read-only)",
    )
    p_mt5_bars.add_argument("--limit", type=int, default=None, help="Only first N symbols")
    p_mt5_bars.add_argument("--symbol", help="Comma-separated symbols (case preserved)")
    p_mt5_bars.add_argument(
        "--from-json-dir",
        help="Directory of exported {ok, history:[...]} JSON (one file per symbol)",
    )
    p_mt5_bars.add_argument(
        "--lookback",
        type=int,
        default=500,
        help="M5 bars to read from a live terminal when JSON is not used",
    )
    p_mt5_bars.add_argument("--snapshot-date", help="MT5 snapshot date when not using JSON dir")
    p_mt5_bars.set_defaults(func=cmd_archive_mt5_bars)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
