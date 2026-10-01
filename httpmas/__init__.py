"""
httpmas - Thư viện HTTP client thuần Python.
Xây dựng hoàn toàn trên socket, không phụ thuộc
requests/urllib/aiohttp.

Cách import chuẩn (giống thư viện requests gốc):
    from httpmas import requests

Sử dụng đồng bộ:
    response = requests.get("https://example.com")

Sử dụng bất đồng bộ:
    from httpmas import asyncio

    async def main():
        r = await asyncio.requests.get("https://example.com")
        print(r.status_code, r.json())

    asyncio.run(main())

Xử lý lỗi (mặc định tự raise khi server trả 4xx/5xx):
    from httpmas import requests, HTTPError, RequestsError

    try:
        r = requests.get("https://example.com/api")
    except HTTPError as e:
        print(f"Server lỗi: {e.status_code}")
    except RequestsError as e:
        print(f"Lỗi mạng: {e}")

    # Disable auto-raise:
    r = requests.get(url, raise_on_error=False)
"""

from . import requests
from .requests import RequestManager
from .async_engine import AsyncRequestManager
from .response import Response
from .exceptions import RequestsError, HTTPError
from .session import Session, AsyncSession
from .cookies import CookieJar, Cookie, CookiePolicy
from .auth import AuthBase, BasicAuth, BearerAuth, DigestAuth, APIKeyAuth
from .headers import CaseInsensitiveHeaders
from .redirect import RedirectHandler

# Import asyncio module con (from httpmas import asyncio)
from . import asyncio

from . import version as _version_module

version = getattr(
    _version_module, "version", None
) or getattr(_version_module, "__version__", "0.0.0")

# Gắn Session vào module requests để tương thích API chuẩn
requests.Session = Session
requests.AsyncSession = AsyncSession
requests.CookieJar = CookieJar
requests.BasicAuth = BasicAuth
requests.BearerAuth = BearerAuth
requests.DigestAuth = DigestAuth
requests.APIKeyAuth = APIKeyAuth

__all__ = [
    # Core
    "requests",
    "RequestManager",
    "AsyncRequestManager",
    "Response",
    # Errors
    "RequestsError",
    "HTTPError",
    # Session
    "Session",
    "AsyncSession",
    # Cookie
    "CookieJar",
    "Cookie",
    "CookiePolicy",
    # Auth
    "AuthBase",
    "BasicAuth",
    "BearerAuth",
    "DigestAuth",
    "APIKeyAuth",
    # Headers
    "CaseInsensitiveHeaders",
    # Redirect
    "RedirectHandler",
    # Async module
    "asyncio",
    # Version
    "version",
]