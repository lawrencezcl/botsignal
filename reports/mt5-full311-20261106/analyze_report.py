#!/usr/bin/env python3
"""MT5 full Market Watch technical screen → Chinese HTML + JSON. Read-only."""
from __future__ import annotations
import json, math, html
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

ROOT = Path("/workspace/mt5_full311_20261106")
BARS = ROOT / "bars"
PERIODS = ["M5", "M15", "H1", "H4", "D1"]
TZ8 = timezone(timedelta(hours=8))
REF_NOW = datetime(2026, 11, 6, 13, 15)  # trade-server-ish snapshot for stale check
STALE_DAYS = 5

def ema(vals, n):
    if not vals: return []
    k = 2 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def atr_ema(h, l, c, n=20):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return ema(tr, n)

def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) < n + 1:
        return out
    gains = []; losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_g = sum(gains[:n]) / n; avg_l = sum(losses[:n]) / n
    def rv(ag, al):
        if al == 0: return 100.0
        return 100 - 100 / (1 + ag / al)
    out[n] = rv(avg_g, avg_l)
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
        out[i + 1] = rv(avg_g, avg_l)
    return out

def macd(closes, fast=12, slow=26, signal=9):
    line = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    sig = ema(line, signal)
    hist = [a - b for a, b in zip(line, sig)]
    return line, sig, hist

