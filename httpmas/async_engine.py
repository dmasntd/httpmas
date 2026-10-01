"""
AsyncEngine — C-accelerated header parsing.
Nâng cấp: raise_on_error=True mặc định, tự ném HTTPError khi server trả 4xx/5xx.
"""
import asyncio
import socket
import ssl
import time
import json as _json
import gzip
import zlib
import random
from typing import Optional, Dict, Any, List, Tuple
from .exceptions import RequestsError, HTTPError, _ColorPrinter
from .tls_manager import TLSManager
from .response import Response
from .requests import _URLParser, _FormEncoder
from .dns import DNSCache
from ._fast import parse_response_head

try:
    import brotli
    _BROTLI_AVAILABLE = True
except ImportError:
    _BROTLI_AVAILABLE = False

_ACCEPT_ENCODING = "gzip, deflate"
if _BROTLI_AVAILABLE:
    _ACCEPT_ENCODING += ", br"


class _TransientError(Exception):
    __slots__ = ()


async def _wait_writer(writer) -> None:
    try:
        await writer.wait_closed()
    except Exception:
        pass


def _close_writer_now(writer) -> None:
    if writer is None:
        return
    try:
        writer.close()
    except Exception:
        pass
    try:
        loop = asyncio.get_running_loop()
        if not loop.is_closed():
            task = asyncio.ensure_future(_wait_writer(writer))
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    except RuntimeError:
        pass


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


class ErrorDispatcher:
    __slots__ = ("_queue", "_running", "_task")

    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._queue = asyncio.Queue()
        self._running = True
        self._task = asyncio.ensure_future(self._dispatch_loop())

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                _ColorPrinter.print_error(message)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def dispatch(self, message: str) -> None:
        if self._queue is not None:
            await self._queue.put(message)
        else:
            _ColorPrinter.print_error(message)

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None


class _HostPool:
    __slots__ = ("idle", "active", "semaphore", "max_keepalive")

    def __init__(self, max_keepalive: int, max_active: int) -> None:
        self.idle: List[Tuple[Any, Any, float]] = []
        self.active = 0
        self.semaphore = asyncio.Semaphore(max_active)
        self.max_keepalive = max_keepalive


class AsyncConnectionPool:
    __slots__ = ("_max_per_host", "_max_keepalive", "_keepalive_timeout", "_hosts")

    def __init__(self, max_per_host: int = 12, max_keepalive: int = 32, keepalive_timeout: float = 60.0) -> None:
        self._max_per_host = int(max_per_host)
        self._max_keepalive = int(max_keepalive)
        self._keepalive_timeout = float(keepalive_timeout)
        self._hosts: Dict[Tuple[str, int, bool], _HostPool] = {}

    def _get_host_pool(self, key):
        hp = self._hosts.get(key)
        if hp is None:
            hp = _HostPool(self._max_keepalive, self._max_per_host)
            self._hosts[key] = hp
        return hp

    @staticmethod
    def _is_alive(reader, writer) -> bool:
        try:
            if reader is None or writer is None:
                return False
        except Exception:
            return False
        try:
            if writer.is_closing():
                return False
        except AttributeError:
            try:
                if writer.transport.is_closing():
                    return False
            except Exception:
                return False
        except Exception:
            return False
        try:
            if reader.at_eof():
                return False
        except Exception:
            return False
        return True

    def _cleanup_expired(self, hp):
        if not hp.idle:
            return
        now = time.monotonic()
        keep = []
        for item in hp.idle:
            reader, writer, ts = item
            if now - ts > self._keepalive_timeout:
                _close_writer_now(writer)
                continue
            if not self._is_alive(reader, writer):
                _close_writer_now(writer)
                continue
            keep.append(item)
        hp.idle = keep

    async def acquire(self, key, timeout, connector, host, port, use_tls):
        hp = self._get_host_pool(key)
        try:
            await asyncio.wait_for(hp.semaphore.acquire(), timeout=timeout)
        except asyncio.TimeoutError:
            raise _TransientError(f"Hết thời gian chờ connection pool cho {host}:{port}")
        try:
            self._cleanup_expired(hp)
            while hp.idle:
                reader, writer, _ = hp.idle.pop()
                if self._is_alive(reader, writer):
                    hp.active += 1
                    return reader, writer, True
                _close_writer_now(writer)
            reader, writer = await connector(host, port, use_tls, timeout)
            hp.active += 1
            return reader, writer, False
        except Exception:
            hp.semaphore.release()
            raise

    def release(self, key, reader, writer, reusable=True):
        hp = self._hosts.get(key)
        if hp is None:
            _close_writer_now(writer)
            return
        hp.active = max(0, hp.active - 1)
        if reusable and self._is_alive(reader, writer) and len(hp.idle) < hp.max_keepalive:
            hp.idle.append((reader, writer, time.monotonic()))
        else:
            _close_writer_now(writer)
        hp.semaphore.release()

    def discard(self, key, writer):
        hp = self._hosts.get(key)
        if hp is not None:
            hp.active = max(0, hp.active - 1)
            hp.semaphore.release()
        _close_writer_now(writer)

    async def close_all(self):
        for hp in self._hosts.values():
            for reader, writer, _ in hp.idle:
                _close_writer_now(writer)
            hp.idle.clear()
        self._hosts.clear()


