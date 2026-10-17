"""全局配置：仓库地址、缓存路径、下载源"""
from __future__ import annotations

import os
from pathlib import Path

# ── 数据托管仓库 ─────────────────────────────────────────────────────
# 也可用环境变量覆盖：export ETF_LITE_REPO=someone/etf-lite
DEFAULT_REPO = os.environ.get("ETF_LITE_REPO", "mingren1992/etf-lite")
DEFAULT_BRANCH = os.environ.get("ETF_LITE_BRANCH", "main")

# 下载源，按顺序尝试，任一成功即用。
# 说明：raw.githubusercontent.com 在国内多数网络不可达，所以放在最后兜底；
#      jsDelivr 对 @main 分支有约 12 小时缓存，日更场景下可能滞后，故排在实时代理之后。
CDN_TEMPLATES = [
    "https://ghfast.top/https://raw.githubusercontent.com/{repo}/{branch}/{path}",
    "https://gh-proxy.com/https://raw.githubusercontent.com/{repo}/{branch}/{path}",
    "https://ghproxy.net/https://raw.githubusercontent.com/{repo}/{branch}/{path}",
    "https://cdn.jsdelivr.net/gh/{repo}@{branch}/{path}",
    "https://raw.githubusercontent.com/{repo}/{branch}/{path}",
]

# 可用环境变量自行指定下载源（多源用逗号分隔），例如：
#   export ETF_LITE_CDN="https://my-mirror.com/{repo}/{branch}/{path}"
_env_cdn = os.environ.get("ETF_LITE_CDN", "").strip()
if _env_cdn:
    CDN_TEMPLATES = [t.strip() for t in _env_cdn.split(",") if t.strip()]

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
