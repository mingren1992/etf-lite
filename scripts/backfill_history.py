#!/usr/bin/env python3
"""历史行情爬取（补齐缺口 / 首次建仓历史）

按年输出 parquet，可直接和每日增量共存。
一次性使用，本地跑或手动触发 GitHub Actions。

用法：
    python scripts/backfill_history.py --start 2026-07-04 --end 2026-10-16
    python scripts/backfill_history.py --start 2015-01-01            # 全量重建
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from common import DAILY_DIR, DATA_DIR, load_manifest, rebuild_manifest, save_manifest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

SESSION = requests.Session()
SESSION.trust_env = False
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
CHUNK_DAYS = 480          # 接口单次约返回 500 根
SLEEP = 0.15


def _kline_once(prefix: str, symbol: str, start: str, end: str) -> list:
    """单次请求，失败返回空列表"""
    try:
        resp = SESSION.get(
            KLINE_URL,
            params={"param": f"{prefix}{symbol},day,{start},{end},500,"},
            timeout=15,
        )
        node = resp.json().get("data", {}).get(f"{prefix}{symbol}", {})
        return node.get("day") or node.get("qfqday") or []
    except Exception:  # noqa: BLE001
        return []


def fetch_kline(prefix: str, symbol: str, start: str, end: str,
                retries: int = 3, verbose: bool = False) -> pd.DataFrame:
    """拉单只 ETF 的日K，返回标准化 DataFrame

    行情接口在请求密集时会返回空结果（限流），因此空结果也要重试。
    """
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    rows = []
    cur = s
    while cur <= e:
        stop = min(cur + timedelta(days=CHUNK_DAYS), e)
        for attempt in range(retries):
            got = _kline_once(prefix, symbol, f"{cur:%Y-%m-%d}", f"{stop:%Y-%m-%d}")
            if got:
                rows.extend(got)
                break
            time.sleep(0.6 * (attempt + 1))       # 退避后重试
        time.sleep(SLEEP)
        cur = stop + timedelta(days=1)

    if not rows:
        return pd.DataFrame()
    if verbose:
        print(f"    {symbol}: {len(rows)} 行")

    recs = []
    prev_close = None
    for d in rows:
        if not isinstance(d, (list, tuple)) or len(d) < 6:
            continue
        close = float(d[2])
        recs.append({
            "ts_code": f"{symbol}.{'SH' if prefix == 'sh' else 'SZ'}",
            "trade_date": d[0],
            "open": float(d[1]),
            "high": float(d[3]),
            "low": float(d[4]),
            "close": close,
            "volume": int(float(d[5])),
            "amount": 0.0,
            "change_pct": round((close - prev_close) / prev_close * 100, 4) if prev_close else 0.0,
            "turnover_rate": pd.NA,
        })
        prev_close = close
    return pd.DataFrame(recs)


def coverage_in_range(start: str, end: str) -> tuple[set[str], int]:
    """统计已有数据在 [start, end] 区间内的覆盖情况

    返回 (覆盖已达标的代码集合, 区间内应有的交易日数)
    """
    dates: set[str] = set()
    cov: dict[str, int] = {}
    for p in sorted(DAILY_DIR.glob("*.parquet")):
        df = pd.read_parquet(p, columns=["ts_code", "trade_date"])
        df = df[(df.trade_date >= start) & (df.trade_date <= end)]
        if df.empty:
            continue
        dates.update(df.trade_date.unique())
        for k, v in df.groupby("ts_code").size().items():
            cov[k] = cov.get(k, 0) + int(v)
    expected = len(dates)
    full = {c for c, n in cov.items() if expected and n >= expected * 0.9}
    return full, expected


def main() -> int:
    global SLEEP

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    ap.add_argument("--only-missing", action="store_true",
                    help="只补区间内数据不完整的代码（断点续跑）")
    ap.add_argument("--sleep", type=float, default=SLEEP, help="每次请求间隔秒数")
    ap.add_argument("--retries", type=int, default=3, help="空结果重试次数")
    args = ap.parse_args()

    SLEEP = args.sleep

    lst_path = DATA_DIR / "etf_list.parquet"
    if not lst_path.exists():
        print("[error] 缺少 etf_list.parquet，请先运行 update_daily.py")
        return 1
    lst = pd.read_parquet(lst_path)

    if args.only_missing:
        full, expected = coverage_in_range(args.start, args.end)
        before = len(lst)
        lst = lst[~lst["ts_code"].isin(full)]
        print(f"[backfill] 区间应有 {expected} 个交易日，已有 {len(full)} 只达标，"
              f"待补 {len(lst)} 只（跳过 {before - len(lst)} 只）")

    if args.limit:
        lst = lst.head(args.limit)

    print(f"[backfill] {args.start} ~ {args.end}，共 {len(lst)} 只 ETF")
    bucket: dict[str, list[pd.DataFrame]] = {}
    empty: list[str] = []
    for i, row in enumerate(lst.itertuples(), 1):
        prefix = "sh" if row.market == "SH" else "sz"
        df = fetch_kline(prefix, row.symbol, args.start, args.end, retries=args.retries)
        if df.empty:
            empty.append(row.ts_code)
        else:
            for year, chunk in df.groupby(df["trade_date"].str[:4]):
                bucket.setdefault(year, []).append(chunk)
        if i % 50 == 0:
            print(f"  进度 {i}/{len(lst)}", flush=True)

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    for year, chunks in sorted(bucket.items()):
        new = pd.concat(chunks, ignore_index=True)
        path = DAILY_DIR / f"{year}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            new = pd.concat([old, new], ignore_index=True)
        new = new.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        new = new.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        new.to_parquet(path, index=False)
        print(f"[{year}] {len(new)} 行 → {path.name}")

    m = load_manifest()
    m = rebuild_manifest(m)
    daily = m["datasets"].get("etf_daily", {})
    if daily:
        m["latest_trade_date"] = pd.read_parquet(
            sorted(DAILY_DIR.glob("*.parquet"))[-1], columns=["trade_date"]
        )["trade_date"].max()
    save_manifest(m)
    print(f"[ok] 累计 {daily.get('rows', 0)} 行")
    if empty:
        print(f"[warn] {len(empty)} 只无数据（可能未上市/已退市/接口无记录）："
              f"{', '.join(empty[:10])}{' …' if len(empty) > 10 else ''}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
