"""数据源直连：腾讯财经行情接口

不依赖 AkShare，直接 requests 请求交易所/行情源。
- ETF 列表：qt.gtimg.cn 批量行情快照反推代码-名称对应
- 日行情快照：qt.gtimg.cn 批量快照（当日 OHLCV + 成交额）
"""
from __future__ import annotations

import os
import re
import socket
import time

import pandas as pd
import requests

from .config import BATCH_SIZE, REQUEST_INTERVAL, TIMEOUT

# 清掉系统代理 + 强制 IPv4，避免 Clash 之类干扰
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


def all_candidate_codes() -> list[str]:
    """生成候选代码池（沪市 510-588 / 深市 159）"""
    codes = []
    for p in SH_PREFIXES:
        codes += [f"sh{p}{i:03d}" for i in range(1000)]
    for p in SZ_PREFIXES:
        codes += [f"sz{p}{i:03d}" for i in range(1000)]
    return codes


def _fetch_batch(prefixed_codes: list[str]) -> list[list[str]]:
    """请求一批代码，返回按 ~ 拆分后的字段列表"""
    url = QUOTE_URL.format(",".join(prefixed_codes))
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.encoding = "gbk"
    out = []
    for line in resp.text.strip().split("\n"):
        line = line.strip().rstrip(";")
        m = re.match(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        out.append([m.group(1)] + m.group(2).split("~"))
    return out


def _iter_batches(codes: list[str]):
    for i in range(0, len(codes), BATCH_SIZE):
        yield i // BATCH_SIZE, codes[i:i + BATCH_SIZE]


def _num(seq: list[str], idx: int, cast=float, default=None):
    try:
        v = seq[idx].strip()
        if v in ("", "-", "0.00"):
            return default if default is not None else (0 if cast is int else 0.0)
        return cast(float(v))
    except Exception:
        return default if default is not None else (0 if cast is int else 0.0)


def fetch_etf_list(verbose: bool = True) -> pd.DataFrame:
    """扫描全市场，返回有效 ETF 列表（代码/名称/市场/最新价）"""
    records = []
    codes = all_candidate_codes()
    total = (len(codes) + BATCH_SIZE - 1) // BATCH_SIZE

    for idx, batch in _iter_batches(codes):
        try:
            rows = _fetch_batch(batch)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  [warn] 批次 {idx} 失败: {e}")
            continue

        for seq in rows:
            key, name, code = seq[0], seq[1], seq[2]
            if len(code) != 6 or not name:
                continue
            market = "SH" if key.startswith("sh") else "SZ"
            records.append({
                "ts_code": f"{code}.{market}",
                "symbol": code,
                "market": market,
                "name": name.strip(),
                "last_price": _num(seq, 3),
            })

        if idx % 10 == 0:
            time.sleep(REQUEST_INTERVAL)

    df = pd.DataFrame(records).drop_duplicates(subset=["ts_code"])
    df = df.sort_values("ts_code").reset_index(drop=True)
    if verbose:
        print(f"[etf_list] 共 {len(df)} 只 ETF（扫描 {total} 批）")
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
    name_map = dict(zip(etf_list["symbol"], etf_list["name"]))

    records = []
    bad_batches = 0
    for idx, batch in _iter_batches(codes):
        try:
            rows = _fetch_batch(batch)
        except Exception as e:  # noqa: BLE001
            bad_batches += 1
            if verbose and bad_batches <= 3:
                print(f"  [warn] 快照批次 {idx} 失败: {e}")
            continue

        for seq in rows:
            code = seq[2]
            if len(code) != 6 or len(seq) < 40:
                continue
            ts = seq[30]                      # 20261016161459
            if len(ts) < 8:
                continue
            close = _num(seq, 3)
            if close <= 0:                    # 停牌/无效
                continue
            volume_hand = _num(seq, 36, int)  # 成交量(手)
            amount_wan = _num(seq, 37)        # 成交额(万元)
            records.append({
                "ts_code": f"{code}.{'SH' if seq[0].startswith('sh') else 'SZ'}",
                "trade_date": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}",
                "open": _num(seq, 5),
                "high": _num(seq, 33),
                "low": _num(seq, 34),
                "close": close,
                "volume": volume_hand,
                "amount": amount_wan,
                "change_pct": _num(seq, 32),
                "turnover_rate": _num(seq, 38),
            })

        if idx % 10 == 0:
            time.sleep(REQUEST_INTERVAL)

    df = pd.DataFrame(records)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["ts_code"]).sort_values("ts_code").reset_index(drop=True)
    if verbose:
        print(f"[snapshot] {df['trade_date'].iloc[0]} 抓取到 {len(df)} 条行情")
    return df