def adx(h, l, c, n=14):
    if len(c) < n * 2:
        return [None] * len(c)
    plus_dm = [0.0]; minus_dm = [0.0]; tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        up = h[i] - h[i - 1]; down = l[i - 1] - l[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    def wilder(arr, n):
        out = [None] * len(arr)
        s = sum(arr[1:n + 1]); out[n] = s
        for i in range(n + 1, len(arr)):
            s = s - s / n + arr[i]; out[i] = s
        return out
    str_ = wilder(tr, n); sp = wilder(plus_dm, n); sm = wilder(minus_dm, n)
    dx = [None] * len(c)
    for i in range(len(c)):
        if str_[i] and str_[i] != 0 and sp[i] is not None and sm[i] is not None:
            pdi = 100 * sp[i] / str_[i]; mdi = 100 * sm[i] / str_[i]
            denom = pdi + mdi
            dx[i] = 0 if denom == 0 else abs(pdi - mdi) / denom * 100
    out = [None] * len(c)
    vals = [(i, v) for i, v in enumerate(dx) if v is not None]
    if len(vals) < n:
        return out
    s = sum(v for _, v in vals[:n]); out[vals[n - 1][0]] = s / n
    prev = s / n
    for i, v in vals[n:]:
        prev = (prev * (n - 1) + v) / n
        out[i] = prev
    return out

def supertrend(h, l, c, atr_period=20, mult=3.0):
    a = atr_ema(h, l, c, atr_period)
    n = len(c)
    st = [None] * n; direction = [0] * n
    upper = [None] * n; lower = [None] * n
    for i in range(n):
        if a[i] is None:
            continue
        mid = (h[i] + l[i]) / 2
        basic_u = mid + mult * a[i]; basic_l = mid - mult * a[i]
        if i == 0 or upper[i - 1] is None:
            upper[i] = basic_u; lower[i] = basic_l; direction[i] = 1; st[i] = lower[i]
            continue
        upper[i] = basic_u if (basic_u < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = basic_l if (basic_l > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]
        d = direction[i - 1]
        if d == 1:
            if c[i] < lower[i]:
                d = -1; st[i] = upper[i]
            else:
                st[i] = lower[i]
        else:
            if c[i] > upper[i]:
                d = 1; st[i] = lower[i]
            else:
                st[i] = upper[i]
        direction[i] = d
    return st, direction, a

def pivot_levels(h, l, c, look=40):
    if len(c) < 5:
        return None, None
    window = min(look, len(c) - 1)
    hh = max(h[-window - 1:-1] or h[-window:])
    ll = min(l[-window - 1:-1] or l[-window:])
    return ll, hh

def bias_from(e20, e50, e200, st_dir, adxv, rsi_v, macd_hist):
    score = 0; notes = []
    if e20 is not None and e50 is not None:
        if e20 > e50: score += 1; notes.append("EMA20>50")
        elif e20 < e50: score -= 1; notes.append("EMA20<50")
    if e50 is not None and e200 is not None:
        if e50 > e200: score += 1; notes.append("EMA50>200")
        elif e50 < e200: score -= 1; notes.append("EMA50<200")
    if st_dir == 1: score += 1; notes.append("ST多")
    elif st_dir == -1: score -= 1; notes.append("ST空")
    if macd_hist is not None:
        score += 0.5 if macd_hist > 0 else (-0.5 if macd_hist < 0 else 0)
    if rsi_v is not None:
        if rsi_v >= 70: notes.append("RSI超买")
        elif rsi_v <= 30: notes.append("RSI超卖")
    strong = adxv is not None and adxv >= 25
    if abs(score) < 1.0:
        bias = "震荡"
    elif score >= 1.5:
        bias = "多" if strong else "偏多"
    elif score <= -1.5:
        bias = "空" if strong else "偏空"
    elif score > 0:
        bias = "偏多"
    else:
        bias = "偏空"
    return bias, score, notes, strong

def analyze_series(history):
    if not history or len(history) < 30:
        return {"ok": False, "reason": "bars不足", "bars": len(history or [])}
    hist = history[:-1] if len(history) >= 31 else history
    h = [x["high"] for x in hist]; l = [x["low"] for x in hist]; c = [x["close"] for x in hist]
    e20 = ema(c, 20); e50 = ema(c, 50)
    e200 = ema(c, 200) if len(c) >= 200 else ema(c, min(200, len(c)))
    st, direc, atrv = supertrend(h, l, c, 20, 3.0)
    r = rsi(c, 14); _ml, _ms, mh = macd(c); ad = adx(h, l, c, 14)
    i = -1
    price = c[i]
    e20v, e50v, e200v = e20[i], e50[i], e200[i]
    st_dir = direc[i]; atr_now = atrv[i]; rsi_v = r[i]; macd_h = mh[i]; adx_v = ad[i]
    bias, score, notes, strong = bias_from(e20v, e50v, e200v, st_dir, adx_v, rsi_v, macd_h)
    sup, res = pivot_levels(h, l, c, 40)
    last_t = hist[-1].get("time")
    stale = False; age_days = None
    try:
        lt = datetime.fromisoformat(last_t)
        age_days = (REF_NOW - lt).total_seconds() / 86400
        stale = age_days > STALE_DAYS
    except Exception:
        pass
    if stale:
        notes = list(notes) + [f"行情陈旧约{age_days:.0f}天"]
    executable = False; plan = "观望"; rr = None; entry = None; sl = None; tp = None; conf = 0.4
    if stale:
        plan = "行情缺口/陈旧，不可执行"; conf = 0.15
    elif atr_now and atr_now > 0 and bias in ("多", "偏多", "空", "偏空"):
        conf = 0.55 + (0.15 if strong else 0) + (0.1 if abs(score) >= 2.5 else 0)
        conf = min(conf, 0.92)
        if bias in ("多", "偏多") and st_dir == 1 and (rsi_v is None or rsi_v < 68):
            entry = price
            st_lvl = st[i]
            sl = min(price - 0.2 * atr_now, st_lvl if st_lvl else price - 1.5 * atr_now)
            if sl >= price:
                sl = price - 1.2 * atr_now
            risk = price - sl
            tp = price + 2.0 * risk
            rr = round((tp - price) / risk, 2) if risk > 0 else None
            if risk > 0 and rr and rr >= 1.5:
                executable = True; plan = "回踩/顺势多（只读思路）"
        elif bias in ("空", "偏空") and st_dir == -1 and (rsi_v is None or rsi_v > 32):
            entry = price
            st_lvl = st[i]
            sl = max(price + 0.2 * atr_now, st_lvl if st_lvl else price + 1.5 * atr_now)
            if sl <= price:
                sl = price + 1.2 * atr_now
            risk = sl - price
            tp = price - 2.0 * risk
            rr = round((price - tp) / risk, 2) if risk > 0 else None
            if risk > 0 and rr and rr >= 1.5:
                executable = True; plan = "反抽/顺势空（只读思路）"
        if not executable:
            plan = "结构未齐，观望"; conf = min(conf, 0.5)
    return {
        "ok": True, "bars": len(hist), "last_bar_time": last_t, "stale": stale,
        "age_days": round(age_days, 1) if age_days is not None else None,
        "close": price,
        "ema20": round(e20v, 6) if e20v is not None else None,
        "ema50": round(e50v, 6) if e50v is not None else None,
        "ema200": round(e200v, 6) if e200v is not None else None,
        "supertrend": round(st[i], 6) if st[i] is not None else None,
        "st_dir": "多" if st_dir == 1 else ("空" if st_dir == -1 else "—"),
        "atr20": round(atr_now, 6) if atr_now is not None else None,
        "rsi14": round(rsi_v, 2) if rsi_v is not None else None,
        "macd_hist": round(macd_h, 6) if macd_h is not None else None,
        "adx14": round(adx_v, 2) if adx_v is not None else None,
        "bias": bias, "score": round(score, 2), "trend_strong": strong,
        "support": round(sup, 6) if sup is not None else None,
        "resistance": round(res, 6) if res is not None else None,
        "notes": notes, "executable": executable, "plan": plan,
        "entry": round(entry, 6) if entry is not None else None,
        "sl": round(sl, 6) if sl is not None else None,
        "tp": round(tp, 6) if tp is not None else None,
        "rr": rr, "confidence": round(conf, 2),
    }

def load_quotes(path: Path):
    quotes = {}
    if not path.exists():
        return quotes
    j = json.loads(path.read_text())
    items = j.get("symbols") if isinstance(j, dict) else j
    if not isinstance(items, list):
        return quotes
    for it in items:
        name = it.get("symbol") or it.get("name")
        if not name:
            continue
        quotes[name] = {
            "bid": it.get("bid"), "ask": it.get("ask"), "spread": it.get("spread"),
            "description": it.get("description") or "", "category": it.get("category") or "",
            "digits": it.get("digits"),
        }
    return quotes

def overall_bias(tfmap):
    w = {"D1": 3, "H4": 2.5, "H1": 2, "M15": 1.2, "M5": 0.8}
    score = 0; tw = 0; stale_all = True; any_ok = False
    for p, a in tfmap.items():
        if not a or not a.get("ok"):
            continue
        any_ok = True
        if not a.get("stale"):
            stale_all = False
        b = a["bias"]; s = {"多": 2, "偏多": 1, "偏空": -1, "空": -2}.get(b, 0)
        # downweight stale
        ww = w.get(p, 1) * (0.15 if a.get("stale") else 1)
        score += s * ww; tw += ww
    if not any_ok:
        return "缺口", 0, True
    if tw == 0:
        return "缺口", 0, True
    v = score / tw
    if v >= 1.0: ob = "多"
    elif v >= 0.35: ob = "偏多"
    elif v <= -1.0: ob = "空"
    elif v <= -0.35: ob = "偏空"
    else: ob = "震荡"
    return ob, round(v, 2), stale_all

def main():
    now = datetime.now(TZ8)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    account = json.loads((ROOT / "account.json").read_text()) if (ROOT / "account.json").exists() else {}
    acct = account.get("account") or account
    quotes = load_quotes(ROOT / "marketwatch.json")

    by_sym = defaultdict(dict)
    failures = []
    for f in BARS.glob("*__*.json"):
        if f.name.endswith(".FAIL.json"):
            try:
                failures.append(json.loads(f.read_text()))
            except Exception:
                failures.append({"file": f.name})
            continue
        sym, period = f.stem.rsplit("__", 1)
        try:
            j = json.loads(f.read_text())
        except Exception as e:
            failures.append({"symbol": sym, "period": period, "error": f"json:{e}"})
            continue
        hist = j.get("history") or []
        if not j.get("ok") or not hist:
            failures.append({"symbol": sym, "period": period, "error": "empty_history", "data_available_from": j.get("data_available_from")})
            by_sym[sym][period] = {"ok": False, "reason": "行情缺口"}
            continue
        a = analyze_series(hist)
        by_sym[sym][period] = a
        if not a.get("ok"):
            failures.append({"symbol": sym, "period": period, "error": a.get("reason"), "bars": a.get("bars")})
        elif a.get("stale"):
            failures.append({"symbol": sym, "period": period, "error": "stale_bars", "last_bar_time": a.get("last_bar_time"), "age_days": a.get("age_days")})

    summary = []
    for sym in sorted(by_sym.keys()):
        tf = by_sym[sym]
        ob, ov, stale_all = overall_bias(tf)
        q = quotes.get(sym) or {}
        price = q.get("bid")
        if price is None:
            for p in ("M5", "M15", "H1", "H4", "D1"):
                a = tf.get(p)
                if a and a.get("ok"):
                    price = a["close"]; break
        best = None; best_tf = None
        for p in ("M15", "H1", "M5", "H4"):
            a = tf.get(p)
            if a and a.get("ok") and a.get("executable") and not a.get("stale"):
                best = a; best_tf = p; break
        if best is None:
            cands = []
            for p in PERIODS:
                a = tf.get(p)
                if a and a.get("ok") and not a.get("stale"):
                    cands.append((a.get("confidence") or 0, p, a))
            cands.sort(reverse=True)
            if cands:
                _, best_tf, best = cands[0]
            else:
                # fall back to any including stale
                for p in PERIODS:
                    a = tf.get(p)
                    if a and a.get("ok"):
                        best = a; best_tf = p; break
        row = {
            "symbol": sym,
            "description": q.get("description") or "",
            "category": q.get("category") or "",
            "bid": q.get("bid"), "ask": q.get("ask"), "spread": q.get("spread"),
            "price": price, "overall_bias": ob, "overall_score": ov,
            "stale_all": stale_all,
            "timeframes": {p: tf.get(p) for p in PERIODS},
            "best_tf": best_tf,
            "plan": (best or {}).get("plan"),
            "entry": (best or {}).get("entry"),
            "sl": (best or {}).get("sl"),
            "tp": (best or {}).get("tp"),
            "rr": (best or {}).get("rr"),
            "confidence": (best or {}).get("confidence"),
            "executable": bool((best or {}).get("executable")),
            "support": (best or {}).get("support"),
            "resistance": (best or {}).get("resistance"),
            "notes": (best or {}).get("notes") or [],
            "bias_M5": (tf.get("M5") or {}).get("bias"),
            "bias_M15": (tf.get("M15") or {}).get("bias"),
            "bias_H1": (tf.get("H1") or {}).get("bias"),
            "bias_H4": (tf.get("H4") or {}).get("bias"),
            "bias_D1": (tf.get("D1") or {}).get("bias"),
        }
        summary.append(row)

    tops = sorted([r for r in summary if r.get("executable") and not r.get("stale_all")],
                  key=lambda r: (-(r.get("confidence") or 0), -abs(r.get("overall_score") or 0)))
    obs = sorted([r for r in summary if not r.get("executable") and not r.get("stale_all")],
                 key=lambda r: (-(r.get("confidence") or 0), -abs(r.get("overall_score") or 0)))
    top_list = []
    keys = ("symbol", "price", "overall_bias", "best_tf", "plan", "entry", "sl", "tp", "rr",
            "confidence", "support", "resistance", "bias_M15", "bias_H1", "bias_H4", "bias_D1", "notes", "description", "category")
    for r in tops[:20]:
        top_list.append({k: r[k] for k in keys})
    for r in obs:
        if len(top_list) >= 30:
            break
        if (r.get("confidence") or 0) >= 0.55 and r.get("overall_bias") in ("多", "空", "偏多", "偏空"):
            item = {k: r[k] for k in keys}; item["observe_only"] = True
            top_list.append(item)

    # success = has at least one non-stale ok TF
    success_n = sum(1 for r in summary if not r.get("stale_all") and any(
        (r["timeframes"].get(p) or {}).get("ok") and not (r["timeframes"].get(p) or {}).get("stale") for p in PERIODS))
    stale_n = sum(1 for r in summary if r.get("stale_all"))
    gap_n = sum(1 for r in summary if r.get("overall_bias") == "缺口")

    meta = {
        "title": "MT5 Market Watch 全品种深度技术分析",
        "generated_at_asia_shanghai": stamp,
        "timezone": "Asia/Shanghai (UTC+8)",
        "server": acct.get("server") or "Exness-MT5Real5",
        "broker": acct.get("broker") or "Exness Technologies Ltd",
        "login": acct.get("login"),
        "equity": acct.get("equity"),
        "balance": acct.get("balance"),
        "currency": acct.get("currency") or "USD",
        "symbols_in_watch": len(quotes) or len(summary),
        "symbols_analyzed": len(summary),
        "symbols_fresh_ok": success_n,
        "symbols_stale": stale_n,
        "symbols_gap": gap_n,
        "failures_count": len(failures),
        "periods": PERIODS,
        "disclaimer": "经纪商 CFD；Tick volume 非交易所量；默认不下单；PCR/GEX 不可用；只读分析。",
        "indicators": "EMA20/50/200, Supertrend(ATR20×3), ATR20, MACD(12,26,9), RSI14, ADX14；已收盘K线（去掉最后一根可能未收盘）。陈旧判定：末根K线距快照>5天。",
    }

    # slim summary for file size: drop full OHLC-less but keep tf metrics
    slim = []
    for r in summary:
        slim.append({k: v for k, v in r.items()})

    (ROOT / "summary.json").write_text(json.dumps({"meta": meta, "symbols": slim}, ensure_ascii=False), encoding="utf-8")
    (ROOT / "top_opportunities.json").write_text(json.dumps({"meta": meta, "items": top_list}, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")

    def esc(x):
        return html.escape("" if x is None else str(x))

    def bias_class(b):
        return {"多": "bull", "偏多": "bullish", "空": "bear", "偏空": "bearish", "震荡": "chop", "缺口": "gap"}.get(b or "", "")

    rows_html = []
    for r in summary:
        rows_html.append(
            "<tr data-sym='{sym}' data-bias='{ob}' data-exec='{ex}' data-stale='{st}'>"
            "<td><b>{sym}</b><div class='sub'>{desc}</div></td><td>{px}</td>"
            "<td class='{c}'>{ob}</td>"
            "<td>{m5}</td><td>{m15}</td><td>{h1}</td><td>{h4}</td><td>{d1}</td>"
            "<td>{sup}</td><td>{res}</td>"
            "<td>{plan}</td><td>{rr}</td><td>{conf}</td><td>{note}</td></tr>".format(
                sym=esc(r["symbol"]), desc=esc((r.get("description") or "")[:40]),
                px=esc(r.get("price")), ob=esc(r.get("overall_bias")),
                c=bias_class(r.get("overall_bias")),
                m5=esc(r.get("bias_M5")), m15=esc(r.get("bias_M15")), h1=esc(r.get("bias_H1")),
                h4=esc(r.get("bias_H4")), d1=esc(r.get("bias_D1")),
                sup=esc(r.get("support")), res=esc(r.get("resistance")),
                plan=esc(r.get("plan")), rr=esc(r.get("rr")), conf=esc(r.get("confidence")),
                note=esc("；".join(r.get("notes") or [])[:100]),
                ex="1" if r.get("executable") else "0",
                st="1" if r.get("stale_all") else "0",
            )
        )

    top_rows = []
    for i, r in enumerate(top_list, 1):
        top_rows.append(
            "<tr><td>{i}</td><td><b>{sym}</b></td><td class='{c}'>{ob}</td><td>{tf}</td><td>{px}</td>"
            "<td>{en}</td><td>{sl}</td><td>{tp}</td><td>{rr}</td><td>{cf}</td><td>{plan}</td>"
            "<td id='pi-{sym}' class='pi'></td></tr>".format(
                i=i, sym=esc(r["symbol"]), ob=esc(r.get("overall_bias")), c=bias_class(r.get("overall_bias")),
                tf=esc(r.get("best_tf")), px=esc(r.get("price")), en=esc(r.get("entry")),
                sl=esc(r.get("sl")), tp=esc(r.get("tp")), rr=esc(r.get("rr")),
                cf=esc(r.get("confidence")), plan=esc(r.get("plan")),
            )
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{esc(meta['title'])} · {esc(stamp)} Asia/Shanghai</title>
<style>
:root {{ --bg:#0b1220; --card:#121a2b; --line:#243047; --text:#e8eef9; --muted:#9db0cc; --bull:#26a69a; --bear:#ef5350; --warn:#ffcc80; }}
*{{box-sizing:border-box}} body{{margin:0;font-family:"PingFang SC","Noto Sans CJK SC",sans-serif;background:var(--bg);color:var(--text);line-height:1.5}}
header{{padding:20px 24px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#152038,#0b1220)}}
h1{{margin:0 0 8px;font-size:22px}} .meta{{color:var(--muted);font-size:13px}} .sub{{color:var(--muted);font-size:11px;font-weight:400}}
.note{{margin:12px 24px;padding:12px 14px;border:1px solid #5d4a1f;background:#2a210f;border-radius:10px;color:var(--warn);font-size:13px}}
.wrap{{padding:12px 24px 40px}}
.controls{{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 16px;align-items:center}}
input,select,button{{background:#0e1626;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:8px 10px}}
button{{cursor:pointer}} table{{width:100%;border-collapse:collapse;font-size:12.5px;background:var(--card)}}
th,td{{border:1px solid var(--line);padding:6px 8px;text-align:left;white-space:nowrap}} th{{position:sticky;top:0;background:#1a2438;z-index:1}}
tr:hover{{background:#182338}} .bull,.bullish{{color:#26a69a;font-weight:600}} .bear,.bearish{{color:#ef5350;font-weight:600}} .chop{{color:#ffcc80}} .gap{{color:#90a4ae}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}} .card b{{font-size:18px}}
.section{{margin-top:22px}} .pi{{white-space:normal;max-width:280px;font-size:12px;color:#b3e5fc}}
</style>
</head>
<body>
<header>
  <h1>{esc(meta['title'])}</h1>
  <div class="meta">快照时间：{esc(stamp)}（Asia/Shanghai）｜服务器：{esc(meta['server'])}｜经纪商：{esc(meta['broker'])}｜账户 {esc(meta.get('login'))}｜权益 {esc(meta.get('equity'))} {esc(meta.get('currency'))}｜余额 {esc(meta.get('balance'))}</div>
  <div class="meta">指标：{esc(meta['indicators'])}</div>
</header>
<div class="note"><b>声明：</b>{esc(meta['disclaimer'])} 本页为技术筛选，不构成投资建议。部分品种终端本地历史停更（标陈旧），已记入 failures。</div>
<div class="wrap">
  <div class="cards">
    <div class="card">观察名单<br/><b>{len(summary)}</b></div>
    <div class="card">新鲜可用<br/><b>{success_n}</b></div>
    <div class="card">陈旧/缺口<br/><b>{stale_n + gap_n}</b></div>
    <div class="card">Top 机会<br/><b>{len(top_list)}</b></div>
    <div class="card">周期<br/><b>{' / '.join(PERIODS)}</b></div>
  </div>

  <div class="section">
    <h2>Top 机会（最多 30，含 pi 批注栏）</h2>
    <table><thead><tr><th>#</th><th>品种</th><th>总偏向</th><th>主周期</th><th>现价</th><th>入场思路</th><th>止损</th><th>目标</th><th>R:R</th><th>置信</th><th>计划</th><th>深度批注</th></tr></thead>
    <tbody>{''.join(top_rows) if top_rows else '<tr><td colspan=12>暂无</td></tr>'}</tbody></table>
  </div>

  <div class="section">
    <h2>全品种表（搜索/筛选）</h2>
    <div class="controls">
      <input id="q" placeholder="搜索品种…" style="min-width:220px"/>
      <select id="bias"><option value="">总偏向（全部）</option><option>多</option><option>偏多</option><option>震荡</option><option>偏空</option><option>空</option><option>缺口</option></select>
      <select id="exec"><option value="">可执行（全部）</option><option value="1">仅可执行思路</option><option value="0">仅观察</option></select>
      <select id="stale"><option value="">陈旧（全部）</option><option value="0">仅新鲜</option><option value="1">仅陈旧</option></select>
      <button onclick="applyFilter()">筛选</button>
      <span class="meta" id="count"></span>
    </div>
    <div style="max-height:70vh;overflow:auto;border:1px solid var(--line);border-radius:10px">
    <table id="all"><thead><tr>
      <th>品种</th><th>现价</th><th>总偏向</th><th>M5</th><th>M15</th><th>H1</th><th>H4</th><th>D1</th>
      <th>近端支撑</th><th>近端阻力</th><th>计划</th><th>R:R</th><th>置信</th><th>备注</th>
    </tr></thead><tbody>{''.join(rows_html)}</tbody></table>
    </div>
  </div>
</div>
<script>
function applyFilter(){{
  const q=(document.getElementById('q').value||'').trim().toLowerCase();
  const b=document.getElementById('bias').value;
  const e=document.getElementById('exec').value;
  const s=document.getElementById('stale').value;
  let n=0;
  document.querySelectorAll('#all tbody tr').forEach(tr=>{{
    const sym=(tr.getAttribute('data-sym')||'').toLowerCase();
    const bias=tr.getAttribute('data-bias')||'';
    const ex=tr.getAttribute('data-exec')||'';
    const st=tr.getAttribute('data-stale')||'';
    let ok=true;
    if(q && !sym.includes(q)) ok=false;
    if(b && bias!==b) ok=false;
    if(e!=='' && ex!==e) ok=false;
    if(s!=='' && st!==s) ok=false;
    tr.style.display=ok?'':'none';
    if(ok) n++;
  }});
  document.getElementById('count').textContent='显示 '+n+' 行';
}}
['q','bias','exec','stale'].forEach(id=>document.getElementById(id).addEventListener(id==='q'?'input':'change', applyFilter));
applyFilter();
</script>
</body></html>
"""
    html_path = ROOT / f"MT5_全品种深度分析_{now.strftime('%Y%m%d_%H%M')}_AsiaShanghai.html"
    html_path.write_text(html_doc, encoding="utf-8")
    (ROOT / "index.html").write_text(html_doc, encoding="utf-8")

    readme = f"""# MT5 Market Watch 全品种深度技术分析

- 生成时间（Asia/Shanghai）：**{stamp}**
- 服务器：`{meta['server']}` / `{meta['broker']}`
- 账户：`{meta.get('login')}` 权益约 `{meta.get('equity')}` {meta.get('currency')}
- 名单品种：{len(summary)}；新鲜可用：{success_n}；陈旧：{stale_n}；failures 记录：{len(failures)}；Top：{len(top_list)}

## 如何打开网页

浏览器直接打开：

- `index.html`（推荐）
- 或 `MT5_全品种深度分析_*_AsiaShanghai.html`

本地双击即可，支持搜索/筛选。

## 文件

- `summary.json` — 全品种结构化结果
- `top_opportunities.json` — 最多 30 条可执行或高置信观察
- `failures.json` — 空历史或陈旧 bars
- `bars/` — `get_chart_history` 原始缓存
- `botsignal-upload.tar.gz` — 上传包（打包后）

## 说明

{meta['disclaimer']}

指标：{meta['indicators']}

数据经 MT5 MCP `get_chart_history`（M5/M15/H1/H4/D1）拉取；部分 Crypto/冷门 CFD 终端本地历史停在更早日期，报告中标「陈旧」并写入 failures。

## pi 批注

若已写入 `pi_annotations.json` 并合并进 HTML，见 Top 表「深度批注」列；否则 README 会注明跳过。
"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps({
        "html": str(html_path), "symbols": len(summary), "fresh": success_n,
        "stale": stale_n, "failures": len(failures), "top": len(top_list), "stamp": stamp,
        "equity": acct.get("equity"),
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
