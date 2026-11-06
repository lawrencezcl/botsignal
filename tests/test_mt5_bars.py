"""MT5 snapshot fallback and idempotent 5m parquet merge (no terminal, no network)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from flipscope_signal import paths as paths_mod
from flipscope_signal.bars import merge_idempotent
from flipscope_signal.cli import build_parser
from flipscope_signal.mt5_bars import (
    MT5_COLS,
    history_to_frame,
    run_archive_mt5_bars,
    run_snapshot_mt5,
    validate_symbol,
)


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths_mod, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(paths_mod, "MT5_SNAPSHOT_DIR", tmp_path / "mt5_snapshots")
    monkeypatch.setattr(paths_mod, "MT5_BARS_DIR", tmp_path / "bars" / "mt5")
    monkeypatch.setattr(paths_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(paths_mod, "UNIVERSE_DIR", tmp_path / "universe_snapshots")
    monkeypatch.setattr(paths_mod, "BARS_DIR", tmp_path / "bars" / "binance_um")
    monkeypatch.setattr(paths_mod, "CONNECTIVITY_DIR", tmp_path / "connectivity")
    monkeypatch.setattr(paths_mod, "LOG_DIR", tmp_path / "logs")
    static = tmp_path / "mt5_all_tradeable.txt"
    static.write_text("# header\nEURUSDz\nXAUUSDz\n", encoding="utf-8")
    monkeypatch.setattr(paths_mod, "MT5_TRADEABLE_SRC", static)
    monkeypatch.delenv("FLIPSCOPE_MT5_TRADEABLE", raising=False)
    return tmp_path


def _bar(ts: str, run: str, volume: float = 10.0) -> dict:
    t = pd.Timestamp(ts, tz="UTC")
    return {
        "open_time": t,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "volume": volume,
        "close_time": t + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1),
        "source_run_id": run,
        "gap_filled": False,
        "is_sparse": volume <= 0,
    }


def test_merge_idempotent_keeps_first_source_run_id():
    existing = pd.DataFrame(
        [_bar("2026-11-05T00:00:00Z", "r1"), _bar("2026-11-05T00:05:00Z", "r1")]
    )
    incoming = pd.DataFrame(
        [_bar("2026-11-05T00:05:00Z", "r2", 99), _bar("2026-11-05T00:10:00Z", "r2")]
    )
    merged, inserted = merge_idempotent(existing, incoming)
    assert list(merged.columns) == MT5_COLS
    assert len(merged) == 3
    assert inserted == 1
    row = merged.loc[merged["open_time"] == pd.Timestamp("2026-11-05T00:05:00Z")].iloc[0]
    assert row["source_run_id"] == "r1"
    assert row["volume"] == 10.0


def test_history_volume_is_tick_volume_and_time_is_utc():
    frame = history_to_frame(
        [
            {
                "time": "2026-11-05T00:45:00",
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "tick_volume": 127,
            },
            {
                "time": 1793836800,
                "open": 2,
                "high": 2,
                "low": 2,
                "close": 2,
                "volume": 4,
            },
        ],
        "run-a",
    )
    assert list(frame.columns) == MT5_COLS
    assert frame.iloc[0]["open_time"] == pd.Timestamp("2026-11-05T00:45:00Z")
    assert frame.iloc[0]["volume"] == 127
    assert frame.iloc[0]["close_time"] == pd.Timestamp("2026-11-05T00:49:59.999Z")
    assert bool(frame.iloc[0]["is_sparse"]) is False
    assert frame.iloc[1]["open_time"] == pd.Timestamp("2026-11-05T00:00:00Z")
    assert frame.iloc[1]["volume"] == 4


def test_snapshot_prefers_dynamic_list(isolated: Path):
    doc = run_snapshot_mt5(
        snapshot_date=date(2026, 11, 6),
        dynamic_fetcher=lambda: ["XAUUSDz", "EURUSDz", "EURUSDz"],
    )
    assert doc["degraded_from_static"] is False
    assert doc["source"] == "metatrader5"
    assert doc["alert"] is None
    assert doc["symbols"] == ["EURUSDz", "XAUUSDz"]
    text = Path(doc["path"]).read_text(encoding="utf-8")
    assert "EURUSDz" in text
    assert "degraded_from_static: false" in text


def test_snapshot_falls_back_to_static_and_warns(isolated: Path, capsys: pytest.CaptureFixture[str]):
    def boom() -> list[str]:
        raise RuntimeError("no terminal")

    doc = run_snapshot_mt5(snapshot_date=date(2026, 11, 6), dynamic_fetcher=boom)
    err = capsys.readouterr().err
    assert "MT5_STATIC_FALLBACK" in err
    assert "no terminal" in err
    assert doc["degraded_from_static"] is True
    assert doc["source"] == "static"
    assert doc["symbols"] == ["EURUSDz", "XAUUSDz"]
    assert Path(doc["path"]).exists()


def test_snapshot_refuses_empty_when_both_fail(isolated: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(paths_mod, "MT5_TRADEABLE_SRC", isolated / "missing.txt")

    def boom() -> list[str]:
        raise RuntimeError("no terminal")

    with pytest.raises(RuntimeError, match="static fallback failed"):
        run_snapshot_mt5(snapshot_date=date(2026, 11, 6), dynamic_fetcher=boom)
    assert list((isolated / "mt5_snapshots").glob("*.txt")) == []


def test_archive_merge_is_idempotent(isolated: Path):
    jdir = isolated / "json"
    jdir.mkdir()
    payload = {
        "ok": True,
        "symbol": "EURUSDz",
        "history": [
            {
                "time": "2026-11-05T00:00:00",
                "open": 1,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "tick_volume": 5,
            },
            {
                "time": "2026-11-05T00:05:00",
                "open": 1.05,
                "high": 1.2,
                "low": 1.0,
                "close": 1.1,
                "tick_volume": 0,
            },
            {
                "time": "2099-01-01T00:00:00",
                "open": 9,
                "high": 9,
                "low": 9,
                "close": 9,
                "tick_volume": 1,
            },
        ],
    }
    (jdir / "EURUSDz.json").write_text(json.dumps(payload), encoding="utf-8")
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    first = run_archive_mt5_bars(from_json_dir=jdir, now=now)
    assert first["ok_count"] == 1
    assert first["results"][0]["inserted"] == 2
    assert first["results"][0]["dropped_forming"] == 1
    second = run_archive_mt5_bars(from_json_dir=jdir, now=now)
    assert second["results"][0]["inserted"] == 0
    assert second["results"][0]["rows_after"] == 2
    path = Path(first["results"][0]["path"])
    df = pd.read_parquet(path)
    assert list(df.columns) == MT5_COLS
    assert set(df["source_run_id"]) == {first["source_run_id"]}
    sparse = df.loc[df["open_time"] == pd.Timestamp("2026-11-05T00:05:00Z")].iloc[0]
    assert bool(sparse["is_sparse"]) is True
    assert float(sparse["volume"]) == 0.0

    payload["history"].insert(
        2,
        {
            "time": "2026-11-05T00:10:00",
            "open": 1.1,
            "high": 1.2,
            "low": 1.05,
            "close": 1.15,
            "tick_volume": 8,
        },
    )
    (jdir / "EURUSDz.json").write_text(json.dumps(payload), encoding="utf-8")
    third = run_archive_mt5_bars(from_json_dir=jdir, now=now)
    assert third["results"][0]["inserted"] == 1
    assert third["results"][0]["rows_after"] == 3
    df = pd.read_parquet(path)
    kept = df.loc[df["open_time"] == pd.Timestamp("2026-11-05T00:00:00Z")].iloc[0]
    added = df.loc[df["open_time"] == pd.Timestamp("2026-11-05T00:10:00Z")].iloc[0]
    assert kept["source_run_id"] == first["source_run_id"]
    assert added["source_run_id"] == third["source_run_id"]


def test_archive_limit_and_gap_from_repo_fixtures(isolated: Path):
    summary = run_archive_mt5_bars(
        limit=5,
        from_json_dir=paths_mod.MT5_M5_FIXTURE_DIR,
        now=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )
    assert summary["error_count"] == 0
    assert summary["ok_count"] == 5
    names = [r["symbol"] for r in summary["results"]]
    assert names == ["BTCUSDz", "EURUSDz", "GBPUSDz", "US500z", "USDJPYz"]
    assert summary["gap_summary"]["symbols_with_gaps"] == 1
    gbp = next(r for r in summary["results"] if r["symbol"] == "GBPUSDz")
    assert gbp["gaps"]["gap_count"] == 1
    assert gbp["gaps"]["missing_bars_est"] == 2
    eurusd = next(r for r in summary["results"] if r["symbol"] == "EURUSDz")
    assert eurusd["rows_after"] == 3
    assert eurusd["dropped_forming"] == 1
    written = list((isolated / "bars" / "mt5").glob("*/5m.parquet"))
    assert len(written) == 5


def test_live_fetcher_without_terminal_package(isolated: Path):
    def fake(symbol: str, count: int) -> list[dict]:
        assert symbol == "EURUSDz"
        assert count == 10
        return [
            {
                "time": "2026-11-05T04:00:00",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "tick_volume": 3,
            }
        ]

    summary = run_archive_mt5_bars(
        symbols=["EURUSDz"],
        lookback_bars=10,
        live_fetcher=fake,
        now=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )
    assert summary["ok_count"] == 1
    assert summary["from_json_dir"] is None
    df = pd.read_parquet(isolated / "bars" / "mt5" / "EURUSDz" / "5m.parquet")
    assert float(df.iloc[0]["volume"]) == 3


def test_missing_terminal_requires_json_dir(isolated: Path):
    with pytest.raises(RuntimeError, match="from-json-dir"):
        run_archive_mt5_bars(symbols=["EURUSDz"])


def test_symbol_mismatch_and_unsafe_name(isolated: Path):
    jdir = isolated / "json"
    jdir.mkdir()
    (jdir / "EURUSDz.json").write_text(
        json.dumps(
            {
                "ok": True,
                "symbol": "GBPUSDz",
                "history": [
                    {
                        "time": "2026-11-05T00:00:00",
                        "open": 1,
                        "high": 1,
                        "low": 1,
                        "close": 1,
                        "tick_volume": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    summary = run_archive_mt5_bars(
        from_json_dir=jdir,
        now=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )
    assert summary["error_count"] == 1
    with pytest.raises(ValueError, match="unsafe"):
        validate_symbol("../EURUSDz")


def test_cli_accepts_json_dir_and_preserves_symbol_case():
    args = build_parser().parse_args(
        [
            "archive_mt5_bars",
            "--limit",
            "5",
            "--symbol",
            "EURUSDz,XAUUSDz",
            "--from-json-dir",
            "fixtures/mt5_m5",
        ]
    )
    assert args.limit == 5
    assert args.symbol == "EURUSDz,XAUUSDz"
    assert args.from_json_dir == "fixtures/mt5_m5"
    snap = build_parser().parse_args(["snapshot_mt5", "--date", "2026-11-06", "--static-only"])
    assert snap.static_only is True
    assert snap.date == "2026-11-06"


def test_module_does_not_call_order_apis():
    text = Path(paths_mod.REPO_ROOT / "src" / "flipscope_signal" / "mt5_bars.py").read_text(
        encoding="utf-8"
    )
    for banned in ("order_send", "order_check", "trade_send", "trade_check", "TRADE_ACTION"):
        assert banned not in text
