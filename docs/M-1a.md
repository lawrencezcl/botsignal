# M-1a 内部数据积累

对应需求终稿 v3：§15.1 M-1a、F-M8、F-D6、§8.1、§10。

## 目标

- 每日 point-in-time **universe 快照**（加密市值前 150 匹配币安 USDT-M 永续；可选 MT5 可交易表）
- **MT5 可交易名单**（`snapshot_mt5`：优先终端动态名单，失败则静态 `mt5_all_tradeable` 并告警）
- **5m K 线 parquet 增量归档**（币安与 MT5；幂等、带 `source_run_id`、缺口摘要）
- **币安双端点连通性探测**（为后续 VPS 选址对照）
- **不建公开站、不发社群、不部署 VPS、不下单**

## 数据目录布局

```
data/
  universe_snapshots/YYYY-MM-DD.json   # F-M8 日快照
  mt5_snapshots/YYYY-MM-DD.txt         # 当日 MT5 可交易名单
  bars/binance_um/{SYMBOL}/5m.parquet  # 币安：每品种一个连续 parquet
  bars/mt5/{SYMBOL}/5m.parquet         # MT5：每品种一个连续 parquet
  connectivity/YYYYMMDD_binance.json   # 双端点探测
  runs/archive_bars_*.json             # 币安归档 run 摘要
  runs/archive_mt5_bars_*.json         # MT5 归档 run 摘要
  logs/                                # 预留
```

### Universe JSON 字段

| 字段 | 说明 |
|---|---|
| `snapshot_date` | UTC 日期 |
| `market` | `binance_um_usdt_perp` |
| `degraded_from_prev` | 失败沿用上一日时为 `true` |
| `source` | 名单来源 |
| `fetched_at` | ISO-8601 UTC |
| `symbols` | `[{symbol, cg_symbol, cg_id, market_cap_rank, matched_via}, ...]` |
| `alert` | 降级时告警文案 |

**失败则沿用上一日快照并告警（stderr/日志），禁止写空名单。**

### Bars parquet

- 路径：`data/bars/binance_um/{SYMBOL}/5m.parquet`（**按品种单文件连续存储**，非按日分区；重复跑按 `open_time` 幂等 upsert，先写优先保留 `source_run_id`）
- 列：`open_time, open, high, low, close, volume, close_time, quote_volume, trades, taker_buy_base, taker_buy_quote, source_run_id, gap_filled, is_sparse`
- 只保留已收盘 K（`close_time <= now`）
- 币安端点优先 `https://www.binance.com/fapi/v1`；`fapi.binance.com` 作备用；读 `X-MBX-USED-WEIGHT-1M`；429 降速；418 熔断切备用，禁止硬撞

### MT5 名单与 5m

行情来自**电脑上的 MT5 终端**，或终端导出的 JSON。**Linux VPS 不跑 MT5 终端**（腾讯云上币安 fapi 不可达时，用这条路径积累 MT5 数据）。在装有终端的机器上归档后，把 `data/` **rsync** 到 VPS。本模块只读：不调用 `order_send` / `trade_*`。

静态兜底文件：`fixtures/mt5_all_tradeable.txt`（可用 `FLIPSCOPE_MT5_TRADEABLE` 指向外部全量名单，例如旧的 `st_ema_scan/mt5_all_tradeable.txt`）。`snapshot_mt5` 先读终端 `symbols_get`（去掉 `trade_mode` 为 disabled / close-only 的品种）；失败则沿用该静态文件，向 stderr 打 `MT5_STATIC_FALLBACK`，**不写空名单**。

5m JSON（每品种一个文件，文件名即品种，如 `EURUSDz.json`）：

```json
{"ok": true, "symbol": "EURUSDz", "history": [
  {"time": "2026-11-05T00:45:00", "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "tick_volume": 127}
]}
```

- `time` 可以是无时区的 ISO 时间或 unix 秒/毫秒。**本里程碑把服务器时间当作 UTC**（需求终稿里的 `server_tz` / DST 对账留到 M1 完整版）
- parquet 路径：`data/bars/mt5/{SYMBOL}/5m.parquet`（按品种单文件；按 `open_time` 幂等 upsert，先写优先保留 `source_run_id`）
- 列：`open_time, open, high, low, close, volume, close_time, source_run_id, gap_filled, is_sparse`
- `volume` = `tick_volume`（没有 `tick_volume` 时才用 `volume`）。`tick_volume <= 0` 时 `is_sparse=true`。不补缺口，`gap_filled` 保持 false
- `close_time = open_time + 5min - 1ms`。只保留已收盘 K
- 样例导出：`fixtures/mt5_m5/`（含一根未来 K，归档时会被丢掉）
- `snapshot_universe` 仍只复制静态名单；若当天 `mt5_snapshots/YYYY-MM-DD.txt` 已在，则保留，不覆盖 `snapshot_mt5` 写下的动态名单

## 安装与运行

```bash
cd /workspace/flipscope-signal
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .   # 可选：注册 flipscope-signal 命令

# 或无需 editable：
export PYTHONPATH=src
```

### CLI

```bash
# 1) 连通性探测 → data/connectivity/YYYYMMDD_binance.json
python -m flipscope_signal connectivity_probe

# 2) Universe 日快照（语义对应 UTC 00:05；本地可随时手动跑）
python -m flipscope_signal snapshot_universe
# 测试降级路径：
python -m flipscope_signal snapshot_universe --force-fail

# 3) 归档币安 5m K（建议先 --limit 5 冒烟）
python -m flipscope_signal archive_bars --limit 5
python -m flipscope_signal archive_bars --symbol BTCUSDT,ETHUSDT

# 4) MT5 当日可交易名单（无终端时自动退回静态文件并告警）
python -m flipscope_signal snapshot_mt5
python -m flipscope_signal snapshot_mt5 --static-only

# 5) MT5 5m 归档。无终端时必须带 --from-json-dir
python -m flipscope_signal archive_mt5_bars --limit 5 --from-json-dir fixtures/mt5_m5
python -m flipscope_signal archive_mt5_bars --symbol EURUSDz,XAUUSDz --from-json-dir /path/to/export
# 仅当本机安装了 MetaTrader5 且终端已开时，才可以不带 --from-json-dir
```

## 验收标准（本里程碑）

1. `connectivity_probe` 写出当日 JSON，记录 primary/backup 是否可达与 451/错误
2. `snapshot_universe` 写出非空 `data/universe_snapshots/YYYY-MM-DD.json`；失败路径 `degraded_from_prev=true`
3. `archive_bars --limit 5` 至少产出若干 `5m.parquet`（若本机币安全失败，须在 TEST_REPORT 记录并保留代码+降级+connectivity）
4. 单元测试：`pytest` 覆盖 degraded、币安 merge 幂等，以及 MT5 merge 幂等 / 静态兜底
5. `snapshot_mt5` 写出非空 `data/mt5_snapshots/YYYY-MM-DD.txt`；动态名单失败时 `degraded_from_static=true` 且 stderr 有 `MT5_STATIC_FALLBACK`
6. `archive_mt5_bars --limit 5 --from-json-dir fixtures/mt5_m5` 至少产出 1 个 `data/bars/mt5/{SYMBOL}/5m.parquet`
7. 无 secrets 入 git；无公开站/部署；无下单 API

## 与 cron 的关系

生产侧建议 UTC 00:05 跑 `snapshot_universe`，其后 `archive_bars`。MT5 在桌面终端机器上跑 `snapshot_mt5` 与 `archive_mt5_bars`（或先导出 JSON），再 rsync `data/`。M-1a 阶段以手动/本地调度为主。
