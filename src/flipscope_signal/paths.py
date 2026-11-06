"""Project data/layout paths (box-local, gitignored data/)."""
from __future__ import annotations

from pathlib import Path

# Package lives at src/flipscope_signal → repo root is parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"

UNIVERSE_DIR = DATA_ROOT / "universe_snapshots"
BARS_DIR = DATA_ROOT / "bars" / "binance_um"
MT5_BARS_DIR = DATA_ROOT / "bars" / "mt5"
CONNECTIVITY_DIR = DATA_ROOT / "connectivity"
MT5_SNAPSHOT_DIR = DATA_ROOT / "mt5_snapshots"
RUNS_DIR = DATA_ROOT / "runs"
LOG_DIR = DATA_ROOT / "logs"

# In-repo static fallback (class: mt5_all_tradeable). Override with FLIPSCOPE_MT5_TRADEABLE.
# Legacy external copy, used only when the repo fixture is absent:
#   /workspace/st_ema_scan/mt5_all_tradeable.txt
MT5_TRADEABLE_SRC = REPO_ROOT / "fixtures" / "mt5_all_tradeable.txt"
MT5_M5_FIXTURE_DIR = REPO_ROOT / "fixtures" / "mt5_m5"


def ensure_data_dirs() -> None:
    for d in (
        UNIVERSE_DIR,
        BARS_DIR,
        MT5_BARS_DIR,
        CONNECTIVITY_DIR,
        MT5_SNAPSHOT_DIR,
        RUNS_DIR,
        LOG_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
