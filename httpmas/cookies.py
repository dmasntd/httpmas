"""
Cookie management cho httpmas.
RFC 6265 compliant (simplified).
Thread-safe, hỗ trợ domain/path matching, expiry.
"""
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


class Cookie:
    """Một cookie đơn lẻ."""
    __slots__ = (
        "name", "value", "domain", "path",
        "expires", "secure", "http_only",
        "same_site", "created_at",
    )

    def __init__(
        self,
        name: str,
        value: str,
        domain: str = "",
        path: str = "/",
        expires: Optional[float] = None,
        secure: bool = False,
        http_only: bool = False,
        same_site: str = "",
    ) -> None:
        self.name = name
        self.value = value
        self.domain = domain.lower().lstrip(".")
        self.path = path
        self.expires = expires
        self.secure = secure
        self.http_only = http_only
        self.same_site = same_site.lower()
        self.created_at = time.monotonic()

    @property
    def is_expired(self) -> bool:
        if self.expires is None:
            return False
        return time.time() > self.expires

    def matches_domain(self, request_host: str) -> bool:
        request_host = request_host.lower()
        if not self.domain:
            return request_host == self.domain
        if request_host == self.domain:
            return True
        return request_host.endswith("." + self.domain)

    def matches_path(self, request_path: str) -> bool:
        if not self.path or self.path == "/":
            return True
        if request_path == self.path:
            return True
        if request_path.startswith(self.path):
            if self.path.endswith("/"):
                return True
            return request_path[len(self.path):].startswith("/")
        return False

    def matches(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        path = parsed.path or "/"
        scheme = parsed.scheme.lower()

        if not self.matches_domain(host):
            return False
        if not self.matches_path(path):
            return False
        if self.secure and scheme != "https":
            return False
        if self.is_expired:
            return False
        return True

    def __repr__(self) -> str:
        return f"<Cookie {self.name}={self.value[:20]} domain={self.domain}>"


class CookiePolicy:
    """Quy tắc accept/reject cookies."""
    __slots__ = ("accept_all", "blocked_domains", "max_cookies")

    def __init__(
        self,
        accept_all: bool = True,
        blocked_domains: Optional[List[str]] = None,
        max_cookies: int = 500,
    ) -> None:
        self.accept_all = accept_all
        self.blocked_domains = set(
            d.lower() for d in (blocked_domains or [])
        )
        self.max_cookies = max_cookies

    def should_accept(self, cookie: Cookie, request_url: str) -> bool:
        if not self.accept_all:
            return False
        parsed = urlparse(request_url)
        request_host = (parsed.hostname or "").lower()
        for blocked in self.blocked_domains:
            if request_host == blocked or request_host.endswith("." + blocked):
                return False
        return True


class CookieJar:
    """Lưu trữ và quản lý cookies theo domain/path.
    Thread-safe. Tự động xoá cookie hết hạn.
    """
    __slots__ = ("_cookies", "_lock", "_policy")

    def __init__(self, policy: Optional[CookiePolicy] = None) -> None:
        self._cookies: List[Cookie] = []
        self._lock = threading.RLock()
        self._policy = policy or CookiePolicy()

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired()
            return len(self._cookies)

    def __iter__(self):
        with self._lock:
            self._purge_expired()
            return iter(list(self._cookies))

    def _purge_expired(self) -> None:
        self._cookies = [c for c in self._cookies if not c.is_expired]
        if len(self._cookies) > self._policy.max_cookies:
            self._cookies = self._cookies[-self._policy.max_cookies:]

    def set_cookie(self, cookie: Cookie) -> None:
        with self._lock:
            self._cookies = [
                c for c in self._cookies
                if not (
                    c.name == cookie.name
                    and c.domain == cookie.domain
                    and c.path == cookie.path
                )
            ]
            self._cookies.append(cookie)
            self._purge_expired()

    def set_from_header(self, header_value: str, url: str) -> None:
        """Parse Set-Cookie header và lưu cookie."""
        parsed = urlparse(url)
        request_host = (parsed.hostname or "").lower()
        request_path = parsed.path or "/"

        cookie = self._parse_set_cookie(header_value, request_host, request_path)
        if cookie is None:
            return

        if not self._policy.should_accept(cookie, url):
            return

        self.set_cookie(cookie)

    def _parse_set_cookie(
        self, header: str, default_domain: str, default_path: str
    ) -> Optional[Cookie]:
        parts = header.split(";")
        if not parts:
            return None

        name_value = parts[0].strip()
        eq_idx = name_value.find("=")
        if eq_idx < 0:
            return None

        name = name_value[:eq_idx].strip()
        value = name_value[eq_idx + 1:].strip()

        if not name:
            return None

        domain = default_domain
        path = default_path
        expires = None
        secure = False
        http_only = False
        same_site = ""
        max_age = None

        for part in parts[1:]:
            part = part.strip()
            lower = part.lower()

            if lower.startswith("domain="):
                domain = part[7:].strip().lstrip(".").lower()
            elif lower.startswith("path="):
                path = part[5:].strip() or "/"
            elif lower.startswith("max-age="):
                try:
                    max_age = int(part[8:].strip())
                except ValueError:
                    pass
            elif lower.startswith("expires="):
                expires = self._parse_expires(part[8:].strip())
            elif lower == "secure":
                secure = True
            elif lower == "httponly":
                http_only = True
            elif lower.startswith("samesite="):
                same_site = part[9:].strip()

        if max_age is not None:
            if max_age <= 0:
                expires = 0.0
            else:
                expires = time.time() + max_age

        return Cookie(
            name=name,
            value=value,
            domain=domain,
            path=path,
            expires=expires,
            secure=secure,
            http_only=http_only,
            same_site=same_site,
        )

    @staticmethod
    def _parse_expires(date_str: str) -> Optional[float]:
        """Parse expires date. Hỗ trợ format phổ biến."""
        import email.utils
        try:
            parsed = email.utils.parsedate_to_datetime(date_str)
            return parsed.timestamp()
        except (ValueError, TypeError):
            pass
        formats = [
            "%a, %d %b %Y %H:%M:%S GMT",
            "%a, %d-%b-%Y %H:%M:%S GMT",
            "%a %b %d %H:%M:%S %Y",
        ]
        for fmt in formats:
            try:
                import datetime
                dt = datetime.datetime.strptime(date_str, fmt)
                return dt.timestamp()
            except ValueError:
                continue
        return None

    def get_cookies_for_url(self, url: str) -> List[Cookie]:
        with self._lock:
            self._purge_expired()
            matched = [c for c in self._cookies if c.matches(url)]
            matched.sort(key=lambda c: len(c.path), reverse=True)
            return matched

    def get_header_for_url(self, url: str) -> Optional[str]:
        cookies = self.get_cookies_for_url(url)
        if not cookies:
            return None
        return "; ".join(f"{c.name}={c.value}" for c in cookies)

    def clear(
        self,
        domain: Optional[str] = None,
        path: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._cookies = [
                c for c in self._cookies
                if not (
                    (domain is None or c.domain == domain.lower())
                    and (path is None or c.path == path)
                    and (name is None or c.name == name)
                )
            ]

    def clear_all(self) -> None:
        with self._lock:
            self._cookies.clear()