# etf-lite

全市场 A 股 ETF 日行情数据 —— **代码 + 数据一起开源**的最小闭环。

数据每天在 GitHub Actions 云端自动抓取并提交进本仓库，用户 `pip install` 后一行代码读取，
不需要注册、不需要 token、没有调用额度。

```python
import etf_lite

etf = etf_lite.ETFLite()                  # 默认连本仓库托管的数据
etf.info()                                # 看数据概况

etf.get_etf_list()                        # 全市场 ETF 列表
etf.get_etf_daily("510300")               # 沪深300ETF 全部历史
etf.get_etf_daily("510300", "2024-01-01") # 指定区间
etf.get_etf_daily(start="2026-01-01")     # 全市场某段时间
```

---

## 一、数据到底存在哪里

| 存放位置 | 用途 | 说明 |
|---------|------|------|
| **GitHub 仓库**（`data/` 目录） | 线上数据源 | 真正的“数据服务器”，所有人从这里下载 |
| jsDelivr CDN | 加速分发 | 自动镜像 GitHub 公开仓库，国内访问快 |
| 用户本地 `~/.cache/etf_lite/` | 用户侧缓存 | 首次下载后本地复用，不重复请求 |
| 你自己的 Mac | 只做开发 | 写代码、改爬虫、验证数据；关机不影响线上更新 |

**关键点：抓取不跑在你的电脑上，跑在 GitHub 的云端机器上**（`.github/workflows/daily.yml`），
每交易日北京时间 18:30 自动执行 → 抓数据 → commit → push。你的电脑不需要开机。

## 二、目录结构

```
etf-lite/
├── .github/workflows/daily.yml   # 云端定时任务（每天自动爬 + 提交）
├── etf_lite/                     # 用户 SDK
│   ├── api.py                    #   ETFLite 类：下载 / 缓存 / 查询
│   ├── sources.py                #   爬虫：腾讯财经直连
│   └── config.py                 #   仓库地址、CDN、缓存路径
├── scripts/                      # 作者侧脚本
│   ├── update_daily.py           #   每日增量更新（Actions 调它）
│   ├── backfill_from_sqlite.py   #   一次性：老 SQLite 库 → parquet
│   ├── backfill_history.py       #   一次性：爬历史补缺口
│   └── common.py                 #   manifest 读写工具
├── data/                         # ★ 对外托管的数据
│   ├── manifest.json             #   数据索引（用户先读它）
│   ├── etf_list.parquet          #   ETF 列表
│   └── etf_daily/2026.parquet    #   按年切分的日行情
├── pyproject.toml
└── requirements.txt
```

## 三、数据规格

`etf_list.parquet`

| 字段 | 含义 |
|------|------|
| ts_code | 带市场后缀，如 `510300.SH` |
| symbol / market / name | 6 位代码 / SH\|SZ / 名称 |
| last_price | 最近一次快照价 |

`etf_daily/YYYY.parquet`

| 字段 | 含义 |
|------|------|
| ts_code | 如 `510300.SH` |
| trade_date | `YYYY-MM-DD` |
| open / high / low / close | 元 |
| volume | 成交量（手） |
| amount | 成交额（万元） |
| change_pct | 涨跌幅 % |
| turnover_rate | 换手率 %（历史回填行为空） |

## 四、你要提供什么

只需要三样，都不涉及把账号密码交出来：

1. **一个 GitHub 账号**（免费注册即可）—— 数据托管和云端定时任务都靠它
2. **在 GitHub 上建一个空仓库**，比如 `etf-lite`，设为 public（公开才能让别人免费用）
3. **在你电脑上执行两条命令**把本地代码推上去（`git remote add` + `git push`）
   —— 用你自己的凭据，我看不到也不该看到

> PyPI 发布是可选的第二步。不发布也能用：
> `pip install git+https://github.com/你的名字/etf-lite.git`

## 五、上线步骤（照着做）

```bash
# 1. 在你电脑上，进入本目录
cd etf-lite

# 2. 首次初始化历史数据（老库导出）
python scripts/backfill_from_sqlite.py --db ../etf-index-data/data/etf_data.db
#    或者从网上爬历史：
# python scripts/backfill_history.py --start 2015-01-01

# 3. 跑一次当天增量，确认全链路通
python scripts/update_daily.py

# 4. 绑定你自己的仓库并推送
git remote add origin https://github.com/你的名字/etf-lite.git
git push -u origin main

# 5. 到 GitHub 仓库 → Actions 页面 → 点一次 "Run workflow" 验证云端能跑

# 6.（可选）发布到 PyPI，让全世界 pip install
pip install build && python -m build
twine upload dist/*
```

## 六、额度与限制

| 项目 | 额度 |
|------|------|
| 公开仓库 Actions 运行时长 | 不限（私有仓库 2000 分钟/月，本项目每天约 5 分钟也够用） |
| GitHub raw 下载 | 匿名约 60 次/小时/IP，带 token 5000 次/小时；SDK 有缓存，正常使用不会触发 |
| jsDelivr CDN | 公开仓库免费、无严格限流，建议作为首选下载源 |
| 单文件体积 | raw 直链上限 100MB；按年切分后单文件约 2-8MB，远低于上限 |
| 存储 | 仓库建议 < 1GB；超过就改用 GitHub Releases 或 Git LFS |

## 七、扩展路线

当前是最小闭环（只有 ETF 列表 + 日行情）。同一套机制可以直接复制到：

- `data/etf_nav/` —— 净值（单位净值 / 累计净值）
- `data/etf_share/` —— 份额（沪市 22:20 公布、深市 Excel）
- `data/etf_pcf/` —— 每日申赎清单（PCF），ETF 真实持仓明细
- `data/index_rules/` —— 指数编制方案结构化

每加一层，就是给 `scripts/` 多一个爬虫 + 给 `manifest.json` 多一个 dataset 条目，SDK 不用改。

---

MIT License
