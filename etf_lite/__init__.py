"""etf-lite：轻量 ETF 数据 SDK

作者侧：每日在 GitHub Actions 云端抓取数据并提交到仓库
用户侧：pip install 后一行代码读取，自动走 CDN 下载 + 本地缓存
"""
from .api import ETFLite, ETFLiteError

__version__ = "0.1.0"
__all__ = ["ETFLite", "ETFLiteError", "__version__"]
