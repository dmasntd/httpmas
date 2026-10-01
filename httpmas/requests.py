"""
RequestManager sync - Optimistic Stale Retry + Adaptive Backoff.
Nâng cấp: raise_on_error=True mặc định, tự ném HTTPError khi server trả 4xx/5xx.
"""
import gzip
import json as _json
import socket
import ssl as _ssl
import threading
import time
import zlib
from typing import Any, Dict, Optional

from .exceptions import RequestsError, HTTPError
from .socket_engine import SocketEngine, NETWORK_STATS
from .http_parser import HTTPParser
from .response import Response

try:
    import brotli
    _BROTLI_AVAILABLE = True
except ImportError:
    _BROTLI_AVAILABLE = False

_ACCEPT_ENCODING = "gzip, deflate"
if _BROTLI_AVAILABLE:
    _ACCEPT_ENCODING += ", br"


class _StaleConnectionError(Exception):
    """Pooled connection đã bị server/NAT đóng. Retry ngay, 0ms delay."""
    __slots__ = ()


class _URLParser:
    __slots__ = ("raw", "scheme", "hostname", "port", "path", "query")

    _CACHE: Dict[str, "_URLParser"] = {}
    _CACHE_MAX = 256
    _CACHE_LOCK = threading.Lock()

    def __init__(self, url: str) -> None:
        self.raw = url.strip()
        self.scheme = ""
        self.hostname = ""
        self.port: Optional[int] = None
        self.path = "/"
        self.query = ""
        self._parse()

    @classmethod
    def parse(cls, url: str) -> "_URLParser":
        url_stripped = url.strip()
        cached = cls._CACHE.get(url_stripped)
        if cached is not None:
            return cached
        with cls._CACHE_LOCK:
            cached = cls._CACHE.get(url_stripped)
            if cached is not None:
                return cached
            obj = cls(url_stripped)
            if len(cls._CACHE) >= cls._CACHE_MAX:
                cls._CACHE.clear()
            cls._CACHE[url_stripped] = obj
            return obj

    def _parse(self) -> None:
        rest = self.raw
        if "://" in rest:
            self.scheme, rest = rest.split("://", 1)
            self.scheme = self.scheme.lower()
        else:
            raise RequestsError(f"URL thiếu scheme: {self.raw}", print_error=False)
        if self.scheme not in ("http", "https"):
            raise RequestsError(f"Scheme không hỗ trợ: {self.scheme}", print_error=False)
        if "#" in rest:
            rest = rest.split("#", 1)[0]
        if "?" in rest:
            rest, self.query = rest.split("?", 1)
        if "/" in rest:
            authority, path_part = rest.split("/", 1)
            self.path = "/" + path_part
        else:
            authority = rest
            self.path = "/"
        if "@" in authority:
            authority = authority.rsplit("@", 1)[1]
        if authority.startswith("["):
            bracket_end = authority.find("]")
            if bracket_end == -1:
                raise RequestsError(f"URL IPv6 không hợp lệ: {self.raw}", print_error=False)
            self.hostname = authority[1:bracket_end]
            after = authority[bracket_end + 1:]
            if after.startswith(":"):
                try:
                    self.port = int(after[1:])
                except ValueError:
                    raise RequestsError(f"Port không hợp lệ: {self.raw}", print_error=False)
        elif ":" in authority:
            host_part, port_part = authority.rsplit(":", 1)
            self.hostname = host_part
            if port_part:
                try:
                    self.port = int(port_part)
                except ValueError:
                    raise RequestsError(f"Port không hợp lệ: {self.raw}", print_error=False)
        else:
            self.hostname = authority
        if not self.hostname:
            raise RequestsError(f"URL không có hostname: {self.raw}", print_error=False)

    @property
    def effective_port(self) -> int:
        if self.port is not None:
            return self.port
        return 443 if self.scheme == "https" else 80

    @property
    def use_tls(self) -> bool:
        return self.scheme == "https"

    @property
    def full_path(self) -> str:
        if self.query:
            return f"{self.path}?{self.query}"
        return self.path


class _FormEncoder:
    __slots__ = ()

    _SAFE = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-.~"
    )

    @classmethod
    def encode_value(cls, value: str) -> str:
        result = []
        for byte in value.encode("utf-8"):
            char = chr(byte)
            if char in cls._SAFE:
                result.append(char)
            elif char == " ":
                result.append("+")
            else:
                result.append(f"%{byte:02X}")
        return "".join(result)

    @classmethod
    def urlencode(cls, data: Dict[str, Any]) -> str:
        pairs = []
        for key, value in data.items():
            pairs.append(
                f"{cls.encode_value(str(key))}={cls.encode_value(str(value))}"
            )
        return "&".join(pairs)


