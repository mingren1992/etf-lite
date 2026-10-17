# etf-lite

**全市场 A 股 ETF 日行情，代码和数据一起开源。**

1700+ 只 ETF，2015 年至今 **140 多万行**日线，每天在云端自动更新。  
`pip install` 之后一行代码读出来，**不用注册、不用 token、没有调用额度**。

![Daily Data Update](https://github.com/mingren1992/etf-lite/actions/workflows/daily.yml/badge.svg)

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)

```python
import etf_lite

etf = etf_lite.ETFLite()

etf.get_etf_list()                        # 全市场 ETF 列表（1718 只）
etf.get_etf_daily("510300")               # 沪深300ETF 全部历史
etf.get_etf_daily("510300", "2024-01-01") # 指定区间
etf.get_etf_daily(start="2026-01-01")     # 全市场自某日起
```

```
        ts_code trade_date   open   high    low  close    volume     amount  change_pct
510300.SH 2026-10-16    4.255  4.282  4.200  4.236  37995085  1609279.0       -0.14
```

---

## 为什么做这个

A 股 ETF 数据一直有两个坑：

1. **要么贵** —— Wind、聚宽、Tushare Pro 好用的都要付费，额度还卡人
2. **要么散** —— AkShare、BaoStock 免费，但每次都得自己写爬虫、自己建库、自己维护；  
   数据分散在第三方网站，格式还常变

这个项目的做法是：**把数据抓一次，然后让全世界都能直接读。**  
抓取不落在你的电脑上，落在 GitHub 的免费云端机器上，每天收盘后自动跑完。

## 核心卖点

|    | etf-lite                        | 传统方案                 |
| -- | ------------------------------- | -------------------- |
| 费用 | 免费，无额度                          | 付费 / 注册 / 限流         |
| 使用 | `pip install` 一行读               | 自己写爬虫自己建库            |
| 数据 | 由作者统一更新                         | 每个用户各自抓，各自踩坑         |
| 体量 | **140 万行仅约 25MB**（Parquet 列式压缩） | 同等数据 SQLite 要 300MB+ |
| 覆盖 | 2015-01 至今，1718 只 ETF           | 视数据源而定               |

## 安装

```bash
# 从 PyPI（发布后）
pip install etf-lite

# 直接从 GitHub 装（现在就能用）
pip install git+https://github.com/mingren1992/etf-lite.git
```

## 使用

### 1. 看数据概况

```python
import etf_lite

etf = etf_lite.ETFLite()
etf.info()
# {'repo': 'mingren1992/etf-lite',
#  'latest_trade_date': '2026-10-16',
#  'etf_count': 1718,
#  'daily_years': [2015, ..., 2026],
#  'daily_rows': 1427617,
#  'cache_dir': '/Users/you/.cache/etf_lite'}
```

### 2. 拿列表

```python
lst = etf.get_etf_list()
lst[lst.name.str.contains("沪深300")]
```

### 3. 拿行情

```python
# 单只，全历史
df = etf.get_etf_daily("510300")

# 区间
df = etf.get_etf_daily("510300", start="2024-01-01", end="2026-10-16")

# 批量：某天全市场成交额 Top10
today = etf.get_etf_daily(start=etf.info()["latest_trade_date"])
today.nlargest(10, "amount")[["ts_code", "close", "change_pct", "amount"]]

# 归一化净值曲线：一眼看出谁跑得强
pivot = etf.get_etf_daily(start="2025-01-01").pivot(
    index="trade_date", columns="ts_code", values="close")
(pivot / pivot.iloc[0] - 1).plot()
```

### 4. 参数说明

| 方法                | 参数              | 说明                                       |
| ----------------- | --------------- | ---------------------------------------- |
| `ETFLite()`       | `repo=`         | 换数据源仓库，默认官方                              |
|                   | `cache_dir=`    | 本地缓存目录，默认 `~/.cache/etf_lite`            |
|                   | `local_dir=`    | 直接读本地仓库（离线 / 自建）                         |
|                   | `verbose=True`  | 打印下载过程                                   |
| `get_etf_list()`  | `force=True`    | 忽略缓存强制重新下载                               |
| `get_etf_daily()` | `code`          | 6 位代码 `510300` 或带后缀 `510300.SH`，留空 = 全市场 |
|                   | `start` / `end` | `2024-01-01` 或 `20240101`                |
| `clear_cache()`   | —               | 清空本地缓存                                   |

## 数据规格

数据托管在本仓库 `data/` 目录，按年切分：

```
data/
├── manifest.json                # 数据索引（SDK 先读它）
├── etf_list.parquet             # 1718 只 ETF 基础信息
└── etf_daily/
    ├── 2015.parquet             # 27,612 行
    ├── ...
    ├── 2025.parquet             # 284,828 行
    └── 2026.parquet             # 当年文件，每日追加
```

**etf_list.parquet**

| 字段         | 类型    | 说明                  |
| ---------- | ----- | ------------------- |
| ts_code    | str   | `510300.SH`         |
| symbol     | str   | 6 位代码               |
| market     | str   | `SH` / `SZ`         |
| name       | str   | 简称，如 `沪深300ETF华泰柏瑞` |
| last_price | float | 最近一次快照价             |
| change_pct | float | 当日涨跌幅 %             |

