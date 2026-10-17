#!/usr/bin/env python3
"""数据体检：跑一遍所有年度 parquet，报告覆盖度与异常

用法：
    python scripts/check_data.py            # 只报告
    python scripts/check_data.py --fix      # 顺手丢掉明显坏行（价格 <= 0）
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DAILY_DIR, DATA_DIR

COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="删除价格 <= 0 的坏行")
    args = ap.parse_args()

    files = sorted(glob.glob(str(DAILY_DIR / "*.parquet")))
    if not files:
        print("[error] data/etf_daily 下没有 parquet")
        return 1

    frames = []
    for p in files:
        part = pd.read_parquet(p)
        frames.append(part[[c for c in COLS if c in part.columns]])
    df = pd.concat(frames, ignore_index=True)
    total = len(df)

    bad_low = df.low <= 0
    bad_close = df.close <= 0
    bad_hl = df.high < df.low
    dup = df.duplicated(["ts_code", "trade_date"])
    bad_vol = df.volume < 0

    print(f"文件      {len(files)} 个（{files[0][-12:-8]} ~ {files[-1][-12:-8]}）")
    print(f"总行数    {total:,}")
    print(f"标的数    {df.ts_code.nunique():,}")
    print(f"日期范围  {df.trade_date.min()} ~ {df.trade_date.max()}")
    print(f"成交额非空 {df.amount.notna().sum():,} ({df.amount.notna().mean()*100:.1f}%)")
    print()
    print("异常检查")
    for name, mask in [("重复行(ts_code+date)", dup), ("收盘价<=0", bad_close),
                       ("最低价<=0", bad_low), ("最高<最低", bad_hl), ("成交量为负", bad_vol)]:
        n = int(mask.sum())
        print(f"  {name:<20} {n:>8}" + ("  ✗" if n else "  ✓"))

    if args.fix and (bad_low.sum() or bad_close.sum()):
        drop = bad_low | bad_close | bad_hl
        print(f"\n[fix] 删除 {int(drop.sum())} 行坏数据")
        for p in files:
            year = Path(p).stem
            part = pd.read_parquet(p)
            before = len(part)
            part = part[(part.low > 0) & (part.close > 0) & (part.high >= part.low)]
            if len(part) != before:
                part.to_parquet(p, index=False)
                print(f"  {year}: {before} → {len(part)}")

    # 覆盖度：每年有多少标的、平均每只多少行
    print("\n年度覆盖")
    g = df.groupby(df.trade_date.str[:4])
    for year, part in g:
        print(f"  {year}  {len(part):>8} 行  {part.ts_code.nunique():>5} 只  "
              f"{part.trade_date.nunique():>4} 个交易日")

    # 最近的交易日是否连续
    dates = sorted(df.trade_date.unique())
    if len(dates) > 1:
        gaps = [(a, b) for a, b in zip(dates, dates[1:])
                if (pd.Timestamp(b) - pd.Timestamp(a)).days > 10]
        print(f"\n超过 10 天的日期空档: {gaps if gaps else '无'}")

    man = json.load(open(DATA_DIR / "manifest.json"))
    print(f"\nmanifest 记录 {man['datasets']['etf_daily']['rows']:,} 行 "
          f"（实际 {total:,}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
