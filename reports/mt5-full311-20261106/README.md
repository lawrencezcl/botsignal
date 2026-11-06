# MT5 Market Watch 全品种深度技术分析

- 生成时间（Asia/Shanghai）：**2026-11-06 21:20:50**
- 服务器：`Exness-MT5Real5` / `Exness Technologies Ltd`
- 账户：`59799034` 权益约 `1432.04` USD
- 名单：311｜新鲜可用：276｜陈旧：35｜Top：30

## 如何打开网页

浏览器打开本目录：

- `index.html`（推荐）
- 或 `MT5_全品种深度分析_*_AsiaShanghai.html`

本地双击即可；支持搜索品种、总偏向/可执行/陈旧筛选。

## 文件

- `index.html` — 主网页报告
- `MT5_全品种深度分析_*_AsiaShanghai.html` — 同次快照的网页副本
- `summary.json` — 全品种结构化
- `top_opportunities.json` — 30 条机会（含 `pi_annotation`）
- `pi_annotations.json` — pi 批注字典
- `failures.json` — 空历史或陈旧 bars
- `account.json` — 账户与终端只读快照（无密钥）
- `marketwatch.json` — Market Watch 名单快照
- `symbols.txt` — 品种名单
- `fetch_summary.json` — 拉取计数
- `analyze_report.py` — 生成脚本（只读分析，不下单）

原始 `bars/` K 线缓存未纳入本目录。

## 说明

经纪商 CFD；Tick volume 非交易所量；默认不下单；PCR/GEX 不可用；只读分析。

指标：EMA20/50/200, Supertrend(ATR20×3), ATR20, MACD(12,26,9), RSI14, ADX14；已收盘K线（去掉最后一根可能未收盘）。陈旧判定：末根K线距快照>5天。

## pi 批注

**已完成**：`pi --provider zai --model glm-5.3-flash` 对 Top 30 写入中文短批注，并合并进网页「深度批注」列。
