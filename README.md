# etf-lite

**全市场 A 股 ETF 日行情，代码和数据一起开源。**

1700+ 只 ETF，**2005 年至今 160 万行**日线，每天在云端自动更新。  
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
| 体量 | **160 万行仅约 41MB**（Parquet 列式压缩） | 同等数据 SQLite 要 300MB+ |
| 覆盖 | **2005-02 至今**，1691 只有数据（含已退市） | 视数据源而定 |

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
#  'daily_years': [2005, ..., 2026],
#  'daily_rows': 1602292,
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
    ├── 2005.parquet             #    213 行（最早的 ETF 只有 1 只）
    ├── ...
    ├── 2025.parquet             # 284,961 行
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

| 字段                        | 类型    | 说明                      |
| ------------------------- | ----- | ----------------------- |
| ts_code                   | str   | `510300.SH`             |
| trade_date                | str   | `YYYY-MM-DD`            |
| open / high / low / close | float | 开 / 高 / 低 / 收（元）        |
| volume                    | int   | 成交量（**手**）              |
| amount                    | float | 成交额（**万元**），2018 年起齐全     |
| change_pct                | float | 涨跌幅 %                   |
| turnover_rate             | float | 换手率 %，**暂时为空**（需要份额数据，见路线图） |

> **空值（NaN）的含义**：表示该数据源没有提供这个字段，不是 0。
> 价格 / 成交量 100% 完整（2005 年至今，逐日无断档）。
> 成交额 **2018 年起齐全**，更早的年份多数为空 —— 免费行情源的历史成交额普遍只回溯到 2018 年。
> 换手率暂时整体为空：公开行情接口不提供 ETF 换手率，需要先有每日份额才能算，见路线图 `etf_share`。

数据体检脚本：`python scripts/check_data.py`（检查重复行、负价格、高低倒挂、年度覆盖）。

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
python scripts/backfill_ths.py                     # 全量历史 OHLCV（同花顺，稳定不封）
python scripts/backfill_xq.py                      # 补成交额 / 换手率（雪球）
python scripts/check_data.py                       # 体检
```

两个回填脚本的分工：

| 脚本 | 提供什么 | 特点 |
|---|---|---|
| `backfill_ths.py` | 价格 + 成交量，**2005 年至今全覆盖** | 一次请求返回上市至今全历史，1700 多只连抓不封 |
| `backfill_xq.py` | 成交额 + 换手率，**2018 年起** | 只补字段不动价格，`update` 合并不覆盖已有值 |

两者都是：

- **断点续跑**：进度写 `data/.backfill_*_progress.json`，中断后重跑自动跳过已完成
- **合并取优**：同一天若新旧两条记录，保留字段更全的那条，不会把真实成交额覆盖成空

```bash
python scripts/backfill_ths.py --retry-failed   # 只重跑上次失败的
python scripts/backfill_ths.py --refresh-all    # 忽略进度全部重抓
```

> 另有 `scripts/backfill_em.py`（东方财富源）：能补成交额，但会触发 IP 封禁，
> 建议只在需要补 `amount` 时小批量跑，脚本内置熔断与等待。

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
A：三条线，都是公开行情接口，不经第三方数据商转手加工：

- **每日增量** —— 腾讯财经 `qt.gtimg.cn` 批量快照（字段全、一次可拉全部 ETF），代码在 `etf_lite/sources.py`
- **历史 OHLCV** —— 同花顺 `d.10jqka.com.cn` 日线（一次返回上市至今全部历史，稳定不封），代码在 `scripts/backfill_ths.py`
- **成交额 / 换手率** —— 雪球 `stock.xueqiu.com` K 线（带 amount 与 turnoverrate），代码在 `scripts/backfill_xq.py`

三条线统一 **不复权** 口径（同花顺用 `00`、雪球用 `type=normal`），可直接与行情软件对照。

**Q：会不会被限流、有额度吗？**  
A：SDK 有本地缓存，一次下载后不再重复请求；下载走国内加速代理与 jsDelivr，无严格限流。  
爬虫侧：同花顺 / 雪球接口温和，1700 多只全量抓一遍也不会封；
东方财富会做 IP 级封禁（返回空响应），`backfill_em.py` 内置熔断——连续失败就暂停探测，解封后自动续跑。

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
