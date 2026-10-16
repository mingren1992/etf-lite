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


def fetch_kline(prefix: str, symbol: str, start: str, end: str) -> pd.DataFrame:
    """拉单只 ETF 的日K，返回标准化 DataFrame"""
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    rows = []
    cur = s
    while cur <= e:
        stop = min(cur + timedelta(days=CHUNK_DAYS), e)
        try:
            resp = SESSION.get(
                KLINE_URL,
                params={"param": f"{prefix}{symbol},day,{cur:%Y-%m-%d},{stop:%Y-%m-%d},500,"},
                timeout=15,
            )
            node = resp.json().get("data", {}).get(f"{prefix}{symbol}", {})
            days = node.get("day") or node.get("qfqday") or []
            rows.extend(days)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(SLEEP)
        cur = stop + timedelta(days=1)

    if not rows:
        return pd.DataFrame()

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    args = ap.parse_args()

    lst_path = DATA_DIR / "etf_list.parquet"
    if not lst_path.exists():
        print("[error] 缺少 etf_list.parquet，请先运行 update_daily.py")
        return 1
    lst = pd.read_parquet(lst_path)
    if args.limit:
        lst = lst.head(args.limit)

    print(f"[backfill] {args.start} ~ {args.end}，共 {len(lst)} 只 ETF")
    bucket: dict[str, list[pd.DataFrame]] = {}
    for i, row in enumerate(lst.itertuples(), 1):
        prefix = "sh" if row.market == "SH" else "sz"
        df = fetch_kline(prefix, row.symbol, args.start, args.end)
        if not df.empty:
            for year, chunk in df.groupby(df["trade_date"].str[:4]):
                bucket.setdefault(year, []).append(chunk)
        if i % 50 == 0:
            print(f"  进度 {i}/{len(lst)}")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
