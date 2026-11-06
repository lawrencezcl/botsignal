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
