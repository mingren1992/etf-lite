#!/usr/bin/env python3
"""全量历史重建（同花顺数据源）

为什么用同花顺：东方财富会在密集请求后做 IP 级封禁（一次封几小时），
同花顺这个静态资源接口一次请求就返回上市至今全部日线，稳定且不封。

已知限制：同花顺不返回成交额（amount），该字段留空（NaN）。
成交额由每日快照补齐，历史部分如需精确值，可在东方财富解封后
用 scripts/backfill_em.py 叠加（合并时优先保留有成交额的行）。

同花顺编码说明（坑点，改代码前务必看）：
  - price     : 每天 4 个整数，[最低价, 开盘-最低, 最高-最低, 收盘-最低]，均已乘 priceFactor
  - volumn    : 成交量，单位「股」（本仓库统一用「手」，需 ÷100）
  - dates     : 逗号分隔的 "MMDD" 字符串，按 sortYear 里的年份分段拼接
  - jsonp 包裹：quotebridge_v6_line_hs_510300_01_all({...})

用法：
    python scripts/backfill_ths.py                    # 全量，跳过已完成
    python scripts/backfill_ths.py --limit 20
    python scripts/backfill_ths.py --codes 510300,159915
    python scripts/backfill_ths.py --refresh-all      # 忽略进度重抓
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DAILY_DIR, DATA_DIR, load_manifest, rebuild_manifest, save_manifest

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

URL = "https://d.10jqka.com.cn/v6/line/hs_{symbol}/00/all.js"
# 路径最后的两位是「周期+复权」代码，实测（2026-10）：
#   00 = 日线·不复权  ← 本仓库用这个，和每日快照口径一致
#   01 = 日线·前复权  ← 老价格会被压低，510050 在 2005 年甚至算出负数，别用
#   02 = 日线·后复权
#   11 = 周线   21 = 月线
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "https://stockpage.10jqka.com.cn/",
}
PROGRESS = DATA_DIR / ".backfill_ths_progress.json"

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update(HEADERS)


def fetch_history(symbol: str, market: str, retries: int = 3) -> pd.DataFrame:
    for attempt in range(retries):
        try:
            r = SESSION.get(URL.format(symbol=symbol), timeout=20)
            if r.status_code == 200 and "{" in r.text:
                return _parse(r.text, symbol, market)
        except requests.RequestException:
            pass
        time.sleep(0.8 * (attempt + 1))
    return pd.DataFrame()


def _parse(text: str, symbol: str, market: str) -> pd.DataFrame:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return pd.DataFrame()
    d = json.loads(m.group(0))
    if not d.get("price"):
        return pd.DataFrame()

    pf = int(d.get("priceFactor") or 1000)
    P = [int(x) for x in d["price"].split(",")]
    V = [int(x) for x in d.get("volumn", "").split(",")] if d.get("volumn") else []
    day_str = d.get("dates", "").split(",")
    years = d.get("sortYear") or []

    dates: list[str] = []
    k = 0
    for y, cnt in years:
        for mmdd in day_str[k:k + cnt]:
            if len(mmdd) == 4:
                dates.append(f"{y}-{mmdd[:2]}-{mmdd[2:]}")
        k += cnt
    if not dates:
        return pd.DataFrame()

    n = min(len(dates), len(P) // 4, len(V) if V else len(dates))
    recs = []
    prev_close = None
    for i in range(n):
        low, d_open, d_high, d_close = P[i * 4: i * 4 + 4]
        close = (low + d_close) / pf
        recs.append({
            "ts_code": f"{symbol}.{market}",
            "trade_date": dates[i],
            "open": round((low + d_open) / pf, 4),
            "high": round((low + d_high) / pf, 4),
            "low": round(low / pf, 4),
            "close": round(close, 4),
            "volume": int(V[i] // 100) if V else 0,      # 股 → 手
            "amount": float("nan"),                       # 同花顺无成交额
            "change_pct": round((close - prev_close) / prev_close * 100, 4) if prev_close else None,
            "turnover_rate": float("nan"),
        })
        prev_close = close

    df = pd.DataFrame(recs)
    df["amount"] = df["amount"].astype("float64")
    df["turnover_rate"] = df["turnover_rate"].astype("float64")
    return df


def load_progress() -> dict:
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"done": {}, "failed": {}}


def save_progress(p: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    p["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def merge_year(path: Path, new: pd.DataFrame) -> pd.DataFrame:
    """合并新旧数据：同 (ts_code, trade_date) 优先保留有成交额的那条"""
    if path.exists():
        old = pd.read_parquet(path)
        for c in ("open", "high", "low", "close", "volume", "amount", "change_pct", "turnover_rate"):
            if c not in old.columns:
                old[c] = float("nan")
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    combined["_score"] = (combined["amount"].fillna(0) > 0).astype(int)
    combined = combined.sort_values(["ts_code", "trade_date", "_score"])
    combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    combined = combined.drop(columns="_score")
    return combined.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只")
    ap.add_argument("--codes", default="", help="指定代码，逗号分隔")
    ap.add_argument("--since", default="", help="丢弃该日期之前的数据")
    ap.add_argument("--sleep", type=float, default=0.35, help="每只之间间隔秒数")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--refresh-all", action="store_true", help="忽略进度全部重抓")
    ap.add_argument("--retry-failed", action="store_true", help="只重跑上次失败的")
    args = ap.parse_args()

    lst_path = DATA_DIR / "etf_list.parquet"
    if not lst_path.exists():
        print("[error] 缺少 etf_list.parquet，请先运行 update_daily.py")
        return 1
    lst = pd.read_parquet(lst_path)

    prog = load_progress()
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        lst = lst[lst.symbol.isin(want)]
    elif args.retry_failed:
        lst = lst[lst.ts_code.isin(set(prog.get("failed", {})))]
    elif not args.refresh_all:
        before = len(lst)
        lst = lst[~lst.ts_code.isin(set(prog.get("done", {})))]
        print(f"[backfill-ths] 已完成 {before - len(lst)} 只，本次待抓 {len(lst)} 只")

    if args.limit:
        lst = lst.head(args.limit)

    total = len(lst)
    print(f"[backfill-ths] 共 {total} 只，间隔 {args.sleep}s", flush=True)

    bucket: dict[str, list[pd.DataFrame]] = {}
    empty: list[str] = []
    t0 = time.time()

    for i, row in enumerate(lst.itertuples(), 1):
        df = fetch_history(row.symbol, row.market, retries=args.retries)
        if df.empty:
            empty.append(row.ts_code)
            prog.setdefault("failed", {})[row.ts_code] = "无数据"
        else:
            if args.since:
                df = df[df.trade_date >= args.since]
            for year, chunk in df.groupby(df["trade_date"].str[:4]):
                bucket.setdefault(year, []).append(chunk)
            prog.setdefault("done", {})[row.ts_code] = {
                "rows": int(len(df)),
                "first": str(df.trade_date.min()),
                "last": str(df.trade_date.max()),
            }
            prog.get("failed", {}).pop(row.ts_code, None)

        if i % 100 == 0:
            el = time.time() - t0
            print(f"  进度 {i}/{total}  成功 {len(prog['done'])}  空 {len(empty)}  "
                  f"用时 {el/60:.1f}分  预计剩余 {(total-i)/(i/el)/60:.1f}分", flush=True)
            save_progress(prog)
        time.sleep(args.sleep * random.uniform(0.7, 1.3))

    save_progress(prog)
    if not bucket:
        print("[warn] 本次没有抓到任何数据，未改动 parquet")
        return 0

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    for year, chunks in sorted(bucket.items()):
        new = pd.concat(chunks, ignore_index=True)
        path = DAILY_DIR / f"{year}.parquet"
        merged = merge_year(path, new)
        merged.to_parquet(path, index=False)
        print(f"[{year}] {len(merged):>9} 行  "
              f"成交额非空 {merged.amount.notna().sum():>9} 行", flush=True)

    m = rebuild_manifest(load_manifest())
    daily = m["datasets"].get("etf_daily", {})
    if daily and daily.get("files"):
        last = DAILY_DIR / f"{max(f['year'] for f in daily['files'])}.parquet"
        m["latest_trade_date"] = pd.read_parquet(last, columns=["trade_date"])["trade_date"].max()
    save_manifest(m)
    print(f"[ok] 累计 {daily.get('rows', 0)} 行，用时 {(time.time()-t0)/60:.1f} 分钟")
    if empty:
        print(f"[warn] {len(empty)} 只无数据：{', '.join(empty[:12])}"
              f"{' …' if len(empty) > 12 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
