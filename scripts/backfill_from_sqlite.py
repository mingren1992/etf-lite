#!/usr/bin/env python3
"""一次性历史回填：把已有 SQLite 库导出成按年切分的 parquet

用法：
    python scripts/backfill_from_sqlite.py \
        --db "../etf-index-data/data/etf_data.db"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from common import DAILY_DIR, DATA_DIR, load_manifest, rebuild_manifest, save_manifest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="源 SQLite 数据库路径")
    args = ap.parse_args()

    db = Path(args.db).expanduser().resolve()
    if not db.exists():
        print(f"[error] 数据库不存在：{db}")
        return 1

    con = sqlite3.connect(db)

    # ── ETF 列表 ────────────────────────────────────────────────
    lst = pd.read_sql("SELECT ts_code, symbol, market, name FROM etf_list", con)
    lst = lst.dropna(subset=["name"]).sort_values("ts_code").reset_index(drop=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lst.to_parquet(DATA_DIR / "etf_list.parquet", index=False)
    print(f"[etf_list] 导出 {len(lst)} 行")

    # ── 日行情按年切分 ──────────────────────────────────────────
    daily = pd.read_sql(
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount, change_pct "
        "FROM etf_daily ORDER BY trade_date, ts_code",
        con,
    )
    con.close()
    if daily.empty:
        print("[error] etf_daily 无数据")
        return 1

    daily["trade_date"] = daily["trade_date"].astype(str).str.replace("/", "-", regex=False)
    daily["turnover_rate"] = pd.NA

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    for year, chunk in daily.groupby(daily["trade_date"].str[:4]):
        path = DAILY_DIR / f"{year}.parquet"
        if path.exists():                      # 已有文件则合并，避免覆盖每日增量
            old = pd.read_parquet(path)
            chunk = pd.concat([chunk, old], ignore_index=True)
            chunk = chunk.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        chunk = chunk.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        chunk.to_parquet(path, index=False)
        print(f"[{year}] {len(chunk)} 行 → {path.name}")

    m = load_manifest()
    m["latest_trade_date"] = daily["trade_date"].max()
    m = rebuild_manifest(m)
    save_manifest(m)
    print(f"[ok] manifest 已更新，累计 {m['datasets']['etf_daily']['rows']} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
