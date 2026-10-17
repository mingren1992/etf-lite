# data/ 目录说明

这里是对外托管的数据本体，由 GitHub Actions 每交易日自动更新。

```
data/
├── manifest.json                  # 数据索引：最新交易日、文件清单、行数、字节数
├── etf_list.parquet               # 全市场 ETF 基础信息（1718 行）
└── etf_daily/
    ├── 2015.parquet               # 按年切分，一年一个文件
    ├── …
    └── 2026.parquet               # 当年文件，每日追加新行
```

## 用法

**方式一（推荐）**：用 SDK 自动读取

```python
import etf_lite
etf = etf_lite.ETFLite()
etf.get_etf_daily("510300", start="2024-01-01")
```

**方式二**：直接下载 parquet 文件

```python
import pandas as pd
pd.read_parquet("https://cdn.jsdelivr.net/gh/mingren1992/etf-lite@main/data/etf_daily/2026.parquet")
```

## 字段说明

`etf_list.parquet`：`ts_code` / `symbol` / `market` / `name` / `last_price` / `change_pct`

`etf_daily/YYYY.parquet`：`ts_code` / `trade_date` / `open` / `high` / `low` / `close` /
`volume`（手）/ `amount`（万元）/ `change_pct`（%）/ `turnover_rate`（%）

## 更新节奏

- 每交易日 **18:30（北京时间）** 抓取当日快照
- 次日 **08:30** 补跑一次，防止漏数据
- 抓取结果自动 commit 回本目录，`manifest.json` 同步更新

> 历史回填的行 `amount` 为 0、`turnover_rate` 为空——数据源对历史只提供 K 线。
> 当日快照会带全这两个字段，并覆盖同日的历史值。
