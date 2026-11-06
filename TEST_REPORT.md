# M-1a 本地自测报告

- 时间：2026-11-06（Asia/Shanghai / box 本地）
- 环境：`/workspace/flipscope-signal/.venv` · Python 3.13 · `pip install -r requirements.txt && pip install -e .`
- 授权：用户 t125u「开工」；仅 M-1a

## 命令与结果

| 步骤 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `pytest -q` | **6 passed**（degraded 沿用、拒绝空名单、merge 幂等、缺口检测） |
| 连通性 | `python -m flipscope_signal connectivity_probe` | primary+backup **均 200 可达**；写出 `data/connectivity/20261106_binance.json` |
| Universe | `python -m flipscope_signal snapshot_universe` | **150** 合约；`degraded_from_prev=false`；MT5 副本 311 品种 |
| Bars | `python -m flipscope_signal archive_bars --limit 5` | BTC/ETH/BNB/XRP/SOL 各 **499** 根已收盘 5m；`ok=5 err=0` |
| 幂等 | 同上再跑一次 | 各品种 `inserted=0 rows=499` |
| 降级 | `snapshot_universe --force-fail --date 2026-11-07` | `degraded_from_prev=true`，沿用 2026-11-06 名单，stderr 告警 `DEGRADED: ...` |

## 样本路径

```
data/connectivity/20261106_binance.json
data/universe_snapshots/2026-11-06.json
data/universe_snapshots/2026-11-07.json   # 强制失败降级样例（测试用）
data/mt5_snapshots/2026-11-06.txt
data/bars/binance_um/BTCUSDT/5m.parquet
data/bars/binance_um/ETHUSDT/5m.parquet
data/runs/archive_bars_*.json
```

## 连通性摘要（本机 box，2026-11-06）

- `https://www.binance.com/fapi/v1`：可达
- `https://fapi.binance.com/fapi/v1`：可达
- 推荐：优先 www 路径，418 时熔断切 backup
- 说明：历史例程曾见本机对 `fapi.binance.com` 返回 451；本次探测双端均通。VPS 选址仍应以候选机上重跑 `connectivity_probe` 为准。

## 已知限制

1. M-1a 未接 cron；UTC 00:05 语义需外部调度手动/后续挂定时任务。
2. `archive_bars` 默认 lookback=500；全量 150 品种首次归档耗时与权重需观察（本次仅 top5）。
3. CoinGecko 免费档有限频；失败已有 degraded 路径。
4. 未 push GitHub（仓尚未创建）；`data/` 已 gitignore。
5. 测试写入的 `2026-11-07.json` 为降级演示，正式日跑前可删。

## 验收对照

- [x] CLI 三条可跑
- [x] 真实快照 + 若干 parquet
- [x] degraded 单测 + `--force-fail` 实跑
- [x] 无 secrets 入仓；无公开站/部署/下单

## MT5 数据侧（t137u，2026-11-06）

本环境无 MetaTrader5 Python 包，也没有 python3-venv，依赖用 `python3 -m pip install --user -r requirements.txt`（Python 3.12）。Linux 侧不连终端；K 线来自 `fixtures/mt5_m5/`。动态名单失败后走仓库内 `fixtures/mt5_all_tradeable.txt`。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 单元测试 | `PYTHONPATH=src python3 -m pytest -q` | **19 passed**（含原 6 项；新增 MT5 merge 幂等、forming K 丢弃、静态兜底、JSON limit、已有名单不被 universe 覆盖） |
| 名单 | `python -m flipscope_signal snapshot_mt5 --date 2026-11-06` | stderr `MT5_STATIC_FALLBACK`（原因：`MetaTrader5 package not installed`）；写出 17 个品种；`degraded_from_static=true` |
| Bars | `archive_mt5_bars --limit 5 --from-json-dir fixtures/mt5_m5` | **5** 个 parquet，`ok=5 err=0`。EURUSDz 3 根（未来 K 已丢），GBPUSDz 4 根且缺口 1（估计缺 2 根） |
| 幂等 | 同上再跑一次 | 五品种 `inserted=0`，行数不变 |

```
data/mt5_snapshots/2026-11-06.txt
data/bars/mt5/BTCUSDz/5m.parquet
data/bars/mt5/EURUSDz/5m.parquet
data/bars/mt5/GBPUSDz/5m.parquet
data/bars/mt5/US500z/5m.parquet
data/bars/mt5/USDJPYz/5m.parquet
data/runs/archive_mt5_bars_*.json
```

列：`open_time, open, high, low, close, volume(=tick_volume), close_time, source_run_id, gap_filled, is_sparse`。GBPUSDz 有 1 行 `is_sparse=true`（`tick_volume=0`）。

未调用任何下单 / `trade_*` API。终端全量动态名单与实盘 `copy_rates` 需在已打开 MT5 的 Windows 机器上再跑；本机验收覆盖的是静态兜底和 `--from-json-dir`。
