#!/usr/bin/env python3
"""用雪球接口补全 amount（成交额）与 turnover_rate（换手率）

为什么需要这一步：
  同花顺 all.js 只给价格和成交量，不给成交额；
  东方财富虽然字段最全，但会按 IP 封禁（一次几小时），不可靠。
  雪球 kline 接口一次返回上市至今全历史，含 amount / turnoverrate，且不会被封。

已知限制：
  雪球对 2018 年以前的成交额字段返回 null（不是本脚本的问题），
  2018 年起才有；更早的成交额留空，不做任何估算填充。

复权口径：type=normal（不复权），与每日快照、同花顺 00 口径一致。

用法：
    python scripts/backfill_xq.py                    # 全量，跳过已完成
    python scripts/backfill_xq.py --codes 510300,159915
    python scripts/backfill_xq.py --refresh-all
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

BASE = "https://stock.xueqiu.com/v5/stock/chart/kline.json"
WARMUP = "https://xueqiu.com/hq"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
PROGRESS = DATA_DIR / ".backfill_xq_progress.json"

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update({"User-Agent": UA, "Referer": "https://xueqiu.com/",
                        "Accept": "application/json, text/plain, */*"})


def warmup() -> bool:
    """访问首页拿 xq_a_token，没有它就 400016"""
    try:
        SESSION.get(WARMUP, timeout=15)
        return "xq_a_token" in SESSION.cookies.get_dict()
    except requests.RequestException:
        return False


def fetch_history(symbol: str, market: str, retries: int = 3) -> pd.DataFrame:
    now_ms = int(time.time() * 1000)
    for attempt in range(retries):
        try:
            r = SESSION.get(BASE, params={
                "symbol": f"{market}{symbol}",
                "begin": now_ms,
                "period": "day",
                "type": "normal",      # 不复权
                "count": -8000,
                "indicator": "kline",
            }, timeout=25)
            js = r.json()
            if js.get("error_code") == 400016:      # token 失效，重取
                warmup()
                time.sleep(1.5)
                continue
            items = (js.get("data") or {}).get("item") or []
            if items:
                return _parse(items, symbol, market)
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.2 * (attempt + 1))
    return pd.DataFrame()


def _parse(items: list, symbol: str, market: str) -> pd.DataFrame:
    rows = []
    prev = None
    for r in items:
        ts, vol, o, h, low, c, _chg, pct, turn, amt = r[:10]
        close = float(c)
        rows.append({
            "ts_code": f"{symbol}.{market}",
            "trade_date": datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d"),
            "open": round(float(o), 4),
            "high": round(float(h), 4),
            "low": round(float(low), 4),
            "close": round(close, 4),
            "volume": int(round(int(vol) / 100)) if vol else 0,            # 股 → 手
            "amount": round(float(amt) / 10000, 4) if amt else float("nan"),  # 元 → 万元
            "change_pct": round(float(pct), 4) if pct is not None else (
                round((close - prev) / prev * 100, 4) if prev else float("nan")),
            "turnover_rate": float(turn) if turn not in (None, 0, 0.0) else float("nan"),
        })
        prev = close
    df = pd.DataFrame(rows)
    for c in ("amount", "change_pct", "turnover_rate"):
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
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    p["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--refresh-all", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
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
        lst = lst[~lst.ts_code.isin(set(prog.get("done", {})))]
    if args.limit:
        lst = lst.head(args.limit)

    if not warmup():
        print("[warn] 未取到 xq_a_token，接口可能返回 400016")

    total = len(lst)
    print(f"[backfill-xq] 共 {total} 只，间隔 {args.sleep}s", flush=True)

    bucket: dict[str, list[pd.DataFrame]] = {}
    empty: list[str] = []
    t0 = time.time()
    for i, row in enumerate(lst.itertuples(), 1):
        df = fetch_history(row.symbol, row.market, retries=args.retries)
        if df.empty:
            empty.append(row.ts_code)
            prog.setdefault("failed", {})[row.ts_code] = "无数据"
        else:
            for year, chunk in df.groupby(df["trade_date"].str[:4]):
                bucket.setdefault(year, []).append(chunk)
            prog.setdefault("done", {})[row.ts_code] = {
                "rows": int(len(df)),
                "amount_rows": int(df.amount.notna().sum()),
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
        print("[warn] 本次没有抓到任何数据")
        return 0

    for year, chunks in sorted(bucket.items()):
        new = pd.concat(chunks, ignore_index=True)
        path = DAILY_DIR / f"{year}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            # 以雪球为准更新同键行（价格口径一致，但补上 amount/turnover_rate）
            upd = new.set_index(["ts_code", "trade_date"])
            old = old.set_index(["ts_code", "trade_date"])
            old.update(upd)                       # 仅用非 NaN 值覆盖
            add = upd[~upd.index.isin(old.index)]  # 雪球有、本地没有的日期
            merged = pd.concat([old, add]).reset_index()
        else:
            merged = new
        merged["amount"] = merged["amount"].where(merged["amount"] > 0)  # 0 视为未知
        merged = merged.drop_duplicates(["ts_code", "trade_date"], keep="last")
        merged = merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        merged.to_parquet(path, index=False)
        print(f"[{year}] {len(merged):>9} 行  成交额非空 {merged.amount.notna().sum():>9} 行",
              flush=True)

    m = rebuild_manifest(load_manifest())
    daily = m["datasets"].get("etf_daily", {})
    if daily and daily.get("files"):
        last = DAILY_DIR / f"{max(f['year'] for f in daily['files'])}.parquet"
        m["latest_trade_date"] = pd.read_parquet(last, columns=["trade_date"])["trade_date"].max()
    save_manifest(m)
    print(f"[ok] 累计 {daily.get('rows', 0):,} 行，用时 {(time.time()-t0)/60:.1f} 分钟")
    if empty:
        print(f"[warn] {len(empty)} 只无数据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
