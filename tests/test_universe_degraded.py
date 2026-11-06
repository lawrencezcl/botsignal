"""Unit tests for degraded universe fallback (no network required for degrade path)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from flipscope_signal import paths as paths_mod
from flipscope_signal.universe import (
    degrade_from_prev,
    find_latest_snapshot,
    write_snapshot,
)


@pytest.fixture()
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    uni = tmp_path / "universe_snapshots"
    mt5 = tmp_path / "mt5_snapshots"
    uni.mkdir()
    mt5.mkdir()
    monkeypatch.setattr(paths_mod, "UNIVERSE_DIR", uni)
    monkeypatch.setattr(paths_mod, "MT5_SNAPSHOT_DIR", mt5)
    monkeypatch.setattr(paths_mod, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(paths_mod, "MT5_TRADEABLE_SRC", tmp_path / "missing_mt5.txt")
    return uni


def _seed(uni: Path, d: str, symbols: list[str]) -> None:
    doc = {
        "snapshot_date": d,
        "market": "binance_um_usdt_perp",
        "degraded_from_prev": False,
        "source": "test",
        "fetched_at": "2026-11-05T00:05:00+00:00",
        "top_n": 150,
        "symbol_count": len(symbols),
        "symbols": [
            {
                "symbol": s,
                "cg_symbol": s.replace("USDT", ""),
                "cg_id": s.lower(),
                "market_cap_rank": i + 1,
                "matched_via": "exact",
            }
            for i, s in enumerate(symbols)
        ],
        "mt5": {"included": False},
        "alert": None,
    }
    (uni / f"{d}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_degrade_reuses_prev_and_marks(isolated_data: Path):
    _seed(isolated_data, "2026-11-05", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    doc = degrade_from_prev(date(2026, 11, 6), reason="network down", include_mt5=False)
    assert doc["degraded_from_prev"] is True
    assert doc["degraded_from_date"] == "2026-11-05"
    assert doc["symbol_count"] == 3
    assert doc["symbols"][0]["symbol"] == "BTCUSDT"
    assert "DEGRADED" in (doc["alert"] or "")
    path = write_snapshot(doc)
    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded["degraded_from_prev"] is True
    assert len(loaded["symbols"]) == 3


def test_refuse_empty_write(isolated_data: Path):
    with pytest.raises(RuntimeError, match="empty"):
        write_snapshot(
            {
                "snapshot_date": "2026-11-06",
                "symbols": [],
                "degraded_from_prev": False,
            }
        )


def test_degrade_without_prior_raises(isolated_data: Path):
    with pytest.raises(RuntimeError, match="no prior snapshot"):
        degrade_from_prev(date(2026, 11, 6), reason="boom", include_mt5=False)


def test_find_latest(isolated_data: Path):
    _seed(isolated_data, "2026-11-04", ["BTCUSDT"])
    _seed(isolated_data, "2026-11-05", ["ETHUSDT"])
    found = find_latest_snapshot(prefer_before=date(2026, 11, 6))
    assert found is not None
    assert found[0] == date(2026, 11, 5)
