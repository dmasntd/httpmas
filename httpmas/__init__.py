"""
httpmas - Thư viện HTTP client thuần Python.
Xây dựng hoàn toàn trên socket, không phụ thuộc
requests/urllib/aiohttp.

Cách import chuẩn (giống thư viện requests gốc):
    from httpmas import requests

Sử dụng đồng bộ:
    response = requests.get("https://example.com")

Sử dụng bất đồng bộ:
    response = await requests.async_get("https://example.com")
"""

from . import requests
from .requests import RequestManager
from .async_engine import AsyncRequestManager
from .response import Response
from .exceptions import RequestsError
from . import version as _version_module

# Tương thích cả hai cách đặt tên trong version.py
__version__ = getattr(
    _version_module, "__version__", None
) or getattr(_version_module, "version", "0.0.0")
version = __version__


__all__ = [
    "requests",
    "RequestManager",
    "AsyncRequestManager",
    "Response",
    "RequestsError",
    "__version__",
    "version",
]