"""全局配置：仓库地址、缓存路径、下载源"""
from __future__ import annotations

import os
from pathlib import Path

# ── 数据托管仓库 ─────────────────────────────────────────────────────
# 也可用环境变量覆盖：export ETF_LITE_REPO=someone/etf-lite
DEFAULT_REPO = os.environ.get("ETF_LITE_REPO", "mingren1992/etf-lite")
DEFAULT_BRANCH = os.environ.get("ETF_LITE_BRANCH", "main")

# 下载源：jsDelivr CDN 优先（国内快、无限流），失败回退 GitHub raw
CDN_TEMPLATES = [
    "https://cdn.jsdelivr.net/gh/{repo}@{branch}/{path}",
    "https://raw.githubusercontent.com/{repo}/{branch}/{path}",
]

# 本地缓存目录
DEFAULT_CACHE_DIR = Path(
    os.environ.get("ETF_LITE_CACHE", Path.home() / ".cache" / "etf_lite")
)

# manifest 缓存有效期（秒）。过期后下次调用会检查是否有新数据
MANIFEST_TTL = int(os.environ.get("ETF_LITE_MANIFEST_TTL", 6 * 3600))

# 爬取时的请求参数
REQUEST_INTERVAL = 0.15
BATCH_SIZE = 50
TIMEOUT = 15
