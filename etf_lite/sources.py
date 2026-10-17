"""数据源直连：腾讯财经行情接口

不依赖 AkShare，直接 requests 请求行情源。
- ETF 列表：qt.gtimg.cn 批量快照反推 代码-名称
- 日行情快照：qt.gtimg.cn 批量快照（当日 OHLCV + 成交额 + 换手率）

响应格式（按 ~ 拆分后的下标，注意 key 已提到下标 0）：
    [1] flag   [2] 名称   [3] 代码   [4] 现价   [5] 昨收   [6] 今开
    [31] 时间(20261016161459)  [32] 涨跌额  [33] 涨跌幅
    [34] 最高  [35] 最低  [37] 成交量(手)  [38] 成交额(万元)  [39] 换手率
"""
from __future__ import annotations

import os
import re
import socket
import time

import pandas as pd
import requests

from .config import BATCH_SIZE, REQUEST_INTERVAL, TIMEOUT

# 清掉系统代理 + 强制 IPv4，避免 Clash 之类代理干扰
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type or socket.SOCK_STREAM, proto, flags)


socket.getaddrinfo = _ipv4_only

SESSION = requests.Session()
SESSION.trust_env = False

SH_PREFIXES = ["510", "511", "512", "513", "515", "516", "517", "518", "520",
               "560", "561", "562", "563", "588"]
SZ_PREFIXES = ["159"]

QUOTE_URL = "https://qt.gtimg.cn/q={}"

# 字段下标
I_NAME, I_CODE, I_CLOSE, I_PREV, I_OPEN = 2, 3, 4, 5, 6
I_TIME, I_CHG_PCT, I_HIGH, I_LOW = 31, 33, 34, 35
I_VOL, I_AMOUNT, I_TURNOVER = 37, 38, 39
MIN_FIELDS = 40


def all_candidate_codes() -> list[str]:
    """候选代码池（沪市 510-588 / 深市 159）"""
    codes = []
    for p in SH_PREFIXES:
        codes += [f"sh{p}{i:03d}" for i in range(1000)]
    for p in SZ_PREFIXES:
        codes += [f"sz{p}{i:03d}" for i in range(1000)]
    return codes


def _iter_batches(codes: list[str]):
    for i in range(0, len(codes), BATCH_SIZE):
        yield i // BATCH_SIZE, codes[i:i + BATCH_SIZE]


def _fetch_batch(prefixed_codes: list[str]) -> list[list[str]]:
    """请求一批代码，返回 [key, 字段...] 列表"""
    resp = SESSION.get(QUOTE_URL.format(",".join(prefixed_codes)), timeout=TIMEOUT)
    resp.encoding = "gbk"
    out = []
    for line in resp.text.strip().split("\n"):
        line = line.strip().rstrip(";")
        m = re.match(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        out.append([m.group(1)] + m.group(2).split("~"))
    return out


def _num(seq: list[str], idx: int, cast=float, default=0):
    try:
        v = seq[idx].strip()
        if v in ("", "-", "--"):
            return default
        return cast(float(v))
    except (IndexError, ValueError):
        return default


def _valid(seq: list[str]) -> bool:
    """有效性校验：正常行至少有 40 个字段，代码是 6 位纯数字"""
    return (
        len(seq) >= MIN_FIELDS
        and len(seq[I_CODE]) == 6
        and seq[I_CODE].isdigit()
        and seq[I_NAME].strip() != ""
    )


def fetch_etf_list(verbose: bool = True) -> pd.DataFrame:
    """扫描全市场，返回有效 ETF 列表"""
    records = []
    codes = all_candidate_codes()
    total = (len(codes) + BATCH_SIZE - 1) // BATCH_SIZE
    failed = 0

    for idx, batch in _iter_batches(codes):
        try:
            rows = _fetch_batch(batch)
        except Exception as e:  # noqa: BLE001
            failed += 1
            if verbose and failed <= 3:
                print(f"  [warn] 批次 {idx} 失败: {e}")
            continue

        for seq in rows:
            if not _valid(seq):
                continue
            code = seq[I_CODE]
            market = "SH" if seq[0].startswith("sh") else "SZ"
            records.append({
                "ts_code": f"{code}.{market}",
                "symbol": code,
                "market": market,
                "name": seq[I_NAME].strip(),
                "last_price": _num(seq, I_CLOSE),
                "change_pct": _num(seq, I_CHG_PCT),
            })

        if idx % 10 == 0:
            time.sleep(REQUEST_INTERVAL)

    df = pd.DataFrame(records).drop_duplicates(subset=["ts_code"])
    df = df.sort_values("ts_code").reset_index(drop=True)
    if verbose:
        print(f"[etf_list] 共 {len(df)} 只 ETF（扫描 {total} 批，失败 {failed} 批）")
    return df


def fetch_daily_snapshot(etf_list: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """抓取全部 ETF 的当日行情快照

    返回列：ts_code / trade_date / open / high / low / close /
            volume(手) / amount(万元) / change_pct / turnover_rate
    """
    codes = [
        ("sh" if m == "SH" else "sz") + s
        for s, m in zip(etf_list["symbol"], etf_list["market"])
    ]

    records = []
    failed = 0
    for idx, batch in _iter_batches(codes):
        try:
            rows = _fetch_batch(batch)
        except Exception as e:  # noqa: BLE001
            failed += 1
            if verbose and failed <= 3:
                print(f"  [warn] 快照批次 {idx} 失败: {e}")
            continue

        for seq in rows:
            if not _valid(seq):
                continue
            ts = seq[I_TIME]
            if len(ts) < 8 or not ts[:8].isdigit():
                continue
            close = _num(seq, I_CLOSE)
            if close <= 0:                       # 停牌 / 无效
                continue
            o, h, low = _num(seq, I_OPEN), _num(seq, I_HIGH), _num(seq, I_LOW)
            if o <= 0 or h <= 0 or low <= 0:     # 当日无成交（停牌），不生成 K 线
                continue
            code = seq[I_CODE]
            market = "SH" if seq[0].startswith("sh") else "SZ"
            records.append({
                "ts_code": f"{code}.{market}",
                "trade_date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}",
                "open": o,
                "high": h,
                "low": low,
                "close": close,
                "volume": _num(seq, I_VOL, int),
                "amount": _num(seq, I_AMOUNT),
                "change_pct": _num(seq, I_CHG_PCT),
                "turnover_rate": _num(seq, I_TURNOVER),
            })

        if idx % 10 == 0:
            time.sleep(REQUEST_INTERVAL)

    df = pd.DataFrame(records)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["ts_code"]).sort_values("ts_code").reset_index(drop=True)
    if verbose:
        print(f"[snapshot] {df['trade_date'].iloc[0]} 抓取到 {len(df)} 条行情"
              f"（失败 {failed} 批）")
    return df
