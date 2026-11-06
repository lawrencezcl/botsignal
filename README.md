# flipscope-signal

全球市场信号扫描站开发仓。当前阶段：**M-1a 内部数据积累**（universe 日快照 + 币安/MT5 bars parquet + 币安连通性探测）。

需求终稿：`docs/需求终稿_v3.md` · M-1a 说明：`docs/M-1a.md`

## 原则

- 信号只作参考，不输出交易建议；**不下单**
- M-1a 已授权；M-1b / 公开站 / VPS 部署须另授权

## 快速开始（M-1a）

```bash
cd /workspace/flipscope-signal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src

python -m flipscope_signal connectivity_probe
python -m flipscope_signal snapshot_universe
python -m flipscope_signal archive_bars --limit 5

# MT5：行情来自电脑终端或导出 JSON。Linux VPS 不跑 MT5 终端，归档后 rsync data/
python -m flipscope_signal snapshot_mt5
python -m flipscope_signal archive_mt5_bars --limit 5 --from-json-dir fixtures/mt5_m5
```

数据写入 `data/`（已 gitignore）。自测报告见 `TEST_REPORT.md`。MT5 细节见 `docs/M-1a.md`。

## Pi + GLM（可选）

```bash
source scripts/load_zai_env.sh
./scripts/run_pi.sh
```

密钥：`/home/box/secrets/zai/api_key`（勿入库）。
