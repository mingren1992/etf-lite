"""作者侧脚本公用工具：仓库路径、manifest 读写"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DAILY_DIR = DATA_DIR / "etf_daily"
MANIFEST_PATH = DATA_DIR / "manifest.json"

sys.path.insert(0, str(ROOT))

CST = timezone(timedelta(hours=8))


def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "updated_at": None, "latest_trade_date": None, "datasets": {}}


def save_manifest(m: dict) -> None:
    m["updated_at"] = now_str()
    MANIFEST_PATH.write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def rel(path: Path) -> str:
    return str(Path(path).resolve().relative_to(ROOT.resolve()))


def file_meta(path: Path) -> dict:
    return {"bytes": path.stat().st_size}


def rebuild_manifest(m: dict) -> dict:
    """扫描 data/ 目录，重建 manifest 的 datasets 部分"""
    import pandas as pd

    datasets: dict = {}

    list_path = DATA_DIR / "etf_list.parquet"
    if list_path.exists():
        df = pd.read_parquet(list_path)
        datasets["etf_list"] = {
            "path": rel(list_path),
            "rows": int(len(df)),
            "columns": list(df.columns),
            **file_meta(list_path),
        }

    files, total = [], 0
    for p in sorted(DAILY_DIR.glob("*.parquet")):
        year = int(p.stem)
        try:
            rows = len(pd.read_parquet(p, columns=["ts_code"]))
        except Exception:  # noqa: BLE001
            rows = 0
        total += rows
        files.append({"year": year, "path": rel(p), "rows": rows, **file_meta(p)})

    if files:
        datasets["etf_daily"] = {
            "rows": total,
            "columns": ["ts_code", "trade_date", "open", "high", "low", "close",
                        "volume", "amount", "change_pct", "turnover_rate"],
            "files": files,
        }

    m["datasets"] = datasets
    return m
