# 信号扫描站 · 开发约定

- 产品需求终稿：`docs/需求终稿_v3.md`
- 原则：信号只作参考，不输出交易建议；第一版不含下单。禁止调用任何下单/交易 API。
- **授权状态（2026-11-06 / t125u）**：**M-1a 内部数据积累已授权**（universe 日快照、bars parquet 归档、币安连通性探测）。**M-1b 外发、公开站、VPS 部署、M0/M1-lite 网站开发仍须另授权。**
- 本地开发根目录：`/workspace/flipscope-signal/`；数据在 `data/`（gitignore）。
- 参考只读实现：`/workspace/st_ema_scan/`（CoinGecko 名单匹配、`www.binance.com/fapi`）。
- GLM Coding Plan：环境变量 `ZAI_API_KEY`（或中国区 `ZAI_CODING_CN_API_KEY`），密钥放 `/home/box/secrets/zai/api_key`，勿写入 git。
- 密钥与 `.env` 永不入库；详见 `.gitignore`。