class AsyncSocketEngine:
    __slots__ = ("_timeout", "_pool", "_dns")
    STREAM_LIMIT = 4 * 1024 * 1024

    def __init__(self, default_timeout: float = 10.0) -> None:
        self._timeout = default_timeout
        self._pool = AsyncConnectionPool(max_per_host=12, max_keepalive=32, keepalive_timeout=60.0)
        self._dns = DNSCache(ttl=300.0, max_entries=1024)

    @classmethod
    def _tune_raw_socket(cls, sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    async def connect(self, host, port, use_tls=False, timeout=None):
        effective_timeout = timeout if timeout is not None else self._timeout
        key = (host, port, use_tls)
        return await self._pool.acquire(key, effective_timeout, self._open_connection, host, port, use_tls)

    async def _open_connection(self, host, port, use_tls, timeout):
        ssl_context = TLSManager.get_context() if use_tls else None
        try:
            infos = await self._dns.async_resolve(host)
        except RequestsError as exc:
            raise _TransientError(str(exc))
        infos = DNSCache.sort_ipv4_first(infos)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout))
        last_error: Optional[Exception] = None
        for info in infos:
            remaining = deadline - loop.time()
            if remaining <= 0.05:
                break
            per_ip_timeout = min(remaining, 2.5)
            family, socktype, proto, _, sockaddr = info
            raw_sock: Optional[socket.socket] = None
            try:
                raw_sock = socket.socket(family, socktype, proto)
                raw_sock.setblocking(False)
                self._tune_raw_socket(raw_sock)
                try:
                    ip = sockaddr[0]
                    if len(sockaddr) == 4:
                        address = (ip, port, sockaddr[2], sockaddr[3])
                    else:
                        address = (ip, port)
                except (IndexError, TypeError):
                    address = (sockaddr[0], port)
                await asyncio.wait_for(loop.sock_connect(raw_sock, address), timeout=per_ip_timeout)
                if use_tls:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(sock=raw_sock, ssl=ssl_context, server_hostname=host, limit=self.STREAM_LIMIT),
                        timeout=per_ip_timeout,
                    )
                else:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(sock=raw_sock, limit=self.STREAM_LIMIT),
                        timeout=per_ip_timeout,
                    )
                raw_sock = None
                return reader, writer
            except asyncio.TimeoutError as exc:
                last_error = exc
            except ssl.SSLCertVerificationError as exc:
                raise RequestsError(f"Xác minh chứng chỉ thất bại cho {host}: {exc}", print_error=False)
            except ssl.SSLError as exc:
                raise RequestsError(f"Lỗi TLS khi kết nối {host}: {exc}", print_error=False)
            except OSError as exc:
                last_error = exc
            finally:
                if raw_sock is not None:
                    try:
                        raw_sock.close()
                    except OSError:
                        pass
        raise _TransientError(f"Không thể kết nối async tới {host}:{port}: {last_error}")

    def release(self, host, port, use_tls, reader, writer, reusable=True):
        key = (host, port, use_tls)
        self._pool.release(key, reader, writer, reusable)

    def discard(self, host, port, use_tls, writer):
        key = (host, port, use_tls)
        self._pool.discard(key, writer)

    async def close_all(self):
        await self._pool.close_all()