def _decompress_body(headers: Dict[str, str], body: bytes) -> bytes:
    if not body:
        return body
    encoding = headers.get("content-encoding", "").lower()
    if "gzip" in encoding or "x-gzip" in encoding:
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    if "deflate" in encoding:
        try:
            return zlib.decompress(body)
        except zlib.error:
            try:
                return zlib.decompress(body, -zlib.MAX_WBITS)
            except zlib.error:
                return body
    if "br" in encoding and _BROTLI_AVAILABLE:
        try:
            return brotli.decompress(body)
        except Exception:
            return body
    return body


class RequestManager:
    __slots__ = ("_timeout", "_engine")

    DEFAULT_HEADERS = {
        "User-Agent": "httpmas/1.0 (Socket-Based)",
        "Accept": "*/*",
        "Accept-Encoding": _ACCEPT_ENCODING,
        "Connection": "keep-alive",
    }

    _HEADER_CACHE: Dict[str, bytes] = {}
    _HEADER_CACHE_MAX = 512
    _HEADER_LOCK = threading.Lock()

    SAFE_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
    RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
    MAX_REQUEST_RETRIES = 2
    RETRY_MAX_DELAY = 2.0

    def __init__(self, timeout: float = 10.0, max_retries: int = 2) -> None:
        self._timeout = float(timeout)
        self._engine = SocketEngine(
            default_timeout=timeout,
            max_retries=max_retries,
        )

    # ==================== RAISE HELPER ====================

    @staticmethod
    def _maybe_raise(response: Response, raise_on_error: bool) -> Response:
        """Tự động ném HTTPError khi server trả status >= 400."""
        if raise_on_error and response.status_code >= 400:
            raise HTTPError(
                f"HTTP {response.status_code} {response.reason} cho {response.url}",
                status_code=response.status_code,
                response=response,
                print_error=True,
            )
        return response

    # ==================== PUBLIC API ====================

    def request(self, method: str, url: str, **kwargs) -> Response:
        return self._request(method.upper(), url, **kwargs)

    def get(self, url, headers=None, params=None, timeout=None,
            raise_on_error=True) -> Response:
        return self._request("GET", url, headers=headers, params=params,
                             timeout=timeout, raise_on_error=raise_on_error)

    def post(self, url, headers=None, data=None, json=None, params=None,
             timeout=None, raise_on_error=True) -> Response:
        return self._request("POST", url, headers=headers, data=data,
                             json=json, params=params, timeout=timeout,
                             raise_on_error=raise_on_error)

    def put(self, url, headers=None, data=None, json=None,
            timeout=None, raise_on_error=True) -> Response:
        return self._request("PUT", url, headers=headers, data=data,
                             json=json, timeout=timeout,
                             raise_on_error=raise_on_error)

    def delete(self, url, headers=None, timeout=None,
               raise_on_error=True) -> Response:
        return self._request("DELETE", url, headers=headers,
                             timeout=timeout, raise_on_error=raise_on_error)

    def patch(self, url, headers=None, data=None, json=None,
              timeout=None, raise_on_error=True) -> Response:
        return self._request("PATCH", url, headers=headers, data=data,
                             json=json, timeout=timeout,
                             raise_on_error=raise_on_error)

    def head(self, url, headers=None, timeout=None,
             raise_on_error=True) -> Response:
        return self._request("HEAD", url, headers=headers,
                             timeout=timeout, raise_on_error=raise_on_error)

    def options(self, url, headers=None, timeout=None,
                raise_on_error=True) -> Response:
        return self._request("OPTIONS", url, headers=headers,
                             timeout=timeout, raise_on_error=raise_on_error)

    # ==================== HELPERS ====================

    def _discard_sock(self, sock) -> None:
        if sock is None:
            return
        discard = getattr(self._engine, "discard", None)
        if callable(discard):
            try:
                discard(sock)
                return
            except Exception:
                pass
        try:
            SocketEngine._close_socket(sock)
        except Exception:
            pass

    def _release_sock(self, parsed: _URLParser, sock) -> None:
        if sock is None:
            return
        try:
            self._engine.release(
                parsed.hostname, parsed.effective_port,
                parsed.use_tls, sock, reusable=True,
            )
        except Exception:
            self._discard_sock(sock)

    @classmethod
    def _header_bytes(cls, key: str, value: str) -> bytes:
        cache_key = f"{key}\x00{value}"
        cached = cls._HEADER_CACHE.get(cache_key)
        if cached is not None:
            return cached
        with cls._HEADER_LOCK:
            cached = cls._HEADER_CACHE.get(cache_key)
            if cached is not None:
                return cached
            line = f"{key}: {value}\r\n".encode("utf-8")
            if len(cls._HEADER_CACHE) >= cls._HEADER_CACHE_MAX:
                cls._HEADER_CACHE.clear()
            cls._HEADER_CACHE[cache_key] = line
            return line

    @classmethod
    def _build_raw_request(cls, method: str, path: str,
                           headers: Dict[str, str], body: bytes) -> bytes:
        parts = [f"{method} {path} HTTP/1.1\r\n".encode("utf-8")]
        for key, value in headers.items():
            parts.append(cls._header_bytes(key, value))
        parts.append(b"\r\n")
        if body:
            parts.append(body)
        return b"".join(parts)

    @classmethod
    def _is_transient_error(cls, exc: Exception) -> bool:
        if isinstance(exc, _ssl.SSLCertVerificationError):
            return False
        if isinstance(exc, _ssl.SSLError):
            return True
        if isinstance(exc, (socket.timeout, TimeoutError,
                            ConnectionError, BrokenPipeError)):
            return True
        msg = str(getattr(exc, "message", exc)).lower()
        non_retry = (
            "chứng chỉ", "certificate", "status line",
            "content-length", "chunk size", "scheme", "ipv6", "json",
        )
        if any(k in msg for k in non_retry):
            return False
        transient = (
            "timeout", "thời gian chờ", "kết nối", "connection",
            "reset", "broken", "đóng", "eof", "incomplete",
            "không thể kết nối", "socket", "dns",
        )
        return any(k in msg for k in transient)

    @staticmethod
    def _adaptive_backoff(attempt: int, host: str) -> float:
        try:
            with NETWORK_STATS._lock:
                est = NETWORK_STATS._rtt.get(host)
        except Exception:
            est = None
        if est is None:
            base = 0.05
        else:
            base = max(0.01, min(est * 0.5, 0.2))
        delay = base * (2 ** attempt)
        return min(delay, 1.0)

    # ==================== REQUEST LOGIC ====================

    def _request(
        self, method: str, url: str,
        headers=None, data=None, json=None, params=None,
        timeout=None, raise_on_error=True,
    ) -> Response:
        method_upper = method.upper()
        parsed = _URLParser.parse(url)
        effective_timeout = float(
            timeout if timeout is not None else self._timeout
        )
        attempts = (
            self.MAX_REQUEST_RETRIES + 1
            if method_upper in self.SAFE_RETRY_METHODS
            else 1
        )
        deadline = time.monotonic() + effective_timeout
        last_exc: Optional[Exception] = None
        attempt = 0
        stale_retried = False

        while attempt < attempts:
            remaining = deadline - time.monotonic()
            if remaining <= 0.2:
                break
            try:
                resp = self._single_request(
                    method_upper, url,
                    headers=headers, data=data, json=json,
                    params=params, timeout=remaining,
                )
            except _StaleConnectionError:
                if stale_retried:
                    raise RequestsError(
                        f"Connection stale tới {url} sau 2 lần thử",
                        print_error=False,
                    )
                stale_retried = True
                continue
            except Exception as exc:
                last_exc = exc
                if not self._is_transient_error(exc):
                    raise
                attempt += 1
                if attempt >= attempts:
                    raise
                delay = self._adaptive_backoff(attempt - 1, parsed.hostname)
                delay = min(delay, self.RETRY_MAX_DELAY)
                sleep_time = min(delay, max(0.0, deadline - time.monotonic()))
                if sleep_time > 0:
                    time.sleep(sleep_time)
                continue

            if (
                resp.status_code in self.RETRYABLE_STATUS
                and method_upper in self.SAFE_RETRY_METHODS
                and attempt + 1 < attempts
            ):
                retry_after = resp.headers.get("retry-after", "")
                delay = 0.3 * (attempt + 1)
                try:
                    if retry_after:
                        delay = max(delay, float(retry_after))
                except ValueError:
                    pass
                delay = min(delay, self.RETRY_MAX_DELAY)
                sleep_time = min(delay, max(0.0, deadline - time.monotonic()))
                if sleep_time > 0:
                    time.sleep(sleep_time)
                attempt += 1
                continue

            return self._maybe_raise(resp, raise_on_error)

        if last_exc is not None:
            raise last_exc
        raise RequestsError(
            f"Request tới {url} thất bại sau {attempts} lần thử",
            print_error=False,
        )

    def _single_request(
        self, method: str, url: str,
        headers=None, data=None, json=None, params=None, timeout=None,
    ) -> Response:
        start_time = time.monotonic()
        parsed = _URLParser.parse(url)
        path = parsed.full_path
        if params:
            query_str = _FormEncoder.urlencode(params)
            if "?" in path:
                path = path + "&" + query_str
            else:
                path = path + "?" + query_str

        req_headers = dict(self.DEFAULT_HEADERS)
        req_headers["Host"] = parsed.hostname
        if headers:
            req_headers.update(headers)

        body = self._prepare_body(data, json, req_headers)
        raw_request = self._build_raw_request(method, path, req_headers, body)

        effective_timeout = float(
            timeout if timeout is not None else self._timeout
        )
        read_timeout = NETWORK_STATS.read_timeout(
            parsed.hostname, effective_timeout
        )
        sock = self._engine.connect(
            parsed.hostname, parsed.effective_port,
            use_tls=parsed.use_tls, timeout=effective_timeout,
        )
        try:
            sock.settimeout(read_timeout)
            sock.sendall(raw_request)
            old_buffer = getattr(sock, "_httpmas_buffer", None)
            parser = HTTPParser(
                sock, buffer=old_buffer,
                method=method, start_time=start_time,
            )
            (
                status_code, reason,
                resp_headers, resp_body, should_close,
            ) = parser.parse()
            if parser.has_pending:
                should_close = True
            resp_body = _decompress_body(resp_headers, resp_body)
            elapsed = time.monotonic() - start_time
            rtt = parser.header_elapsed if parser.header_elapsed > 0 else elapsed
            NETWORK_STATS.record_rtt(parsed.hostname, rtt)

            if should_close:
                self._discard_sock(sock)
            else:
                reusable_buffer = parser.take_buffer(max_size=1 << 20)
                if reusable_buffer is not None:
                    try:
                        sock._httpmas_buffer = reusable_buffer
                    except Exception:
                        pass
                self._release_sock(parsed, sock)

            return Response(
                status_code=status_code, reason=reason,
                headers=resp_headers, content=resp_body,
                url=url, elapsed=elapsed,
            )
        except _StaleConnectionError:
            raise
        except RequestsError:
            self._discard_sock(sock)
            raise
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self._discard_sock(sock)
            raise _StaleConnectionError()
        except socket.timeout:
            self._discard_sock(sock)
            raise RequestsError(
                f"Hết thời gian chờ khi gửi request tới {parsed.hostname}",
                print_error=False,
            )
        except OSError as exc:
            self._discard_sock(sock)
            raise RequestsError(
                f"Lỗi socket khi giao tiếp với {parsed.hostname}: {exc}",
                print_error=False,
            )

    def _prepare_body(self, data, json, headers) -> bytes:
        body = b""
        if json is not None:
            body = _json.dumps(json, ensure_ascii=False).encode("utf-8")
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json; charset=utf-8"
        elif data is not None:
            if isinstance(data, bytes):
                body = data
            elif isinstance(data, str):
                body = data.encode("utf-8")
            elif isinstance(data, dict):
                body = _FormEncoder.urlencode(data).encode("utf-8")
                if "Content-Type" not in headers:
                    headers["Content-Type"] = (
                        "application/x-www-form-urlencoded"
                    )
            else:
                body = str(data).encode("utf-8")
        if body:
            headers["Content-Length"] = str(len(body))
        return body

    def close(self) -> None:
        self._engine.close_all()

    def __del__(self) -> None:
        try:
            self._engine.close_all()
        except Exception:
            pass


