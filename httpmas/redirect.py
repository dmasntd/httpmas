"""
Redirect handling cho httpmas.
RFC 7231 compliant.
"""
from typing import List, Optional, Tuple
from urllib.parse import urlparse, urljoin

from .exceptions import RequestsError


class RedirectHandler:
    """Xử lý chuỗi redirect 3xx."""
    __slots__ = ("max_redirects", "allow_redirects", "_history")

    REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        max_redirects: int = 30,
        allow_redirects: bool = True,
    ) -> None:
        self.max_redirects = max_redirects
        self.allow_redirects = allow_redirects
        self._history: List[str] = []

    @property
    def history(self) -> List[str]:
        return list(self._history)

    def should_redirect(self, status_code: int) -> bool:
        return self.allow_redirects and status_code in self.REDIRECT_CODES

    def check_limit(self) -> None:
        if len(self._history) >= self.max_redirects:
            raise RequestsError(
                f"Quá nhiều redirect (>{self.max_redirects})",
                print_error=False,
            )

    def check_loop(self, url: str) -> None:
        if url in self._history:
            raise RequestsError(
                f"Redirect loop phát hiện tại {url}",
                print_error=False,
            )

    def record(self, url: str) -> None:
        self._history.append(url)

    def resolve(
        self, response, method: str, current_url: str
    ) -> Tuple[str, str, bool]:
        """
        Trả về (new_url, new_method, should_strip_body).

        Logic theo RFC 7231:
        - 301/302: POST → GET (strip body)
        - 303: luôn → GET (strip body)
        - 307/308: giữ nguyên method + body
        """
        location = ""
        if hasattr(response, "headers"):
            location = response.headers.get("location", "")

        if not location:
            return current_url, method, False

        new_url = self._resolve_relative_url(location, current_url)

        status = response.status_code if hasattr(response, "status_code") else 0

        if status in (301, 302, 303):
            if method.upper() == "POST" or status == 303:
                return new_url, "GET", True
            return new_url, method, False

        return new_url, method, False

    @staticmethod
    def _resolve_relative_url(location: str, base_url: str) -> str:
        if location.startswith("http://") or location.startswith("https://"):
            return location

        parsed_base = urlparse(base_url)

        if location.startswith("//"):
            return f"{parsed_base.scheme}:{location}"

        return urljoin(base_url, location)