class AsyncHTTPParser:
    """Phân tích HTTP response từ asyncio StreamReader — C-accelerated."""
    __slots__ = ("_reader", "_method")

    def __init__(self, reader, method: str = "GET") -> None:
        self._reader = reader
        self._method = method.upper()

    async def parse(self):
        try:
            head = await self._reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            raise RequestsError("Header async vượt giới hạn StreamReader", print_error=False)
        except asyncio.IncompleteReadError as exc:
            raise _TransientError(f"Kết nối bị đóng khi đọc header: {exc}")
        except (ConnectionError, asyncio.TimeoutError, OSError) as exc:
            raise _TransientError(f"Lỗi kết nối khi đọc header: {exc}")
        head = head[:-4]
        try:
            status_code, reason, http_version, headers = parse_response_head(head)
        except (ValueError, Exception) as exc:
            raise RequestsError(str(exc), print_error=False)
        connection_header = headers.get("connection", "").lower()
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        content_length = headers.get("content-length", "")
        no_body = (
            self._method == "HEAD"
            or status_code in (204, 304)
            or 100 <= status_code < 200
        )
        should_close = False
        if connection_header == "close":
            should_close = True
        elif http_version == "HTTP/1.0" and connection_header != "keep-alive":
            should_close = True
        elif not no_body and "chunked" not in transfer_encoding and not content_length:
            should_close = True
        body = b"" if no_body else await self._read_body(headers)
        return status_code, reason, headers, body, should_close

    async def _read_body(self, headers):
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer_encoding:
            return await self._read_chunked()
        content_length = headers.get("content-length", "")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                raise RequestsError(f"Content-Length không hợp lệ: {content_length}", print_error=False)
            if length == 0:
                return b""
            try:
                return await self._reader.readexactly(length)
            except asyncio.IncompleteReadError as exc:
                raise _TransientError(f"Kết nối bị đóng khi đọc body: {exc}")
            except (ConnectionError, asyncio.TimeoutError, OSError) as exc:
                raise _TransientError(f"Lỗi kết nối khi đọc body: {exc}")
        try:
            return await self._reader.read(-1)
        except (ConnectionError, asyncio.TimeoutError, OSError) as exc:
            raise _TransientError(f"Lỗi kết nối khi đọc body: {exc}")
        except Exception as exc:
            raise RequestsError(f"Lỗi khi đọc body: {exc}", print_error=False)

    async def _read_chunked(self):
        chunks = []
        while True:
            try:
                size_line = await self._reader.readuntil(b"\r\n")
            except asyncio.IncompleteReadError as exc:
                raise _TransientError(f"Kết nối bị đóng khi đọc chunked: {exc}")
            try:
                size_str = size_line.decode("ascii").split(";")[0].strip()
                chunk_size = int(size_str, 16)
            except ValueError as exc:
                raise RequestsError(f"Chunk size không hợp lệ: {exc}", print_error=False)
            if chunk_size == 0:
                while True:
                    try:
                        trailer = await self._reader.readuntil(b"\r\n")
                        if trailer.strip() == b"":
                            break
                    except asyncio.IncompleteReadError:
                        break
                break
            try:
                chunk_data = await self._reader.readexactly(chunk_size)
            except asyncio.IncompleteReadError as exc:
                raise _TransientError(f"Chunk bị cắt ngắn: {exc}")
            if chunk_data:
                chunks.append(chunk_data)
            try:
                await self._reader.readuntil(b"\r\n")
            except asyncio.IncompleteReadError:
                break
        return b"".join(chunks)


