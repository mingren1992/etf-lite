"""用户侧 SDK：pip install 后一行代码拿到数据

用法：
    import etf_lite

    etf = etf_lite.ETFLite()              # 默认连作者托管的数据仓库
    etf = etf_lite.ETFLite("你的名字/etf-lite")   # 也可以连自己的

    etf.get_etf_list()                    # 全市场 ETF 列表
    etf.get_etf_daily("510300")           # 单只 ETF 全部历史行情
    etf.get_etf_daily(start="2024-01-01") # 全市场自某日起的行情
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

from .config import (
    CDN_TEMPLATES,
    DEFAULT_BRANCH,
    DEFAULT_CACHE_DIR,
    DEFAULT_REPO,
    MANIFEST_TTL,
    TIMEOUT,
)

MANIFEST_NAME = "data/manifest.json"


class ETFLiteError(RuntimeError):
    """SDK 抛出的业务异常"""


class ETFLite:
    def __init__(
        self,
        repo: str = DEFAULT_REPO,
        cache_dir: str | Path | None = None,
        branch: str = DEFAULT_BRANCH,
        verbose: bool = False,
        local_dir: str | Path | None = None,
    ):
        self.repo = repo
        self.branch = branch
        self.verbose = verbose
        # 本地模式：直接读本地仓库目录（作者自己调试 / 离线使用）
        self.local_dir = Path(local_dir).expanduser().resolve() if local_dir else None
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.file_dir = self.cache_dir / "files"
        self.file_dir.mkdir(parents=True, exist_ok=True)
        self._manifest: dict | None = None
        self._session = requests.Session()
        self._session.trust_env = False

    # ── 底层下载 ────────────────────────────────────────────────
    def _urls(self, path: str) -> list[str]:
        return [t.format(repo=self.repo, branch=self.branch, path=path) for t in CDN_TEMPLATES]

    def _download(self, path: str, force: bool = False) -> Path:
        """下载仓库内某个文件到本地缓存，返回本地路径"""
        if self.local_dir is not None:
            p = self.local_dir / path
            if not p.exists():
                raise ETFLiteError(f"本地仓库缺少文件：{p}")
            return p

        target = self.file_dir / path
        if target.exists() and not force:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)

        last_err = None
        for url in self._urls(path):
            try:
                if self.verbose:
                    print(f"[etf-lite] 下载 {url}")
                resp = self._session.get(url, timeout=TIMEOUT)
                if resp.status_code == 200 and resp.content:
                    tmp = target.with_suffix(target.suffix + ".part")
                    tmp.write_bytes(resp.content)
                    tmp.replace(target)
                    return target
                last_err = f"HTTP {resp.status_code}"
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
        raise ETFLiteError(
            f"下载失败：{path}（{last_err}）。"
            f"请检查仓库名是否正确：{self.repo}，或网络是否能访问 GitHub。"
        )

    # ── manifest ────────────────────────────────────────────────
    def refresh(self, force: bool = True) -> dict:
        """更新数据索引（manifest.json）"""
        if self.local_dir is not None:
            path = self._download(MANIFEST_NAME, force=True)
            self._manifest = json.loads(path.read_text(encoding="utf-8"))
            return self._manifest

        local = self.cache_dir / "manifest.json"
        if not force and local.exists() and (time.time() - local.stat().st_mtime) < MANIFEST_TTL:
            self._manifest = json.loads(local.read_text(encoding="utf-8"))
            return self._manifest

        path = self._download(MANIFEST_NAME, force=True)
        self._manifest = json.loads(path.read_text(encoding="utf-8"))
        local.write_text(json.dumps(self._manifest, ensure_ascii=False), encoding="utf-8")
        return self._manifest

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            local = self.cache_dir / "manifest.json"
            if local.exists() and (time.time() - local.stat().st_mtime) < MANIFEST_TTL:
                self._manifest = json.loads(local.read_text(encoding="utf-8"))
            else:
                self.refresh(force=True)
        return self._manifest

    def info(self) -> dict:
        """数据概况：最新交易日、文件数、总行数"""
        m = self.manifest
        daily = m["datasets"].get("etf_daily", {})
        return {
            "repo": self.repo,
            "updated_at": m.get("updated_at"),
            "latest_trade_date": m.get("latest_trade_date"),
            "etf_count": m["datasets"].get("etf_list", {}).get("rows", 0),
            "daily_years": [f["year"] for f in daily.get("files", [])],
            "daily_rows": daily.get("rows", 0),
            "cache_dir": str(self.cache_dir),
        }

    # ── 数据读取 ────────────────────────────────────────────────
    def get_etf_list(self, force: bool = False) -> pd.DataFrame:
        """全市场 ETF 列表"""
        meta = self.manifest["datasets"]["etf_list"]
        path = self._download(meta["path"], force=force)
        return pd.read_parquet(path)

    def _daily_files(self, years: set[int] | None) -> list[dict]:
        files = self.manifest["datasets"].get("etf_daily", {}).get("files", [])
        if years is None:
            return files
        return [f for f in files if f["year"] in years]

    def get_etf_daily(
        self,
        code: str | None = None,
        start: str | None = None,
        end: str | None = None,
        force: bool = False,
    ) -> pd.DataFrame:
        """ETF 日行情

        Args:
            code: 6 位代码（如 '510300'），None = 全市场
            start / end: 'YYYY-MM-DD' 或 'YYYYMMDD'
        """
        start = _norm_date(start)
        end = _norm_date(end)

        years = None
        if start or end:
            y0 = int((start or "1990-01-01")[:4])
            y1 = int((end or "2099-12-31")[:4])
            years = set(range(y0, y1 + 1))

        frames = []
        for f in self._daily_files(years):
            path = self._download(f["path"], force=force)
            frames.append(pd.read_parquet(path))

        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)

        if code:
            df = df[df["ts_code"] == _norm_code(code)]
        df = df.sort_values(["trade_date", "ts_code"])
        if start:
            df = df[df["trade_date"] >= start]
        if end:
            df = df[df["trade_date"] <= end]
        return df.reset_index(drop=True)

    def clear_cache(self) -> None:
        """清空本地缓存，下次调用会重新下载"""
        for p in sorted(self.file_dir.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        (self.cache_dir / "manifest.json").unlink(missing_ok=True)
        self._manifest = None


def _norm_date(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip().replace("/", "-")
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _norm_code(code: str) -> str:
    code = code.strip().upper()
    if "." in code or code.startswith(("SH", "SZ")):
        if code.startswith("SH"):
            return f"{code[2:]}.SH"
        if code.startswith("SZ"):
            return f"{code[2:]}.SZ"
        return code
    return f"{code}.{'SH' if code.startswith('5') else 'SZ'}"
