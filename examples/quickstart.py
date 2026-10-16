#!/usr/bin/env python3
"""本地自检：不依赖 GitHub，直接读本地 data/ 目录验证 SDK 全链路

用法：
    python examples/quickstart.py                # 读本地仓库
    python examples/quickstart.py --repo 某人/etf-lite   # 读线上仓库
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_lite  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None, help="线上仓库 owner/repo（省则读本地）")
    args = ap.parse_args()

    if args.repo:
        etf = etf_lite.ETFLite(repo=args.repo, verbose=True)
    else:
        etf = etf_lite.ETFLite(local_dir=ROOT, verbose=True)

    info = etf.info()
    print("\n── 数据概况 ─────────────────────────────")
    for k, v in info.items():
        print(f"  {k:20s} {v}")

    lst = etf.get_etf_list()
    print(f"\n── ETF 列表 {len(lst)} 行 ─────────────────")
    print(lst.head(5).to_string(index=False))

    df = etf.get_etf_daily("510300", start="2026-01-01")
    print(f"\n── 510300 沪深300ETF 2026年行情 {len(df)} 行 ──")
    print(df.head(3).to_string(index=False))
    print(df.tail(3).to_string(index=False))

    all_today = etf.get_etf_daily(start=info["latest_trade_date"])
    print(f"\n── 最新交易日全市场 {len(all_today)} 只 ──")
    top = all_today.nlargest(5, "amount")[["ts_code", "close", "change_pct", "amount"]]
    print(top.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