class AsyncRequestManager:
    __slots__ = ("_timeout", "_engine", "_error_dispatcher")

    DEFAULT_HEADERS = {
        "User-Agent": "httpmas/1.0 (Async-Socket-Based)",
        "Accept": "*/*",
        "Accept-Encoding": _ACCEPT_ENCODING,
        "Connection": "keep-alive",
    }

    SAFE_RETRY_METHODS = {"GET", "HEAD", "OPTIONS"}
    MAX_AUTO_RETRIES = 2
    RETRY_BASE_DELAY = 0.05
    RETRY_MAX_DELAY = 1.0
    _HEADER_PREFIX_CACHE: Dict[str, bytes] = {}
    _HEADER_CACHE_MAX = 512

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout
        self._engine = AsyncSocketEngine(default_timeout=timeout)
        self._error_dispatcher = ErrorDispatcher()

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

    async def async_get(self, url, headers=None, params=None, timeout=None, raise_on_error=True):
        return await self._request("GET", url, headers=headers, params=params, timeout=timeout, raise_on_error=raise_on_error)

    async def async_post(self, url, headers=None, data=None, json=None, params=None, timeout=None, raise_on_error=True):
        return await self._request("POST", url, headers=headers, data=data, json=json, params=params, timeout=timeout, raise_on_error=raise_on_error)

    async def async_put(self, url, headers=None, data=None, json=None, timeout=None, raise_on_error=True):
        return await self._request("PUT", url, headers=headers, data=data, json=json, timeout=timeout, raise_on_error=raise_on_error)

    async def async_delete(self, url, headers=None, timeout=None, raise_on_error=True):
        return await self._request("DELETE", url, headers=headers, timeout=timeout, raise_on_error=raise_on_error)

    async def async_patch(self, url, headers=None, data=None, json=None, timeout=None, raise_on_error=True):
        return await self._request("PATCH", url, headers=headers, data=data, json=json, timeout=timeout, raise_on_error=raise_on_error)

    async def async_head(self, url, headers=None, timeout=None, raise_on_error=True):
        return await self._request("HEAD", url, headers=headers, timeout=timeout, raise_on_error=raise_on_error)

    async def async_options(self, url, headers=None, timeout=None, raise_on_error=True):
        return await self._request("OPTIONS", url, headers=headers, timeout=timeout, raise_on_error=raise_on_error)

    async def gather(self, *coros):
        await self._error_dispatcher.start()

        async def _wrapper(coro):
            try:
                return await coro
            except RequestsError as exc:
                return exc
            except Exception as exc:
                await self._error_dispatcher.dispatch(str(exc))
                return exc

        tasks = [asyncio.ensure_future(_wrapper(c)) for c in coros]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        self._error_dispatcher.stop()
        return list(results)

    # ==================== HELPERS ====================

    @classmethod
    def _header_prefix(cls, key):
        cache = cls._HEADER_PREFIX_CACHE
        prefix = cache.get(key)
        if prefix is not None:
            return prefix
        prefix = f"{key}: ".encode("utf-8")
        if len(cache) >= cls._HEADER_CACHE_MAX:
            cache.clear()
        cache[key] = prefix
        return prefix

    @classmethod
    def _build_raw_request(cls, method, path, headers, body):
        parts = [f"{method} {path} HTTP/1.1\r\n".encode("utf-8")]
        for key, value in headers.items():
            parts.append(cls._header_prefix(key))
            parts.append(str(value).encode("utf-8"))
            parts.append(b"\r\n")
        parts.append(b"\r\n")
        if body:
            parts.append(body)
        return b"".join(parts)

    @staticmethod
    def _is_transient_error(exc):
        msg = str(getattr(exc, "message", exc)).lower()
        non_retry = ("chứng chỉ", "certificate", "status line", "content-length", "chunk", "json", "scheme", "url", "port")
        if any(m in msg for m in non_retry):
            return False
        transient = ("timeout", "thời gian chờ", "kết nối", "connection", "reset", "broken", "đóng", "dns", "socket", "không thể kết nối", "eof", "incomplete")
        return any(m in msg for m in transient)

    @classmethod
    def _retry_delay(cls, attempt):
        base = cls.RETRY_BASE_DELAY * (2 ** attempt)
        return random.uniform(0, min(base, cls.RETRY_MAX_DELAY))

    @staticmethod
    def _make_final_error(exc):
        if isinstance(exc, RequestsError):
            return exc
        message = "Hết thời gian chờ khi gửi/đọc response" if isinstance(exc, asyncio.TimeoutError) else str(getattr(exc, "message", exc))
        if not message:
            message = exc.__class__.__name__
        try:
            return RequestsError(message, print_error=False)
        except TypeError:
            return RequestsError(message)

    # ==================== REQUEST LOGIC ====================

    async def _request_impl(self, method, url, headers=None, data=None, json=None, params=None, timeout=None, raise_on_error=True):
        method_upper = method.upper()
        parsed = _URLParser.parse(url)
        path = parsed.full_path
        if params:
            query_str = _FormEncoder.urlencode(params)
            if "?" in path:
                path += "&" + query_str
            else:
                path += "?" + query_str
        req_headers = dict(self.DEFAULT_HEADERS)
        req_headers["Host"] = parsed.hostname
        if headers:
            req_headers.update(headers)
        body = self._prepare_body(data, json, req_headers)
        raw_request = self._build_raw_request(method_upper, path, req_headers, body)
        effective_timeout = timeout if timeout is not None else self._timeout
        attempts = self.MAX_AUTO_RETRIES + 1 if method_upper in self.SAFE_RETRY_METHODS else 1
        deadline = time.monotonic() + effective_timeout
        last_exc = None
        for attempt in range(attempts):
            writer = None
            remaining = deadline - time.monotonic()
            if remaining <= 0.3:
                break
            try:
                start_time = time.monotonic()
                reader, writer, reused = await self._engine.connect(parsed.hostname, parsed.effective_port, parsed.use_tls, remaining)
                resp = await self._send_and_parse(method_upper, parsed, url, raw_request, reader, writer, start_time, remaining)
                return self._maybe_raise(resp, raise_on_error)
            except ssl.SSLError as exc:
                if writer is not None:
                    self._engine.discard(parsed.hostname, parsed.effective_port, parsed.use_tls, writer)
                raise RequestsError(f"Lỗi TLS khi giao tiếp với {parsed.hostname}: {exc}", print_error=False)
            except HTTPError:
                raise
            except RequestsError as exc:
                if writer is not None:
                    self._engine.discard(parsed.hostname, parsed.effective_port, parsed.use_tls, writer)
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise
                if not self._is_transient_error(exc):
                    raise
                delay = self._retry_delay(attempt)
                sleep_time = min(delay, max(0, deadline - time.monotonic()))
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            except (_TransientError, asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError) as exc:
                if writer is not None:
                    self._engine.discard(parsed.hostname, parsed.effective_port, parsed.use_tls, writer)
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise self._make_final_error(exc)
                if not self._is_transient_error(exc):
                    raise self._make_final_error(exc)
                delay = self._retry_delay(attempt)
                sleep_time = min(delay, max(0, deadline - time.monotonic()))
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        raise self._make_final_error(last_exc if last_exc else Exception(f"Request tới {url} thất bại sau {attempts} lần thử"))

    async def _request(self, method, url, headers=None, data=None, json=None, params=None, timeout=None, raise_on_error=True):
        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            return await asyncio.wait_for(
                self._request_impl(method, url, headers=headers, data=data, json=json, params=params, timeout=timeout, raise_on_error=raise_on_error),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            raise RequestsError(f"Hết thời gian chờ {effective_timeout}s cho request tới {url}", print_error=False)

    async def _send_and_parse(self, method, parsed, url, raw_request, reader, writer, start_time, timeout):
        try:
            writer.write(raw_request)
            await writer.drain()
            parser = AsyncHTTPParser(reader, method=method)
            status_code, reason, resp_headers, resp_body, should_close = await parser.parse()
        except (_TransientError, asyncio.TimeoutError, ConnectionError, BrokenPipeError, OSError):
            self._engine.discard(parsed.hostname, parsed.effective_port, parsed.use_tls, writer)
            raise
        except RequestsError:
            self._engine.discard(parsed.hostname, parsed.effective_port, parsed.use_tls, writer)
            raise
        resp_body = _decompress_body(resp_headers, resp_body)
        elapsed = time.monotonic() - start_time
        if should_close:
            self._engine.discard(parsed.hostname, parsed.effective_port, parsed.use_tls, writer)
        else:
            self._engine.release(parsed.hostname, parsed.effective_port, parsed.use_tls, reader, writer, reusable=True)
        return Response(status_code=status_code, reason=reason, headers=resp_headers, content=resp_body, url=url, elapsed=elapsed)

    def _prepare_body(self, data, json, headers):
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
        return body

    async def close(self):
        await self._engine.close_all()