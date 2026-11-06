"""Project data/layout paths (box-local, gitignored data/)."""
from __future__ import annotations

from pathlib import Path

# Package lives at src/flipscope_signal → repo root is parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

UNIVERSE_DIR = DATA_ROOT / "universe_snapshots"
BARS_DIR = DATA_ROOT / "bars" / "binance_um"
CONNECTIVITY_DIR = DATA_ROOT / "connectivity"
MT5_SNAPSHOT_DIR = DATA_ROOT / "mt5_snapshots"
RUNS_DIR = DATA_ROOT / "runs"
LOG_DIR = DATA_ROOT / "logs"

# Optional static copy source (read-only reference from existing scan)
MT5_TRADEABLE_SRC = Path("/workspace/st_ema_scan/mt5_all_tradeable.txt")


def ensure_data_dirs() -> None:
    for d in (UNIVERSE_DIR, BARS_DIR, CONNECTIVITY_DIR, MT5_SNAPSHOT_DIR, RUNS_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
