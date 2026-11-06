# M-1a 内部数据积累

对应需求终稿 v3：§15.1 M-1a、F-M8、F-D6、§8.1、§10。

## 目标

- 每日 point-in-time **universe 快照**（加密市值前 150 匹配币安 USDT-M 永续；可选 MT5 可交易表静态副本）
- **5m K 线 parquet 增量归档**（幂等、带 `source_run_id`、缺口摘要）
- **币安双端点连通性探测**（为后续 VPS 选址对照）
- **不建公开站、不发社群、不部署 VPS、不下单**

## 数据目录布局

```
data/
  universe_snapshots/YYYY-MM-DD.json   # F-M8 日快照
  mt5_snapshots/YYYY-MM-DD.txt         # 可选：当日 MT5 可交易表副本
  bars/binance_um/{SYMBOL}/5m.parquet  # 每品种一个连续 parquet（非按日分区）
  connectivity/YYYYMMDD_binance.json   # 双端点探测
  runs/archive_bars_*.json             # 每次归档 run 摘要
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

### 三条 CLI

```bash
# 1) 连通性探测 → data/connectivity/YYYYMMDD_binance.json
python -m flipscope_signal connectivity_probe

# 2) Universe 日快照（语义对应 UTC 00:05；本地可随时手动跑）
python -m flipscope_signal snapshot_universe
# 测试降级路径：
python -m flipscope_signal snapshot_universe --force-fail

# 3) 归档 5m K（建议先 --limit 5 冒烟）
python -m flipscope_signal archive_bars --limit 5
python -m flipscope_signal archive_bars --symbol BTCUSDT,ETHUSDT
```

## 验收标准（本里程碑）

1. `connectivity_probe` 写出当日 JSON，记录 primary/backup 是否可达与 451/错误
2. `snapshot_universe` 写出非空 `data/universe_snapshots/YYYY-MM-DD.json`；失败路径 `degraded_from_prev=true`
3. `archive_bars --limit 5` 至少产出若干 `5m.parquet`（若本机币安全失败，须在 TEST_REPORT 记录并保留代码+降级+connectivity）
4. 单元测试：`pytest` 覆盖 degraded 与 merge 幂等
5. 无 secrets 入 git；无公开站/部署

## 与 cron 的关系

生产侧建议 UTC 00:05 跑 `snapshot_universe`，其后 `archive_bars`；M-1a 阶段以手动/本地调度为主。
