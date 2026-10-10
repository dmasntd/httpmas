"""
Authentication handlers cho httpmas.
Hỗ trợ: Basic, Bearer, Digest (RFC 7616), API Key.
"""
import base64
import hashlib
import os
import re
from typing import Dict, Optional


class AuthBase:
    """Base class cho mọi auth handler."""

    def apply(self, method: str, url: str, headers: Dict[str, str]) -> Dict[str, str]:
        """Áp dụng auth vào headers. Trả về headers đã sửa."""
        raise NotImplementedError

    def handle_401(
        self,
        response,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: Optional[bytes] = None,
    ) -> Optional[Dict[str, str]]:
        """Xử lý 401 challenge. Trả về headers mới để retry, hoặc None."""
        return None


class BasicAuth(AuthBase):
    """HTTP Basic Authentication.
    Header: Authorization: Basic base64(user:pass)
    """
    __slots__ = ("_header_value",)

    def __init__(self, username: str, password: str) -> None:
        credentials = f"{username}:{password}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("ascii")
        self._header_value = f"Basic {encoded}"

    def apply(self, method: str, url: str, headers: Dict[str, str]) -> Dict[str, str]:
        headers["Authorization"] = self._header_value
        return headers


class BearerAuth(AuthBase):
    """Bearer Token Authentication.
    Header: Authorization: Bearer <token>
    """
    __slots__ = ("_header_value",)

    def __init__(self, token: str) -> None:
        self._header_value = f"Bearer {token}"

    def apply(self, method: str, url: str, headers: Dict[str, str]) -> Dict[str, str]:
        headers["Authorization"] = self._header_value
        return headers


class APIKeyAuth(AuthBase):
    """API Key qua header hoặc query param."""
    __slots__ = ("_key_name", "_key_value", "_location")

    def __init__(
        self,
        key_name: str,
        key_value: str,
        location: str = "header",
    ) -> None:
        self._key_name = key_name
        self._key_value = key_value
        self._location = location.lower()

    def apply(self, method: str, url: str, headers: Dict[str, str]) -> Dict[str, str]:
        if self._location == "header":
            headers[self._key_name] = self._key_value
        return headers

    def apply_to_url(self, url: str) -> str:
        """Thêm API key vào query string (cho location='query')."""
        if self._location != "query":
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{self._key_name}={self._key_value}"


class DigestAuth(AuthBase):
    """HTTP Digest Authentication (RFC 7616).
    Challenge-response:
    1. Gửi request không auth → server trả 401 + WWW-Authenticate
    2. Tính response hash → gửi lại với Authorization header
    """
    __slots__ = ("username", "password", "_nonce_count")

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self._nonce_count = 0

    def apply(self, method: str, url: str, headers: Dict[str, str]) -> Dict[str, str]:
        return headers

    def handle_401(
        self,
        response,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: Optional[bytes] = None,
    ) -> Optional[Dict[str, str]]:
        www_auth = ""
        if hasattr(response, "headers"):
            www_auth = response.headers.get("www-authenticate", "")

        if "digest" not in www_auth.lower():
            return None

        params = self._parse_challenge(www_auth)
        if not params:
            return None

        self._nonce_count += 1
        auth_header = self._build_auth_header(params, method, url)
        if auth_header:
            headers = dict(headers)
            headers["Authorization"] = auth_header
            return headers
        return None

    def _parse_challenge(self, www_auth: str) -> Optional[Dict[str, str]]:
        content = www_auth
        idx = www_auth.lower().find("digest")
        if idx >= 0:
            content = www_auth[idx + 6:]

        params = {}
        pattern = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([\w./:-]+))')
        for match in pattern.finditer(content):
            key = match.group(1).lower()
            value = match.group(2) if match.group(2) is not None else match.group(3)
            params[key] = value

        if "realm" not in params or "nonce" not in params:
            return None
        return params

    def _build_auth_header(
        self, params: Dict[str, str], method: str, url: str
    ) -> Optional[str]:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        uri = parsed.path
        if parsed.query:
            uri += "?" + parsed.query

        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        opaque = params.get("opaque", "")
        qop = params.get("qop", "")
        algorithm = params.get("algorithm", "MD5").upper()

        if algorithm == "MD5":
            hash_func = hashlib.md5
        elif algorithm == "SHA-256":
            hash_func = hashlib.sha256
        elif algorithm == "SHA-512":
            hash_func = hashlib.sha512
        else:
            hash_func = hashlib.md5

        ha1 = hash_func(
            f"{self.username}:{realm}:{self.password}".encode()
        ).hexdigest()

        ha2 = hash_func(f"{method}:{uri}".encode()).hexdigest()

        nc = f"{self._nonce_count:08x}"
        cnonce = os.urandom(8).hex()

        if qop and ("auth" in qop):
            response = hash_func(
                f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}".encode()
            ).hexdigest()
        else:
            response = hash_func(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()

        parts = [
            f'username="{self.username}"',
            f'realm="{realm}"',
            f'nonce="{nonce}"',
            f'uri="{uri}"',
            f'response="{response}"',
            f'algorithm={algorithm}',
        ]

        if opaque:
            parts.append(f'opaque="{opaque}"')
        if qop and "auth" in qop:
            parts.append(f'qop=auth')
            parts.append(f'nc={nc}')
            parts.append(f'cnonce="{cnonce}"')

        return "Digest " + ", ".join(parts)