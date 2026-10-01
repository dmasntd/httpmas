"""
Session cho httpmas.
Tích hợp: Cookie + Auth + Redirect + Headers.
Nâng cấp: raise_on_error=True mặc định, tự ném HTTPError khi server trả 4xx/5xx.
Cả sync và async.
"""
import time
from typing import Any, Dict, Optional

from .auth import AuthBase
from .cookies import CookieJar, CookiePolicy
from .headers import CaseInsensitiveHeaders
from .redirect import RedirectHandler
from .response import Response
from .exceptions import RequestsError, HTTPError


class Session:
    """HTTP Session với đầy đủ tính năng.

    Khác với RequestManager (chỉ giữ pool):
    - CookieJar: tự động lưu/gửi cookies
    - Auth: áp dụng auth cho mọi request
    - Default headers: headers mặc định cho mọi request
    - Redirect: tự động follow redirects
    - raise_on_error: tự ném HTTPError khi server trả 4xx/5xx
    """
    __slots__ = (
        "_engine", "_cookie_jar", "_auth",
        "_default_headers", "_max_redirects",
        "_allow_redirects", "_timeout", "_max_retries",
        "_closed",
    )

    DEFAULT_USER_AGENT = "httpmas/1.0 (Socket-Based)"

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 2,
        max_redirects: int = 30,
        allow_redirects: bool = True,
        auth: Optional[AuthBase] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[CookieJar] = None,
    ) -> None:
        from .socket_engine import SocketEngine

        self._engine = SocketEngine(
            default_timeout=timeout,
            max_retries=max_retries,
        )
        self._cookie_jar = cookies or CookieJar()
        self._auth = auth
        self._default_headers = CaseInsensitiveHeaders({
            "User-Agent": self.DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        if headers:
            self._default_headers.update(headers)
        self._max_redirects = max_redirects
        self._allow_redirects = allow_redirects
        self._timeout = timeout
        self._max_retries = max_retries
        self._closed = False

    # ---- Context manager ----
    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ---- Properties ----
    @property
    def cookies(self) -> CookieJar:
        return self._cookie_jar

    @cookies.setter
    def cookies(self, jar: CookieJar) -> None:
        self._cookie_jar = jar

    @property
    def auth(self) -> Optional[AuthBase]:
        return self._auth

    @auth.setter
    def auth(self, value: Optional[AuthBase]) -> None:
        self._auth = value

    @property
    def headers(self) -> CaseInsensitiveHeaders:
        return self._default_headers

    # ---- Raise helper ----
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

    # ---- Public API ----
    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Any = None,
        json: Any = None,
        params: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        allow_redirects: Optional[bool] = None,
        auth: Optional[AuthBase] = None,
        raise_on_error: bool = True,
    ) -> Response:
        if self._closed:
            raise RequestsError("Session đã đóng", print_error=False)

        effective_auth = auth or self._auth
        effective_redirects = (
            allow_redirects if allow_redirects is not None
            else self._allow_redirects
        )

        redirect_handler = RedirectHandler(
            max_redirects=self._max_redirects,
            allow_redirects=effective_redirects,
        )

        current_url = url
        current_method = method.upper()
        current_data = data
        current_json = json
        response_history = []

        while True:
            merged_headers = self._prepare_headers(
                current_url, headers, effective_auth
            )
            response = self._single_request(
                current_method,
                current_url,
                merged_headers,
                current_data,
                current_json,
                params,
                timeout,
            )
            self._store_cookies(response, current_url)

            if not redirect_handler.should_redirect(response.status_code):
                break

            redirect_handler.check_loop(current_url)
            redirect_handler.check_limit()
            redirect_handler.record(current_url)

            new_url, new_method, strip_body = redirect_handler.resolve(
                response, current_method, current_url
            )
            response_history.append(response)

            if strip_body:
                current_data = None
                current_json = None

            current_url = new_url
            current_method = new_method

        response._history = response_history
        response._cookies = self._cookie_jar.get_cookies_for_url(current_url)
        return self._maybe_raise(response, raise_on_error)

    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Response:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs) -> Response:
        return self.request("DELETE", url, **kwargs)

    def patch(self, url: str, **kwargs) -> Response:
        return self.request("PATCH", url, **kwargs)

    def head(self, url: str, **kwargs) -> Response:
        kwargs.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs) -> Response:
        return self.request("OPTIONS", url, **kwargs)

    # ---- Internal ----
    def _prepare_headers(
        self,
        url: str,
        extra_headers: Optional[Dict[str, str]],
        auth: Optional[AuthBase],
    ) -> CaseInsensitiveHeaders:
        merged = CaseInsensitiveHeaders()
        merged.update(self._default_headers)

        if extra_headers:
            merged.update(extra_headers)

        cookie_header = self._cookie_jar.get_header_for_url(url)
        if cookie_header:
            merged["Cookie"] = cookie_header

        if auth is not None:
            merged_dict = merged.to_dict()
            merged_dict = auth.apply("GET", url, merged_dict)
            merged = CaseInsensitiveHeaders(merged_dict)

        from .requests import _URLParser
        parsed = _URLParser.parse(url)
        merged["Host"] = parsed.hostname
        return merged

    def _single_request(
        self,
        method: str,
        url: str,
        headers: CaseInsensitiveHeaders,
        data: Any,
        json: Any,
        params: Optional[Dict[str, str]],
        timeout: Optional[float],
    ) -> Response:
        from .requests import _URLParser, _FormEncoder
        from .http_parser import HTTPParser
        from .socket_engine import NETWORK_STATS
        import gzip
        import zlib
        import json as _json

        start_time = time.monotonic()
        parsed = _URLParser.parse(url)
        path = parsed.full_path

        if params:
            query_str = _FormEncoder.urlencode(params)
            if "?" in path:
                path = path + "&" + query_str
            else:
                path = path + "?" + query_str

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
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = str(data).encode("utf-8")
        if body:
            headers["Content-Length"] = str(len(body))

        raw_request = self._build_raw_request(method, path, headers, body)

        effective_timeout = float(
            timeout if timeout is not None else self._timeout
        )
        read_timeout = NETWORK_STATS.read_timeout(
            parsed.hostname, effective_timeout
        )
        sock = self._engine.connect(
            parsed.hostname,
            parsed.effective_port,
            use_tls=parsed.use_tls,
            timeout=effective_timeout,
        )
        try:
            sock.settimeout(read_timeout)
            sock.sendall(raw_request)

            old_buffer = getattr(sock, "_httpmas_buffer", None)
            parser = HTTPParser(
                sock,
                buffer=old_buffer,
                method=method,
                start_time=start_time,
            )
            (
                status_code, reason,
                resp_headers, resp_body, should_close,
            ) = parser.parse()

            if parser.has_pending:
                should_close = True

            resp_body = self._decompress_body(resp_headers, resp_body)
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
                status_code=status_code,
                reason=reason,
                headers=resp_headers,
                content=resp_body,
                url=url,
                elapsed=elapsed,
            )
        except RequestsError:
            self._discard_sock(sock)
            raise
        except Exception as exc:
            self._discard_sock(sock)
            raise RequestsError(
                f"Lỗi khi request tới {url}: {exc}",
                print_error=False,
            )

    @staticmethod
    def _build_raw_request(
        method: str,
        path: str,
        headers: CaseInsensitiveHeaders,
        body: bytes,
    ) -> bytes:
        parts = [f"{method} {path} HTTP/1.1\r\n".encode("utf-8")]
        for key, value in headers.items():
            parts.append(f"{key}: {value}\r\n".encode("utf-8"))
        parts.append(b"\r\n")
        if body:
            parts.append(body)
        return b"".join(parts)

    @staticmethod
    def _decompress_body(headers: Dict[str, str], body: bytes) -> bytes:
        import gzip
        import zlib
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
        return body

    def _store_cookies(self, response: Response, url: str) -> None:
        set_cookie_headers = response.headers.get_list("set-cookie") if hasattr(
            response.headers, "get_list"
        ) else []

        if not set_cookie_headers:
            raw_header = response.headers.get("set-cookie", "")
            if raw_header:
                set_cookie_headers = [raw_header]

        for header_value in set_cookie_headers:
            self._cookie_jar.set_from_header(header_value, url)

    def _discard_sock(self, sock) -> None:
        if sock is None:
            return
        try:
            self._engine.discard(sock)
        except Exception:
            pass

    def _release_sock(self, parsed, sock) -> None:
        if sock is None:
            return
        try:
            self._engine.release(
                parsed.hostname,
                parsed.effective_port,
                parsed.use_tls,
                sock,
                reusable=True,
            )
        except Exception:
            self._discard_sock(sock)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._engine.close_all()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class AsyncSession:
    """Async version của Session."""
    __slots__ = (
        "_engine", "_cookie_jar", "_auth",
        "_default_headers", "_max_redirects",
        "_allow_redirects", "_timeout",
        "_closed",
    )

    def __init__(
        self,
        timeout: float = 10.0,
        max_redirects: int = 30,
        allow_redirects: bool = True,
        auth: Optional[AuthBase] = None,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[CookieJar] = None,
    ) -> None:
        from .async_engine import AsyncSocketEngine

        self._engine = AsyncSocketEngine(default_timeout=timeout)
        self._cookie_jar = cookies or CookieJar()
        self._auth = auth
        self._default_headers = CaseInsensitiveHeaders({
            "User-Agent": "httpmas/1.0 (Async-Socket-Based)",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        if headers:
            self._default_headers.update(headers)
        self._max_redirects = max_redirects
        self._allow_redirects = allow_redirects
        self._timeout = timeout
        self._closed = False

    async def __aenter__(self) -> "AsyncSession":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    @property
    def cookies(self) -> CookieJar:
        return self._cookie_jar

    @property
    def headers(self) -> CaseInsensitiveHeaders:
        return self._default_headers

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

    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Any = None,
        json: Any = None,
        params: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        allow_redirects: Optional[bool] = None,
        raise_on_error: bool = True,
    ) -> Response:
        if self._closed:
            raise RequestsError("AsyncSession đã đóng", print_error=False)

        effective_redirects = (
            allow_redirects if allow_redirects is not None
            else self._allow_redirects
        )

        redirect_handler = RedirectHandler(
            max_redirects=self._max_redirects,
            allow_redirects=effective_redirects,
        )

        current_url = url
        current_method = method.upper()
        current_data = data
        current_json = json
        response_history = []

        while True:
            merged_headers = self._prepare_headers(
                current_url, headers, self._auth
            )
            response = await self._single_request(
                current_method,
                current_url,
                merged_headers,
                current_data,
                current_json,
                params,
                timeout,
            )
            self._store_cookies(response, current_url)

            if not redirect_handler.should_redirect(response.status_code):
                break

            redirect_handler.check_loop(current_url)
            redirect_handler.check_limit()
            redirect_handler.record(current_url)

            new_url, new_method, strip_body = redirect_handler.resolve(
                response, current_method, current_url
            )
            response_history.append(response)

            if strip_body:
                current_data = None
                current_json = None

            current_url = new_url
            current_method = new_method

        response._history = response_history
        response._cookies = self._cookie_jar.get_cookies_for_url(current_url)
        return self._maybe_raise(response, raise_on_error)

    async def get(self, url: str, **kwargs) -> Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> Response:
        return await self.request("DELETE", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> Response:
        return await self.request("PATCH", url, **kwargs)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._engine.close_all()

    def _prepare_headers(
        self,
        url: str,
        extra_headers: Optional[Dict[str, str]],
        auth: Optional[AuthBase],
    ) -> CaseInsensitiveHeaders:
        merged = CaseInsensitiveHeaders()
        merged.update(self._default_headers)

        if extra_headers:
            merged.update(extra_headers)

        cookie_header = self._cookie_jar.get_header_for_url(url)
        if cookie_header:
            merged["Cookie"] = cookie_header

        if auth is not None:
            merged_dict = merged.to_dict()
            merged_dict = auth.apply("GET", url, merged_dict)
            merged = CaseInsensitiveHeaders(merged_dict)

        from .requests import _URLParser
        parsed = _URLParser.parse(url)
        merged["Host"] = parsed.hostname
        return merged

    async def _single_request(
        self,
        method: str,
        url: str,
        headers: CaseInsensitiveHeaders,
        data: Any,
        json: Any,
        params: Optional[Dict[str, str]],
        timeout: Optional[float],
    ) -> Response:
        from .requests import _URLParser, _FormEncoder
        from .async_engine import AsyncHTTPParser, _decompress_body
        import json as _json

        start_time = time.monotonic()
        parsed = _URLParser.parse(url)
        path = parsed.full_path

        if params:
            query_str = _FormEncoder.urlencode(params)
            if "?" in path:
                path = path + "&" + query_str
            else:
                path = path + "?" + query_str

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
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = str(data).encode("utf-8")
        if body:
            headers["Content-Length"] = str(len(body))

        parts = [f"{method} {path} HTTP/1.1\r\n".encode("utf-8")]
        for key, value in headers.items():
            parts.append(f"{key}: {value}\r\n".encode("utf-8"))
        parts.append(b"\r\n")
        if body:
            parts.append(body)
        raw_request = b"".join(parts)

        effective_timeout = float(
            timeout if timeout is not None else self._timeout
        )
        reader, writer, reused = await self._engine.connect(
            parsed.hostname,
            parsed.effective_port,
            parsed.use_tls,
            effective_timeout,
        )
        try:
            writer.write(raw_request)
            await writer.drain()

            parser = AsyncHTTPParser(reader, method=method)
            (
                status_code, reason,
                resp_headers, resp_body, should_close,
            ) = await parser.parse()

            resp_body = _decompress_body(resp_headers, resp_body)
            elapsed = time.monotonic() - start_time

            if should_close:
                self._engine.discard(
                    parsed.hostname, parsed.effective_port,
                    parsed.use_tls, writer,
                )
            else:
                self._engine.release(
                    parsed.hostname, parsed.effective_port,
                    parsed.use_tls, reader, writer,
                    reusable=True,
                )

            return Response(
                status_code=status_code,
                reason=reason,
                headers=resp_headers,
                content=resp_body,
                url=url,
                elapsed=elapsed,
            )
        except Exception as exc:
            try:
                self._engine.discard(
                    parsed.hostname, parsed.effective_port,
                    parsed.use_tls, writer,
                )
            except Exception:
                pass
            raise

    def _store_cookies(self, response: Response, url: str) -> None:
        set_cookie_headers = response.headers.get_list("set-cookie") if hasattr(
            response.headers, "get_list"
        ) else []

        if not set_cookie_headers:
            raw_header = response.headers.get("set-cookie", "")
            if raw_header:
                set_cookie_headers = [raw_header]

        for header_value in set_cookie_headers:
            self._cookie_jar.set_from_header(header_value, url)