**etf_daily/YYYY.parquet**

| 字段                        | 类型    | 说明               |
| ------------------------- | ----- | ---------------- |
| ts_code                   | str   | `510300.SH`      |
| trade_date                | str   | `YYYY-MM-DD`     |
| open / high / low / close | float | 开 / 高 / 低 / 收（元） |
| volume                    | int   | 成交量（**手**）       |
| amount                    | float | 成交额（**万元**）      |
| change_pct                | float | 涨跌幅 %            |
| turnover_rate             | float | 换手率 %（历史回填行为空）   |

> **关于历史数据质量**：`amount`（成交额）与 `turnover_rate`（换手率）在早期的历史回填行中可能为空，
> 因为部分行情源只给 K 线不给成交额。每个交易日的当日快照始终带全这两个字段，并自动覆盖同日历史值。
> 如需完整成交额历史，用 `scripts/backfill_em.py`（东方财富源，自带成交额）重跑一遍即可。

## 工作原理

```
   交易所 / 行情源
        │  ① 爬取
        ▼
   GitHub Actions 云端机器          ← 每交易日 18:30 自动触发
        │  ② commit + push
        ▼
   GitHub 仓库 data/ ──► ghfast.top / gh-proxy.com / jsDelivr（国内加速）
        │  ③ 用户下载（首次全量，之后增量）
        ▼
   用户本地 ~/.cache/etf_lite       ← 缓存后不再重复请求
        │  ④
        ▼
   pandas DataFrame
```

**你的电脑不用开机，也不用当服务器。** 数据存在 GitHub 服务器上，抓取在 GitHub 的云端机器上跑。

## 自己建一份完整历史

仓库里的 `data/` 是成品数据。如果你想从零抓一份，本地跑：

```bash
pip install -r requirements.txt

python scripts/update_daily.py --refresh-list      # 拉 ETF 列表 + 当日快照
python scripts/backfill_em.py --sleep 0.9          # 全量历史（东方财富，含成交额）
```

`backfill_em.py` 特点：

- **一次请求返回上市至今全部历史**，不用按年分片
- **自带成交额**，比只给 K 线的接口完整
- **断点续跑**：进度写 `data/.backfill_progress.json`，中断后重跑自动跳过已完成
- **限流熔断**：连续失败自动暂停，每隔一段时间探测，解封后继续

```bash
python scripts/backfill_em.py --retry-failed   # 只重跑上次失败的
python scripts/backfill_em.py --refresh-all    # 忽略进度全部重抓
```

## 自己部署一份（Fork 即用）

1. 点右上角 **Fork**（或直接建个新仓库）
2. 仓库 **Settings → Actions → General → Workflow permissions** 勾选 **Read and write permissions**
3. **Actions** 页面点一次 **Run workflow** 验证
4. 改两处指向你的仓库：
   - `etf_lite/config.py` → `DEFAULT_REPO = "你的用户名/etf-lite"`
   - `pyproject.toml` → `[project.urls]`

之后每天自动更新，不用管。

### 想扩展数据集？

每加一层数据，只需要三件事：给 `scripts/` 加一个爬虫、给 `data/` 加一个目录、  
给 `manifest.json` 加一条 dataset 条目。**SDK 不用改。**

后续规划：

- [x] `etf_daily` —— 日行情
- [ ] `etf_nav` —— 单位净值 / 累计净值
- [ ] `etf_share` —— 每日份额变动（沪市 22:20 公布、深市 Excel）
- [ ] `etf_pcf` —— 每日申赎清单，ETF 真实持仓明细
- [ ] `index_rules` —— 指数编制方案结构化
- [ ] `rebalance_prediction` —— 基于编制规则的调样预测

## 常见问题

**Q：数据从哪来？**  
A：两条线，都是公开行情接口，不经第三方数据商转手加工：

- **每日增量** —— 腾讯财经 `qt.gtimg.cn` 批量快照（字段全、一次可拉全部 ETF），代码在 `etf_lite/sources.py`
- **历史回补** —— 东方财富 `push2his.eastmoney.com` 日线（一次返回上市至今、自带成交额），代码在 `scripts/backfill_em.py`

**Q：会不会被限流、有额度吗？**  
A：SDK 有本地缓存，一次下载后不再重复请求；下载走国内加速代理与 jsDelivr，无严格限流。  
爬虫侧：东方财富对密集请求会做 IP 级封禁（返回空响应），`backfill_em.py` 内置熔断——连续失败就暂停探测，解封后自动续跑。

**Q：下载慢？**  
A：SDK 默认按 `ghfast.top → gh-proxy.com → ghproxy.net → jsDelivr → GitHub raw` 顺序尝试，前三个是实时代理。  
如果都不通，可用 `ETF_LITE_CDN` 环境变量指定自己的源，或 clone 仓库后用 `ETFLite(local_dir="本地路径")` 离线读取。

**Q：数据能商用吗？**  
A：代码 MIT 协议随便用。行情数据版权归交易所 / 数据源所有，本项目仅作技术研究与学习用途，  
不构成任何投资建议，据此交易风险自担。

## 参与贡献

Issue / PR 都欢迎。新增数据源请附上接口示例与字段说明，方便核对。

## License

[MIT](LICENSE)

---

数据仅供研究学习，不构成投资建议。
