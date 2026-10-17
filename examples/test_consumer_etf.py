#!/usr/bin/env python3
"""体验测试：从 etf-lite 里查「消费类」ETF 有多少、都有哪些、谁最活跃

用法：
    python examples/test_consumer_etf.py
    python examples/test_consumer_etf.py 科技      # 换个关键词
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import etf_lite

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "消费"


def main() -> int:
    etf = etf_lite.ETFLite()
    info = etf.info()
    print("=" * 62)
    print(f"etf-lite 数据概况：{info['etf_count']} 只 ETF，"
          f"{info['daily_rows']:,} 行日线，最新交易日 {info['latest_trade_date']}")
    print("=" * 62)

    # 1. 全市场列表里按名字搜
    lst = etf.get_etf_list()
    hit = lst[lst["name"].str.contains(KEYWORD, na=False)].copy()
    print(f"\n【{KEYWORD}】相关的 ETF 共 {len(hit)} 只：\n")
    print(hit[["ts_code", "name", "last_price", "change_pct"]]
          .to_string(index=False))

    if hit.empty:
        print("(没找到，试试别的关键词，比如：科技 / 医药 / 军工 / 券商)")
        return 0

    # 2. 看这些 ETF 最近一个交易日谁成交最活跃
    latest = info["latest_trade_date"]
    daily = etf.get_etf_daily(start=latest)
    sub = daily[daily["ts_code"].isin(set(hit["ts_code"]))].copy()
    sub["成交额(亿)"] = sub["amount"] / 10000
    sub["close"] = sub["close"].round(3)
    print(f"\n它们在 {latest} 的表现（按成交额排序）：\n")
    top = sub.sort_values("amount", ascending=False).head(15)
    merged = top.merge(hit[["ts_code", "name"]], on="ts_code", how="left")
    print(merged[["ts_code", "name", "close", "change_pct",
                  "volume", "成交额(亿)"]].to_string(index=False))

    # 3. 抽成交额最大的一只，看近一年走势摘要
    champion = merged.iloc[0]["ts_code"]
    hist = etf.get_etf_daily(champion, start="2026-01-01")
    print(f"\n【走势摘要】{merged.iloc[0]['name']}（{champion}）2026 年以来：")
    print(f"  交易日 {len(hist)} 个 | 年初至今 {hist['close'].iloc[0]} -> "
          f"{hist['close'].iloc[-1]} 元，涨跌 "
          f"{(hist['close'].iloc[-1] / hist['close'].iloc[0] - 1) * 100:+.1f}% | "
          f"年内最高 {hist['high'].max()} / 最低 {hist['low'].min()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
