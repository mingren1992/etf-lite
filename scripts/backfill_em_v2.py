#!/usr/bin/env python3
"""用东方财富 K 线接口补 amount（成交额）与 turnover_rate（换手率）

为什么用它：
  东财 kline 的 f57 = 成交额、f61 = 换手率，全历史一次性给出，
  是唯一能同时补齐这两个字段的公开源（雪球 ETF 换手率恒为 0，
  同花顺/腾讯/新浪都没有成交额）。

前置条件：
  本机必须能访问 push2his.eastmoney.com。先用 --check 自检：
      python scripts/backfill_em_v2.py --check
  返回 ok 才能跑全量；返回 fail 说明网络被拦，换网络（热点/代理）再试。

用法：
    python scripts/backfill_em_v2.py --check             # 连通性自检
    python scripts/backfill_em_v2.py --codes 510300      # 单只试跑
    python scripts/backfill_em_v2.py --limit 20          # 前 20 只
    python scripts/backfill_em_v2.py                     # 全量（断点续跑）
    python scripts/backfill_em_v2.py --retry-failed      # 只重试失败
"""
from __future__ import annotations

import argparse
import json
import os
import random
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

BASE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PROGRESS = DATA_DIR / ".backfill_em_progress.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update({"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})

FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
# 依次为：日期,开盘,收盘,最高,最低,成交量(手),成交额(元),振幅,涨跌幅,涨跌额,换手率


def secid(ts_code: str) -> str:
    code, mkt = ts_code.split(".")
    return f"{1 if mkt == 'SH' else 0}.{code}"


def check() -> bool:
    """连通性自检"""
    try:
        r = SESSION.get(BASE, params={
            "secid": "1.510300", "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": FIELDS2, "klt": 101, "fqt": 0, "lmt": 3,
        }, timeout=20)
    except requests.RequestException as e:
        print(f"[check] fail  {type(e).__name__}: {e}")
        return False
    if r.status_code != 200 or not r.text.strip():
        print(f"[check] fail  http={r.status_code} size={len(r.text)}")
        return False
    try:
        klines = ((r.json().get("data") or {}).get("klines") or [])
    except ValueError:
        print(f"[check] fail  返回非 JSON：{r.text[:80]}")
        return False
    if not klines:
        print("[check] fail  无数据")
        return False
    print(f"[check] ok  样例：{klines[-1]}")
    return True


def fetch(ts_code: str, retries: int = 3) -> pd.DataFrame:
    """拉全历史，返回 date/amount(万元)/turnover_rate"""
    for attempt in range(retries):
        try:
            r = SESSION.get(BASE, params={
                "secid": secid(ts_code), "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": FIELDS2, "klt": 101, "fqt": 0,
                "beg": 0, "end": 20500101, "lmt": 100000,
            }, timeout=25)
            klines = ((r.json().get("data") or {}).get("klines") or [])
            if klines:
                return _parse(klines, ts_code)
        except (requests.RequestException, ValueError, KeyError):
            pass
        time.sleep(1.5 * (attempt + 1) + random.random())
    return pd.DataFrame()


def _parse(klines: list, ts_code: str) -> pd.DataFrame:
    rows = []
    for line in klines:
        p = line.split(",")
        if len(p) < 11:
            continue
        try:
            amt = float(p[6]) if p[6] not in ("-", "") else float("nan")
            turn = float(p[10]) if p[10] not in ("-", "") else float("nan")
        except ValueError:
            continue
        rows.append({
            "ts_code": ts_code,
            "trade_date": p[0],
            "amount": round(amt / 10000, 4) if amt == amt else float("nan"),  # 元 → 万元
            "turnover_rate": turn if turn == turn else float("nan"),
        })
    df = pd.DataFrame(rows)
    for c in ("amount", "turnover_rate"):
        df[c] = df[c].astype("float64")
    return df


def load_progress() -> dict:
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"done": {}, "failed": {}}


