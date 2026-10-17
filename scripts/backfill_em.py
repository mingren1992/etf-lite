#!/usr/bin/env python3
"""全量历史重建（东方财富数据源，带自动限流恢复）

相比腾讯 K 线接口的优势：
  1. 一次请求返回上市至今全部历史，无需分片
  2. 自带成交额（amount），腾讯 K 线没有这个字段
  3. 涨跌幅直接给出

价格口径：不复权（fqt=0），与每日快照一致。改成 FQT=1 即前复权。

东方财富会对密集请求做 IP 级封禁（表现为 `Empty reply from server`），
本脚本内置熔断：连续失败到阈值就停下来等，每隔一段时间探测一次，
解封后自动继续。进度写进 data/.backfill_progress.json，随时可续跑。

用法：
    python scripts/backfill_em.py                      # 全量重建，可重复执行
    python scripts/backfill_em.py --limit 20           # 先试 20 只
    python scripts/backfill_em.py --codes 510300,159915
    python scripts/backfill_em.py --retry-failed       # 只重跑上次失败的
    python scripts/backfill_em.py --refresh-all        # 忽略进度全部重抓
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

HOSTS = [
    "https://push2his.eastmoney.com",
    "https://1.push2his.eastmoney.com",
    "https://7.push2his.eastmoney.com",
    "https://33.push2his.eastmoney.com",
    "https://62.push2his.eastmoney.com",
    "https://82.push2his.eastmoney.com",
]
PATH = "/api/qt/stock/kline/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}
FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58"   # 日期,开,收,高,低,量,额,涨跌幅
FQT = 0
PROGRESS = DATA_DIR / ".backfill_progress.json"


class Blocked(Exception):
    """被限流/封禁"""


SESSION = requests.Session()
SESSION.trust_env = False
SESSION.headers.update(HEADERS)


def probe(host: str | None = None) -> bool:
    """用最轻的请求探测是否已解封"""
    h = host or random.choice(HOSTS)
    try:
        r = SESSION.get(
            h + PATH,
            params={"secid": "1.510300", "fields1": "f1", "fields2": "f51",
                    "klt": "101", "fqt": "0", "beg": "20260101", "end": "20500101", "lmt": "1"},
            timeout=10,
        )
        return r.status_code == 200 and 'klines' in r.text
    except Exception:  # noqa: BLE001
        return False


def fetch_history(symbol: str, market: str, host: str) -> pd.DataFrame:
    """拉单只 ETF 全部历史日线；被限流时抛 Blocked"""
    secid = f"{'1' if market == 'SH' else '0'}.{symbol}"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": FIELDS2,
        "klt": "101",
        "fqt": str(FQT),
        "beg": "19900101",
        "end": "20500101",
        "lmt": "10000",
    }
    try:
        r = SESSION.get(host + PATH, params=params, timeout=25)
    except requests.RequestException as e:
        raise Blocked(str(e)) from e
    if r.status_code != 200:
        raise Blocked(f"HTTP {r.status_code}")
    if not r.text.strip():
        raise Blocked("空响应")
    try:
        data = (r.json() or {}).get("data") or {}
    except ValueError as e:
        raise Blocked(f"非 JSON 响应：{r.text[:60]}") from e
    klines = data.get("klines") or []
    if not klines:
        return pd.DataFrame()          # 正常返回但没有数据（未上市/退市）
    return _parse(klines, symbol, market)


def _parse(klines: list[str], symbol: str, market: str) -> pd.DataFrame:
    recs = []
    for line in klines:
        p = line.split(",")
        if len(p) < 8:
            continue
        try:
            recs.append({
                "ts_code": f"{symbol}.{market}",
                "trade_date": p[0],
                "open": float(p[1]),
                "high": float(p[3]),
                "low": float(p[4]),
                "close": float(p[2]),
                "volume": int(float(p[5])) * 100,   # 手 → 份
                "amount": float(p[6]) / 1e4,      # 元 → 万元，与每日快照口径一致
                "change_pct": float(p[7]),
                "turnover_rate": pd.NA,
            })
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(recs)


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
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    ap.add_argument("--codes", default="", help="指定代码，逗号分隔")
    ap.add_argument("--since", default="", help="丢弃该日期之前的数据，如 2015-01-01")
    ap.add_argument("--sleep", type=float, default=0.9, help="每只之间间隔秒数")
    ap.add_argument("--cooldown", type=int, default=120, help="被限流后等待秒数")
    ap.add_argument("--max-wait", type=int, default=10800, help="最长等待解封秒数")
    ap.add_argument("--refresh-all", action="store_true", help="忽略进度，全部重抓")
    ap.add_argument("--retry-failed", action="store_true", help="只重跑上次失败的")
    ap.add_argument("--overwrite", action="store_true",
                    help="整年覆盖写入（危险：丢弃其它标的数据）")
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
        fails = set(prog.get("failed", {}))
        lst = lst[lst.ts_code.isin(fails)]
        print(f"[backfill-em] 重跑上次失败的 {len(lst)} 只")
    elif not args.refresh_all:
        done = set(prog.get("done", {}))
        before = len(lst)
        lst = lst[~lst.ts_code.isin(done)]
        print(f"[backfill-em] 已完成 {before - len(lst)} 只，本次待抓 {len(lst)} 只")

    if args.limit:
        lst = lst.head(args.limit)

    total = len(lst)
    print(f"[backfill-em] 共 {total} 只，fqt={FQT}，起始 {args.since or '上市首日'}，"
          f"间隔 {args.sleep}s", flush=True)

    bucket: dict[str, list[pd.DataFrame]] = {}
    empty: list[str] = []
    consec_fail = 0
    host_idx = 0
    t0 = time.time()
    stalls = 0

    for i, row in enumerate(lst.itertuples(), 1):
        host = HOSTS[host_idx % len(HOSTS)]
        try:
            df = fetch_history(row.symbol, row.market, host)
            consec_fail = 0
        except Blocked as e:
            consec_fail += 1
            prog.setdefault("failed", {})[row.ts_code] = str(e)[:80]
            if consec_fail >= 5:
                stalls += 1
                waited = 0
                print(f"  [限流] 连续失败 {consec_fail} 次（{e}），暂停等待解封…", flush=True)
                while waited < args.max_wait:
                    time.sleep(args.cooldown)
                    waited += args.cooldown
                    if probe():
                        print(f"  [恢复] 等待 {waited}s 后接口恢复，继续", flush=True)
                        break
                    print(f"    仍在封禁中…已等 {waited}s", flush=True)
                else:
                    print(f"  [放弃] 等待 {args.max_wait}s 仍未恢复，保存进度退出", flush=True)
                    break
                consec_fail = 0
                host_idx += 1
            time.sleep(1.5)
            continue

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

        if i % 50 == 0:
            el = time.time() - t0
            rate = i / el
            print(f"  进度 {i}/{total}  成功 {len(prog['done'])}  空 {len(empty)}  "
                  f"用时 {el/60:.1f}分  预计剩余 {(total-i)/rate/60:.1f}分", flush=True)
        if i % 100 == 0:
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
        if not args.overwrite and path.exists():
            new = pd.concat([pd.read_parquet(path), new], ignore_index=True)
        new = new.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        new = new.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        new.to_parquet(path, index=False)
        print(f"[{year}] {len(new):>9} 行  成交额非零 {(new.amount > 0).sum():>9} 行", flush=True)

    m = rebuild_manifest(load_manifest())
    daily = m["datasets"].get("etf_daily", {})
    if daily and daily.get("files"):
        last = DAILY_DIR / f"{max(f['year'] for f in daily['files'])}.parquet"
        m["latest_trade_date"] = pd.read_parquet(last, columns=["trade_date"])["trade_date"].max()
    save_manifest(m)
    print(f"[ok] 累计 {daily.get('rows', 0)} 行，用时 {(time.time()-t0)/60:.1f} 分钟，"
          f"熔断等待 {stalls} 次")
    if empty:
        print(f"[warn] {len(empty)} 只无数据：{', '.join(empty[:12])}"
              f"{' …' if len(empty) > 12 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
