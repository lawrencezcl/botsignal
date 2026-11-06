"""Binance/CoinGecko HTTP client with weight awareness and endpoint failover."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger("flipscope_signal.http")

PRIMARY_FAPI = "https://www.binance.com/fapi/v1"
BACKUP_FAPI = "https://fapi.binance.com/fapi/v1"
COINGECKO = "https://api.coingecko.com/api/v3"

WEIGHT_SOFT = 1800  # of 2400/min — start pacing
WEIGHT_HARD = 2200


@dataclass
class WeightState:
    used_1m: int = 0
    last_endpoint: str = PRIMARY_FAPI
    circuit_open_until: float = 0.0  # epoch; primary banned (418)
    primary_banned: bool = False


@dataclass
class HttpClient:
    session: requests.Session = field(default_factory=requests.Session)
    weight: WeightState = field(default_factory=WeightState)
    user_agent: str = "flipscope-signal/0.1a (m1a-archive; read-only)"

    def __post_init__(self) -> None:
        self.session.headers.update(
            {"User-Agent": self.user_agent, "Accept": "application/json"}
        )

    def _active_fapi_base(self) -> str:
        if self.weight.primary_banned and time.time() < self.weight.circuit_open_until:
            return BACKUP_FAPI
        if self.weight.primary_banned and time.time() >= self.weight.circuit_open_until:
            # try primary again after cool-down
            self.weight.primary_banned = False
        return PRIMARY_FAPI

    def _pace(self) -> None:
        w = self.weight.used_1m
        if w >= WEIGHT_HARD:
            sleep_s = 5.0
        elif w >= WEIGHT_SOFT:
            sleep_s = 0.35
        else:
            sleep_s = 0.05
        time.sleep(sleep_s)

    def _update_weight(self, resp: requests.Response) -> None:
        raw = resp.headers.get("X-MBX-USED-WEIGHT-1M") or resp.headers.get(
            "x-mbx-used-weight-1m"
        )
        if raw is not None:
            try:
                self.weight.used_1m = int(raw)
            except ValueError:
                pass

    def get_json(
        self,
        url: str,
        params: dict | None = None,
        *,
        retries: int = 4,
        timeout: float = 30.0,
        allow_failover: bool = False,
    ) -> Any:
        """GET JSON. If allow_failover and url uses PRIMARY_FAPI, 418 flips to BACKUP."""
        last_err: Exception | None = None
        attempt_url = url
        for i in range(retries):
            self._pace()
            try:
                r = self.session.get(attempt_url, params=params, timeout=timeout)
                self._update_weight(r)
                if r.status_code == 429:
                    wait = 1.5 * (i + 1)
                    log.warning("429 rate limit on %s; sleep %.1fs", attempt_url, wait)
                    time.sleep(wait)
                    continue
                if r.status_code == 418:
                    log.error(
                        "418 IP ban on %s — circuit break to backup for 15m; do not hammer",
                        attempt_url,
                    )
                    self.weight.primary_banned = True
                    self.weight.circuit_open_until = time.time() + 900
                    if allow_failover and PRIMARY_FAPI in attempt_url:
                        attempt_url = attempt_url.replace(PRIMARY_FAPI, BACKUP_FAPI)
                        last_err = RuntimeError(f"418 on primary; switching to backup")
                        time.sleep(2.0)
                        continue
                    r.raise_for_status()
                if r.status_code == 451:
                    raise RuntimeError(f"HTTP 451 Unavailable For Legal Reasons: {attempt_url}")
                r.raise_for_status()
                self.weight.last_endpoint = attempt_url.rsplit("/", 1)[0]
                return r.json()
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (i + 1))
        raise RuntimeError(f"GET failed {url}: {last_err}")

    def fapi_get(self, path: str, params: dict | None = None, **kw: Any) -> Any:
        base = self._active_fapi_base()
        return self.get_json(
            f"{base}{path}",
            params,
            allow_failover=True,
            **kw,
        )

    def probe(self, url: str, timeout: float = 12.0) -> dict[str, Any]:
        t0 = time.time()
        out: dict[str, Any] = {
            "url": url,
            "ok": False,
            "status": None,
            "elapsed_ms": None,
            "error": None,
            "weight_1m": None,
            "body_snippet": None,
        }
        try:
            r = self.session.get(url, timeout=timeout)
            elapsed = int((time.time() - t0) * 1000)
            self._update_weight(r)
            out["status"] = r.status_code
            out["elapsed_ms"] = elapsed
            out["weight_1m"] = self.weight.used_1m or None
            out["body_snippet"] = (r.text or "")[:200]
            out["ok"] = 200 <= r.status_code < 300
            if r.status_code == 451:
                out["error"] = "451_geo_block"
            elif r.status_code == 418:
                out["error"] = "418_ip_ban"
        except Exception as e:
            out["elapsed_ms"] = int((time.time() - t0) * 1000)
            out["error"] = str(e)
        return out