# ============================================================
# API MỨC MODULE
# ============================================================

_default_manager = RequestManager()
_default_async_manager = None


def _get_async_manager() -> Any:
    global _default_async_manager
    if _default_async_manager is None:
        from .async_engine import AsyncRequestManager
        _default_async_manager = AsyncRequestManager()
    return _default_async_manager


def request(method: str, url: str, **kwargs) -> Response:
    return _default_manager.request(method, url, **kwargs)

def get(url: str, **kwargs) -> Response:
    return _default_manager.get(url, **kwargs)

def post(url: str, **kwargs) -> Response:
    return _default_manager.post(url, **kwargs)

def put(url: str, **kwargs) -> Response:
    return _default_manager.put(url, **kwargs)

def delete(url: str, **kwargs) -> Response:
    return _default_manager.delete(url, **kwargs)

def patch(url: str, **kwargs) -> Response:
    return _default_manager.patch(url, **kwargs)

def head(url: str, **kwargs) -> Response:
    return _default_manager.head(url, **kwargs)

def options(url: str, **kwargs) -> Response:
    return _default_manager.options(url, **kwargs)

def close() -> None:
    _default_manager.close()

async def async_request(method: str, url: str, **kwargs) -> Response:
    mgr = _get_async_manager()
    return await mgr._request(method.upper(), url, **kwargs)

async def async_get(url: str, **kwargs) -> Response:
    return await _get_async_manager().async_get(url, **kwargs)

async def async_post(url: str, **kwargs) -> Response:
    return await _get_async_manager().async_post(url, **kwargs)

async def async_put(url: str, **kwargs) -> Response:
    return await _get_async_manager().async_put(url, **kwargs)

async def async_delete(url: str, **kwargs) -> Response:
    return await _get_async_manager().async_delete(url, **kwargs)

async def async_patch(url: str, **kwargs) -> Response:
    return await _get_async_manager().async_patch(url, **kwargs)

async def gather(*coros):
    return await _get_async_manager().gather(*coros)