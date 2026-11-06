"""Daily point-in-time universe snapshot (F-M8 / F-D6).

CoinGecko mcap rank → Binance USDT-M PERPETUAL, exclude stables, top N.
On failure: reuse previous day snapshot with degraded_from_prev=True; never write empty.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from datetime import date, datetime, timezone
from typing import Any

from . import paths as paths_mod
from .http_client import COINGECKO, HttpClient
from .paths import ensure_data_dirs

log = logging.getLogger("flipscope_signal.universe")

STABLE = {
    "USDT",
    "USDC",
    "DAI",
    "FDUSD",
    "TUSD",
    "USDP",
    "BUSD",
    "USDE",
    "USD1",
    "USDD",
    "PYUSD",
    "EUR",
    "USYC",
}
STABLE_ID_HINTS = (
    "tether",
    "usd-coin",
    "dai-",
    "first-digital-usd",
    "true-usd",
    "usdd",
    "ethena-usde",
    "paypal-usd",
    "binance-bridged-usdt",
)

DEFAULT_TOP_N = 150


def _base_asset(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


def fetch_perp_symbols(client: HttpClient) -> set[str]:
    info = client.fapi_get("/exchangeInfo")
    out: set[str] = set()
    for sym in info.get("symbols", []):
        if (
            sym.get("contractType") == "PERPETUAL"
            and sym.get("quoteAsset") == "USDT"
            and sym.get("status") == "TRADING"
        ):
            out.add(sym["symbol"])
    return out


def fetch_coingecko_ranked(client: HttpClient, pages: int = 3) -> list[dict]:
    rows: list[dict] = []
    for page in range(1, pages + 1):
        data = client.get_json(
            f"{COINGECKO}/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": page,
                "sparkline": "false",
            },
            retries=3,
            timeout=40.0,
        )
        if not data:
            break
        rows.extend(data)
        time.sleep(0.3)
    return rows


def match_top_n(cg_rows: list[dict], perps: set[str], top_n: int = DEFAULT_TOP_N) -> list[dict]:
    """Return list of {symbol, cg_symbol, cg_id, market_cap_rank, matched_via}."""
    base_to_symbol: dict[str, str] = {}
    for sym in perps:
        base = _base_asset(sym)
        base_to_symbol[base.upper()] = sym
        for pref in ("1000", "10000", "1M", "1M0"):
            if base.upper().startswith(pref) and len(base) > len(pref):
                bare = base.upper()[len(pref) :]
                base_to_symbol.setdefault(bare, sym)

    matched: list[dict] = []
    seen: set[str] = set()
    for row in cg_rows:
        tick = (row.get("symbol") or "").upper()
        if tick in STABLE:
            continue
        cid = (row.get("id") or "").lower()
        if any(x in cid for x in STABLE_ID_HINTS):
            continue

        cand = None
        via = None
        exact = f"{tick}USDT"
        if exact in perps:
            cand, via = exact, "exact"
        elif tick in base_to_symbol:
            cand, via = base_to_symbol[tick], "base_map"
        else:
            for pref in ("1000", "10000", "1M"):
                psym = f"{pref}{tick}USDT"
                if psym in perps:
                    cand, via = psym, f"prefix_{pref}"
                    break

        if cand and cand not in seen:
            seen.add(cand)
            matched.append(
                {
                    "symbol": cand,
                    "cg_symbol": tick,
                    "cg_id": row.get("id"),
                    "market_cap_rank": row.get("market_cap_rank"),
                    "matched_via": via,
                }
            )
        if len(matched) >= top_n:
            break
    return matched


def _snapshot_path(d: date) -> Path:
    return paths_mod.UNIVERSE_DIR / f"{d.isoformat()}.json"


def load_snapshot(d: date) -> dict[str, Any] | None:
    p = _snapshot_path(d)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def find_latest_snapshot(*, prefer_before: date | None = None) -> tuple[date, dict[str, Any]] | None:
    """Newest non-empty snapshot. If prefer_before set, prefer dates < prefer_before first."""
    ensure_data_dirs()
    files = sorted(paths_mod.UNIVERSE_DIR.glob("????-??-??.json"), reverse=True)
    parsed: list[tuple[date, dict[str, Any]]] = []
    for f in files:
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("symbols"):
            parsed.append((d, data))
    if not parsed:
        return None
    if prefer_before is not None:
        older = [x for x in parsed if x[0] < prefer_before]
        if older:
            return older[0]
    return parsed[0]


def copy_mt5_tradeable(snapshot_date: date) -> dict[str, Any] | None:
    """Optional static copy of MT5 tradeable list for the day.

    If ``snapshot_mt5`` already wrote this date, keep that file. The dedicated
    command is what tries the live terminal; this copy stays static.
    """
    ensure_data_dirs()
    dest = paths_mod.MT5_SNAPSHOT_DIR / f"{snapshot_date.isoformat()}.txt"
    if dest.exists() and dest.stat().st_size > 0:
        symbols = [
            ln.strip()
            for ln in dest.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        if symbols:
            log.info("MT5 snapshot already present, keeping %s (%d symbols)", dest, len(symbols))
            return {
                "path": str(dest),
                "source": "existing",
                "count": len(symbols),
                "symbols": symbols,
            }
    if not paths_mod.MT5_TRADEABLE_SRC.exists():
        log.warning("MT5 tradeable source missing: %s", paths_mod.MT5_TRADEABLE_SRC)
        return None
    shutil.copy2(paths_mod.MT5_TRADEABLE_SRC, dest)
    symbols = [
        ln.strip()
        for ln in dest.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    meta = {
        "path": str(dest),
        "source": str(paths_mod.MT5_TRADEABLE_SRC),
        "count": len(symbols),
        "symbols": symbols,
    }
    log.info("copied MT5 tradeable → %s (%d symbols)", dest, len(symbols))
    return meta


def build_live_snapshot(
    client: HttpClient,
    *,
    snapshot_date: date | None = None,
    top_n: int = DEFAULT_TOP_N,
    include_mt5: bool = True,
) -> dict[str, Any]:
    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    fetched_at = datetime.now(timezone.utc).isoformat()
    perps = fetch_perp_symbols(client)
    cg = fetch_coingecko_ranked(client, pages=3)
    matched = match_top_n(cg, perps, top_n=top_n)
    if not matched:
        raise RuntimeError("matched universe empty — refusing to write empty list")

    mt5_meta = copy_mt5_tradeable(snapshot_date) if include_mt5 else None
    return {
        "snapshot_date": snapshot_date.isoformat(),
        "market": "binance_um_usdt_perp",
        "degraded_from_prev": False,
        "source": "coingecko+binance_exchangeInfo",
        "fetched_at": fetched_at,
        "top_n": top_n,
        "symbol_count": len(matched),
        "perp_universe_size": len(perps),
        "coingecko_rows": len(cg),
        "symbols": matched,
        "mt5": (
            {
                "included": True,
                "path": mt5_meta["path"],
                "count": mt5_meta["count"],
                "symbols": mt5_meta["symbols"],
            }
            if mt5_meta
            else {"included": False}
        ),
        "alert": None,
    }


def degrade_from_prev(
    snapshot_date: date,
    *,
    reason: str,
    include_mt5: bool = True,
) -> dict[str, Any]:
    """Reuse previous non-empty snapshot; mark degraded; never empty."""
    found = find_latest_snapshot(prefer_before=snapshot_date)
    if found is None:
        # fall back to any including same-day prior successful write
        found = find_latest_snapshot(prefer_before=None)
    if found is None:
        raise RuntimeError(
            f"universe fetch failed and no prior snapshot to degrade to: {reason}"
        )
    prev_date, prev = found
    symbols = prev.get("symbols") or []
    if not symbols:
        raise RuntimeError("prior snapshot has empty symbols — refusing empty write")

    alert = (
        f"DEGRADED: live fetch failed ({reason}); "
        f"reusing snapshot from {prev.get('snapshot_date') or prev_date.isoformat()}"
    )
    log.error("%s", alert)
    print(alert, flush=True)

    mt5_meta = copy_mt5_tradeable(snapshot_date) if include_mt5 else None
    return {
        "snapshot_date": snapshot_date.isoformat(),
        "market": prev.get("market", "binance_um_usdt_perp"),
        "degraded_from_prev": True,
        "degraded_from_date": prev.get("snapshot_date") or prev_date.isoformat(),
        "source": prev.get("source", "coingecko+binance_exchangeInfo"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "top_n": prev.get("top_n"),
        "symbol_count": len(symbols),
        "symbols": symbols,
        "mt5": (
            {
                "included": True,
                "path": mt5_meta["path"],
                "count": mt5_meta["count"],
                "symbols": mt5_meta["symbols"],
            }
            if mt5_meta
            else prev.get("mt5") or {"included": False}
        ),
        "alert": alert,
        "prior_fetched_at": prev.get("fetched_at"),
    }


def write_snapshot(doc: dict[str, Any]) -> Path:
    ensure_data_dirs()
    symbols = doc.get("symbols") or []
    if not symbols:
        raise RuntimeError("refusing to write empty universe snapshot")
    d = date.fromisoformat(doc["snapshot_date"])
    path = _snapshot_path(d)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info(
        "wrote universe snapshot %s count=%d degraded=%s",
        path,
        len(symbols),
        doc.get("degraded_from_prev"),
    )
    return path


def run_snapshot_universe(
    *,
    snapshot_date: date | None = None,
    top_n: int = DEFAULT_TOP_N,
    include_mt5: bool = True,
    force_fail: bool = False,
    client: HttpClient | None = None,
) -> Path:
    """UTC 00:05 semantics: caller/cron chooses when; this writes today's (UTC) snapshot."""
    ensure_data_dirs()
    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    client = client or HttpClient()
    try:
        if force_fail:
            raise RuntimeError("forced failure (test hook)")
        doc = build_live_snapshot(
            client, snapshot_date=snapshot_date, top_n=top_n, include_mt5=include_mt5
        )
    except Exception as e:
        log.exception("live universe fetch failed: %s", e)
        doc = degrade_from_prev(snapshot_date, reason=str(e), include_mt5=include_mt5)
    return write_snapshot(doc)


def symbol_list_from_snapshot(doc: dict[str, Any]) -> list[str]:
    out = []
    for item in doc.get("symbols") or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("symbol"):
            out.append(item["symbol"])
    return out
