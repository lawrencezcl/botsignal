"""Probe Binance dual endpoints; write daily connectivity record for VPS siting."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .http_client import BACKUP_FAPI, PRIMARY_FAPI, HttpClient
from .paths import CONNECTIVITY_DIR, ensure_data_dirs

log = logging.getLogger("flipscope_signal.connectivity")

PROBE_PATHS = [
    ("primary_ping", f"{PRIMARY_FAPI}/ping"),
    ("primary_time", f"{PRIMARY_FAPI}/time"),
    ("backup_ping", f"{BACKUP_FAPI}/ping"),
    ("backup_time", f"{BACKUP_FAPI}/time"),
    # bare host (docs often cite fapi.binance.com without /fapi/v1)
    ("fapi_host_ping", "https://fapi.binance.com/fapi/v1/ping"),
    ("www_host_ping", "https://www.binance.com/fapi/v1/ping"),
]


def run_connectivity_probe(client: HttpClient | None = None) -> dict[str, Any]:
    ensure_data_dirs()
    client = client or HttpClient()
    fetched_at = datetime.now(timezone.utc).isoformat()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    probes = []
    for name, url in PROBE_PATHS:
        result = client.probe(url)
        result["name"] = name
        probes.append(result)
        log.info(
            "probe %s status=%s ok=%s err=%s ms=%s",
            name,
            result.get("status"),
            result.get("ok"),
            result.get("error"),
            result.get("elapsed_ms"),
        )

    primary_ok = any(p["name"].startswith("primary_") and p["ok"] for p in probes) or any(
        p["name"] == "www_host_ping" and p["ok"] for p in probes
    )
    backup_ok = any(p["name"].startswith("backup_") and p["ok"] for p in probes) or any(
        p["name"] == "fapi_host_ping" and p["ok"] for p in probes
    )
    record = {
        "fetched_at": fetched_at,
        "day_utc": day,
        "primary_base": PRIMARY_FAPI,
        "backup_base": BACKUP_FAPI,
        "primary_reachable": primary_ok,
        "backup_reachable": backup_ok,
        "recommendation": _recommend(primary_ok, backup_ok, probes),
        "probes": probes,
    }
    out_path = CONNECTIVITY_DIR / f"{day}_binance.json"
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("wrote %s", out_path)
    return record


def _recommend(primary_ok: bool, backup_ok: bool, probes: list[dict]) -> str:
    codes = {p.get("status") for p in probes}
    if primary_ok and not backup_ok:
        return "prefer www.binance.com/fapi (fapi.binance.com unreachable or blocked from this host)"
    if backup_ok and not primary_ok:
        return "prefer fapi.binance.com (www path blocked from this host)"
    if primary_ok and backup_ok:
        return "both endpoints reachable; prefer www.binance.com/fapi/v1 with failover to fapi.binance.com"
    if 451 in codes:
        return "geo/legal block (451) detected — choose VPS outside blocked region; re-run probe on candidate VPS"
    return "neither endpoint reachable from this host — network or ban; re-run on VPS candidates"
