#!/usr/bin/env python3
"""每日增量更新（GitHub Actions 里跑这个）

流程：
  1. 确保 etf_list.parquet 存在（缺失或超过 7 天则重新扫描）
  2. 抓当日全市场 ETF 行情快照
  3. 合并进 data/etf_daily/{年份}.parquet（按 ts_code+trade_date 去重，新值覆盖）
  4. 重建 manifest.json

用法：
    python scripts/update_daily.py            # 自动判断
    python scripts/update_daily.py --refresh-list   # 强制重扫 ETF 列表
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

from common import DAILY_DIR, DATA_DIR, load_manifest, rebuild_manifest, save_manifest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etf_lite.sources import fetch_daily_snapshot, fetch_etf_list  # noqa: E402

LIST_PATH = DATA_DIR / "etf_list.parquet"
LIST_MAX_AGE_DAYS = 7


def ensure_etf_list(refresh: bool = False) -> pd.DataFrame:
    stale = (
        not LIST_PATH.exists()
        or (time.time() - LIST_PATH.stat().st_mtime) > LIST_MAX_AGE_DAYS * 86400
    )
    if refresh or stale:
        df = fetch_etf_list()
        df.to_parquet(LIST_PATH, index=False)
        print(f"[etf_list] 已写入 {LIST_PATH.name}（{len(df)} 行）")
    else:
        df = pd.read_parquet(LIST_PATH)
        print(f"[etf_list] 复用已有列表（{len(df)} 行）")
    return df


def merge_daily(snapshot: pd.DataFrame) -> Path:
    """把当日快照合并进当年 parquet，返回文件路径"""
    year = snapshot["trade_date"].iloc[0][:4]
    path = DAILY_DIR / f"{year}.parquet"
    DAILY_DIR.mkdir(parents=True, exist_ok=True)

    if path.exists():
        old = pd.read_parquet(path)
        old = old[old["trade_date"] < snapshot["trade_date"].iloc[0]]
        merged = pd.concat([old, snapshot], ignore_index=True)
        print(f"[merge] {year}.parquet：原有 {len(old)} 行 + 新增 {len(snapshot)} 行")
    else:
        merged = snapshot
        print(f"[merge] 新建 {year}.parquet，{len(snapshot)} 行")

    merged = merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    merged.to_parquet(path, index=False)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-list", action="store_true", help="强制重扫 ETF 列表")
    args = ap.parse_args()

    etf_list = ensure_etf_list(refresh=args.refresh_list)
    snapshot = fetch_daily_snapshot(etf_list)
    if snapshot.empty:
        print("[error] 未抓取到任何行情，退出（可能是非交易日或接口异常）")
        return 1

    path = merge_daily(snapshot)

    m = load_manifest()
    m["latest_trade_date"] = snapshot["trade_date"].iloc[0]
    m = rebuild_manifest(m)
    save_manifest(m)

    daily = m["datasets"]["etf_daily"]
    print(f"[ok] 最新交易日 {m['latest_trade_date']}，"
          f"累计 {daily['rows']} 行 / {len(daily['files'])} 个年度文件")
    print(f"[ok] 已更新 {path.name} 与 manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