def save_progress(p: dict) -> None:
    p["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def flush(bucket: dict, stats: dict) -> None:
    """把已抓到的数据写回年度 parquet，只覆盖 amount / turnover_rate 两列。

    分批调用可让内存占用保持恒定（全市场 1700 只全攒在内存里约 1GB，
    在 GitHub Actions 的 7GB 机器上也能跑，但分批更稳）。
    """
    if not bucket:
        return
    for year, chunks in sorted(bucket.items()):
        new = pd.concat(chunks, ignore_index=True)
        new = new.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        path = DAILY_DIR / f"{year}.parquet"
        if not path.exists():
            continue
        old = pd.read_parquet(path)
        n_before = int(old.amount.notna().sum() + old.turnover_rate.notna().sum())

        old = old.merge(new, on=["ts_code", "trade_date"], how="left",
                        suffixes=("", "_new"))
        old["amount"] = old["amount"].where(old["amount"].notna(), old["amount_new"])
        old["turnover_rate"] = old["turnover_rate"].where(
            old["turnover_rate"].notna(), old["turnover_rate_new"])
        old = old.drop(columns=["amount_new", "turnover_rate_new"])
        old = old.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        old.to_parquet(path, index=False)

        n_after = int(old.amount.notna().sum() + old.turnover_rate.notna().sum())
        stats[year] = (len(old), n_before, n_after)
        print(f"[{year}] {len(old):>9} 行  字段非空 {n_before:>9} → {n_after:>9}", flush=True)
    bucket.clear()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--check", action="store_true", help="只做连通性自检")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--refresh-all", action="store_true")
    ap.add_argument("--flush-every", type=int, default=300, help="每 N 只写一次盘")
    args = ap.parse_args()

    if args.check:
        return 0 if check() else 2

    lst_path = DATA_DIR / "etf_list.parquet"
    if not lst_path.exists():
        print("[error] 缺少 etf_list.parquet")
        return 1
    lst = pd.read_parquet(lst_path)

    prog = load_progress()
    if args.codes:
        want = {c.strip() for c in args.codes.split(",") if c.strip()}
        lst = lst[lst.symbol.isin(want)]
    elif args.retry_failed:
        lst = lst[lst.ts_code.isin(set(prog.get("failed", {})))]
    elif not args.refresh_all:
        lst = lst[~lst.ts_code.isin(set(prog.get("done", {})))]
    if args.limit:
        lst = lst.head(args.limit)

    if lst.empty:
        print("[ok] 没有待处理的 ETF")
        return 0

    total = len(lst)
    print(f"[backfill-em] 共 {total} 只，间隔 {args.sleep}s", flush=True)

    bucket: dict[str, list[pd.DataFrame]] = {}
    stats: dict[str, tuple] = {}
    empty: list[str] = []
    t0 = time.time()

    for i, row in enumerate(lst.itertuples(), 1):
        df = fetch(row.ts_code, retries=args.retries)
        if df.empty:
            empty.append(row.ts_code)
            prog.setdefault("failed", {})[row.ts_code] = "无数据"
        else:
            for year, chunk in df.groupby(df["trade_date"].str[:4]):
                bucket.setdefault(year, []).append(chunk)
            prog.setdefault("done", {})[row.ts_code] = {
                "rows": int(len(df)),
                "amount_rows": int(df.amount.notna().sum()),
                "turnover_rows": int(df.turnover_rate.notna().sum()),
            }
            prog.get("failed", {}).pop(row.ts_code, None)

        if i % 50 == 0:
            el = time.time() - t0
            print(f"  进度 {i}/{total}  空 {len(empty)}  用时 {el/60:.1f}分  "
                  f"预计剩余 {(total-i)/(i/el)/60:.1f}分", flush=True)
            save_progress(prog)

        if args.flush_every and i % args.flush_every == 0:
            flush(bucket, stats)

        time.sleep(args.sleep * random.uniform(0.7, 1.3))

    save_progress(prog)
    flush(bucket, stats)

    if not stats:
        print("[warn] 本次未抓到数据")
        return 0

    m = rebuild_manifest(load_manifest())
    save_manifest(m)
    print(f"[ok] 用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    if empty:
        print(f"[warn] {len(empty)} 只无数据（可用 --retry-failed 重试）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
