"""
Connection pool cho httpmas.

Bao gồm:
- Connection
- ConnectionPool
- PoolManager
- AsyncConnection
- AsyncConnectionPool
- AsyncPoolManager
"""

import asyncio
import select
import socket
import ssl as _ssl
import threading
import time
from collections import deque
from typing import Callable, Dict, Optional, Tuple

from .dns import DNSCache
from .exceptions import RequestsError
from .tls_manager import TLSManager


class Connection:
    """Đại diện một kết nối socket đồng bộ."""

    __slots__ = ("sock", "host", "port", "use_tls", "created_at", "last_used")

    def __init__(
        self,
        sock: socket.socket,
        host: str,
        port: int,
        use_tls: bool,
    ) -> None:
        self.sock = sock
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.created_at = time.monotonic()
        self.last_used = self.created_at

    @property
    def key(self) -> Tuple[str, int, bool]:
        return self.host, self.port, self.use_tls

    @property
    def idle_time(self) -> float:
        return time.monotonic() - self.last_used

    def mark_used(self) -> None:
        self.last_used = time.monotonic()

    def close(self) -> None:
        """Đóng socket an toàn."""
        sock = self.sock
        self.sock = None
        if sock is None:
            return

        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            sock.close()
        except OSError:
            pass


class ConnectionPool:
    """Connection pool đồng bộ - FIX lỗi 1, 2, 5."""

    def __init__(
        self,
        max_per_host: int = 10,
        max_connections: int = 128,
        idle_timeout: float = 60.0,
    ) -> None:
        self._max_per_host = int(max_per_host)
        self._max_connections = int(max_connections)
        self._idle_timeout = float(idle_timeout)

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

        self._idle: Dict[Tuple[str, int, bool], list] = {}
        self._open_count = 0
        self._idle_count = 0

    def _is_expired(self, conn: Connection) -> bool:
        if conn.sock is None:
            return True
        return conn.idle_time > self._idle_timeout

    def _is_healthy(self, conn: Connection) -> bool:
        """FIX #2: Health check nhẹ nhàng hơn.
        
        - Bỏ check SSLSocket.pending() > 0 (hiểu sai TLS data)
        - Chỉ dùng select() để check EOF
        - Không consume byte
        """
        sock = conn.sock
        if sock is None:
            return False

        try:
            if sock.fileno() < 0:
                return False
        except (OSError, ValueError):
            return False

        try:
            # FIX #2: Chỉ dùng select() để phát hiện EOF
            # Không check pending() cho SSLSocket
            readable, _, _ = select.select([sock], [], [], 0)

            if readable:
                # Socket readable khi idle = server đã đóng (EOF)
                # Thử peek 1 byte để xác nhận
                try:
                    if isinstance(sock, _ssl.SSLSocket):
                        # SSLSocket: không dùng MSG_PEEK
                        # readable + idle = EOF
                        return False
                    else:
                        # Socket thường: peek 1 byte
                        msg_peek = getattr(socket, "MSG_PEEK", 0x02)
                        data = sock.recv(1, msg_peek)
                        # Nếu có data hoặc EOF (b"") → không healthy
                        return False
                except (BlockingIOError, OSError):
                    # BlockingIOError = không có data thực sự (false positive)
                    return True

            return True

        except Exception:
            return False

    def _cleanup_expired_locked(self) -> None:
        """Dọn connection hết hạn / chết."""
        for key, conns in list(self._idle.items()):
            keep = []

            for conn in conns:
                self._idle_count -= 1

                if (
                    conn.sock is None
                    or self._is_expired(conn)
                    or not self._is_healthy(conn)
                ):
                    conn.close()
                    self._open_count = max(0, self._open_count - 1)
                else:
                    keep.append(conn)
                    self._idle_count += 1

            if keep:
                self._idle[key] = keep
            else:
                self._idle.pop(key, None)

    def acquire(
        self,
        host: str,
        port: int,
        use_tls: bool,
        timeout: Optional[float] = None,
        connector: Optional[Callable] = None,
    ) -> Connection:
        """Lấy connection từ pool hoặc tạo mới."""
        if connector is None:
            raise RequestsError("ConnectionPool cần connector")

        key = (host, port, use_tls)
        deadline = None if timeout is None else time.monotonic() + float(timeout)

        with self._condition:
            self._cleanup_expired_locked()

            while True:
                # FIX #5: Dùng LIFO - pop() thay vì popleft()
                # Connection nóng nhất (vừa dùng) được ưu tiên
                conns = self._idle.get(key)
                if conns:
                    conn = conns.pop()
                    self._idle_count -= 1

                    if not conns:
                        self._idle.pop(key, None)

                    # Check health khi ACQUIRE (không phải khi release)
                    if (
                        conn.sock is not None
                        and not self._is_expired(conn)
                        and self._is_healthy(conn)
                    ):
                        conn.mark_used()
                        return conn

                    # Connection chết, bỏ qua
                    conn.close()
                    self._open_count = max(0, self._open_count - 1)
                    continue

                if self._open_count < self._max_connections:
                    self._open_count += 1
                    break

                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RequestsError(
                            "Hết thời gian chờ khi đợi connection"
                        )
                    self._condition.wait(timeout=min(0.05, remaining))
                else:
                    self._condition.wait(timeout=0.05)

                self._cleanup_expired_locked()

        try:
            sock = connector(host, port, use_tls, timeout)
            return Connection(sock, host, port, use_tls)
        except Exception:
            with self._condition:
                self._open_count = max(0, self._open_count - 1)
                self._condition.notify_all()
            raise

    def release(self, conn: Connection, reusable: bool = True) -> None:
        """FIX #1: KHÔNG gọi _is_healthy() khi release.
        
        Chỉ check:
        - reusable flag (từ parser: Connection: close)
        - idle timeout
        - max per host
        """
        if conn is None:
            return

        with self._condition:
            if conn.sock is None:
                self._open_count = max(0, self._open_count - 1)
                self._condition.notify_all()
                return

            # FIX #1: Không check health khi release
            can_pool = (
                reusable
                and not self._is_expired(conn)
                and len(self._idle.get(conn.key, [])) < self._max_per_host
                and self._idle_count < self._max_connections
            )

            if can_pool:
                conn.mark_used()
                self._idle.setdefault(conn.key, []).append(conn)
                self._idle_count += 1
            else:
                conn.close()
                self._open_count = max(0, self._open_count - 1)

            self._condition.notify_all()

    def discard(self, conn: Connection) -> None:
        if conn is None:
            return

        with self._condition:
            conn.close()
            self._open_count = max(0, self._open_count - 1)
            self._condition.notify_all()

    def cleanup_expired(self) -> None:
        with self._condition:
            self._cleanup_expired_locked()
            self._condition.notify_all()

    def close_all(self) -> None:
        with self._condition:
            for conns in self._idle.values():
                for conn in conns:
                    conn.close()
                    self._open_count = max(0, self._open_count - 1)

            self._idle.clear()
            self._idle_count = 0
            self._condition.notify_all()


class PoolManager:
    """Quản lý pool + DNS + tạo socket."""

    def __init__(
        self,
        default_timeout: float = 10.0,
        max_retries: int = 2,
        dns_ttl: float = 300.0,
        max_per_host: int = 10,
        max_connections: int = 128,
        idle_timeout: float = 60.0,
    ) -> None:
        self.default_timeout = float(default_timeout)
        self.max_retries = int(max_retries)
        self.dns_cache = DNSCache(ttl=dns_ttl)
        self.pool = ConnectionPool(
            max_per_host=max_per_host,
            max_connections=max_connections,
            idle_timeout=idle_timeout,
        )

    @staticmethod
    def optimize_socket(sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        except OSError:
            pass

        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        except OSError:
            pass

    @staticmethod
    def _safe_close(sock: Optional[socket.socket]) -> None:
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass

    @staticmethod
    def _address_for(sockaddr: tuple, port: int) -> tuple:
        try:
            if len(sockaddr) == 2:
                return sockaddr[0], port
            if len(sockaddr) == 4:
                return sockaddr[0], port, sockaddr[2], sockaddr[3]
        except (IndexError, TypeError):
            pass
        return sockaddr

    def acquire(
        self,
        host: str,
        port: int,
        use_tls: bool,
        timeout: Optional[float] = None,
    ) -> Connection:
        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )
        return self.pool.acquire(
            host,
            port,
            use_tls,
            timeout=effective_timeout,
            connector=self._create_connected_socket,
        )

    def release(self, conn: Connection, reusable: bool = True) -> None:
        self.pool.release(conn, reusable=reusable)

    def discard(self, conn: Connection) -> None:
        self.pool.discard(conn)

    def cleanup(self) -> None:
        self.pool.cleanup_expired()

    def close_all(self) -> None:
        self.pool.close_all()
        self.dns_cache.clear()

    def _create_connected_socket(
        self,
        host: str,
        port: int,
        use_tls: bool,
        timeout: Optional[float] = None,
    ) -> socket.socket:
        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )

        infos = self.dns_cache.resolve(host)
        if not infos:
            raise RequestsError(f"Không có địa chỉ nào cho host {host}")

        last_error: Optional[Exception] = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            for info in infos:
                family, socktype, proto, _, sockaddr = info
                sock: Optional[socket.socket] = None

                try:
                    sock = socket.socket(family, socktype, proto)
                    sock.settimeout(effective_timeout)
                    self.optimize_socket(sock)

                    address = self._address_for(sockaddr, port)
                    sock.connect(address)

                    if use_tls:
                        sock = TLSManager.wrap_socket(sock, host)

                    return sock

                except RequestsError:
                    self._safe_close(sock)
                    raise

                except _ssl.SSLCertVerificationError as exc:
                    self._safe_close(sock)
                    raise RequestsError(
                        f"Xác minh chứng chỉ thất bại cho {host}: {exc}"
                    )

                except _ssl.SSLError as exc:
                    self._safe_close(sock)
                    raise RequestsError(
                        f"Lỗi TLS khi kết nối {host}: {exc}"
                    )

                except socket.timeout as exc:
                    last_error = exc
                    self._safe_close(sock)

                except OSError as exc:
                    last_error = exc
                    self._safe_close(sock)

            if attempt < attempts - 1:
                time.sleep(0.1 * (attempt + 1))

        raise RequestsError(
            f"Không thể kết nối tới {host}:{port} sau {attempts} lần thử: "
            f"{last_error}"
        )


class AsyncConnection:
    """Đại diện một kết nối async."""

    __slots__ = (
        "reader",
        "writer",
        "host",
        "port",
        "use_tls",
        "created_at",
        "last_used",
        "closed",
    )

    def __init__(
        self,
        reader,
        writer,
        host: str,
        port: int,
        use_tls: bool,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.created_at = time.monotonic()
        self.last_used = self.created_at
        self.closed = False

    @property
    def key(self) -> Tuple[str, int, bool]:
        return self.host, self.port, self.use_tls

    @property
    def idle_time(self) -> float:
        return time.monotonic() - self.last_used

    def mark_used(self) -> None:
        self.last_used = time.monotonic()

    def is_alive(self) -> bool:
        if self.closed:
            return False

        try:
            if self.reader is None or self.writer is None:
                return False
            if self.reader.at_eof():
                return False
        except Exception:
            return False

        try:
            if self.writer.is_closing():
                return False
        except AttributeError:
            try:
                if self.writer.transport.is_closing():
                    return False
            except Exception:
                return False
        except Exception:
            return False

        return True

    async def close(self) -> None:
        if self.closed:
            return

        self.closed = True
        writer = self.writer
        self.reader = None
        self.writer = None

        if writer is None:
            return

        try:
            writer.close()
        except Exception:
            pass

        try:
            await writer.wait_closed()
        except Exception:
            pass


class AsyncConnectionPool:
    """Connection pool async - FIX lỗi 1, 5."""

    def __init__(
        self,
        max_per_host: int = 10,
        max_connections: int = 128,
        idle_timeout: float = 60.0,
    ) -> None:
        self._max_per_host = int(max_per_host)
        self._max_connections = int(max_connections)
        self._idle_timeout = float(idle_timeout)

        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(self._max_connections)

        self._idle: Dict[Tuple[str, int, bool], list] = {}
        self._open_count = 0
        self._idle_count = 0

    def _is_expired(self, conn: AsyncConnection) -> bool:
        if conn.closed:
            return True
        return conn.idle_time > self._idle_timeout

    def _cleanup_locked(self) -> list:
        expired = []

        for key, conns in list(self._idle.items()):
            keep = []

            for conn in conns:
                self._idle_count -= 1

                if (
                    conn.closed
                    or self._is_expired(conn)
                    or not conn.is_alive()
                ):
                    self._open_count = max(0, self._open_count - 1)
                    expired.append(conn)
                else:
                    keep.append(conn)
                    self._idle_count += 1

            if keep:
                self._idle[key] = keep
            else:
                self._idle.pop(key, None)

        return expired

    async def _finalize_closed(self, conn: AsyncConnection) -> None:
        await conn.close()
        self._semaphore.release()

    async def acquire(
        self,
        host: str,
        port: int,
        use_tls: bool,
        timeout: Optional[float] = None,
        connector: Optional[Callable] = None,
    ) -> AsyncConnection:
        if connector is None:
            raise RequestsError("AsyncConnectionPool cần connector")

        key = (host, port, use_tls)

        async with self._lock:
            expired = self._cleanup_locked()
            
            # FIX #5: LIFO - pop() từ cuối list
            conn = None
            conns = self._idle.get(key)
            if conns:
                conn = conns.pop()
                self._idle_count -= 1
                if not conns:
                    self._idle.pop(key, None)

        for item in expired:
            await self._finalize_closed(item)

        if conn is not None:
            conn.mark_used()
            return conn

        try:
            if timeout is None:
                await self._semaphore.acquire()
            else:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=float(timeout),
                )
        except asyncio.TimeoutError:
            raise RequestsError(
                "Hết thời gian chờ khi đợi connection async"
            )

        try:
            conn = await connector(host, port, use_tls, timeout)
            async with self._lock:
                self._open_count += 1
            return conn
        except Exception:
            self._semaphore.release()
            raise

    async def release(self, conn: AsyncConnection, reusable: bool = True) -> None:
        """FIX #1: Không check health khi release."""
        if conn is None or conn.closed:
            return

        # FIX #1: Chỉ check reusable flag + expired
        if reusable and not self._is_expired(conn):
            async with self._lock:
                conns = self._idle.setdefault(conn.key, [])
                if len(conns) < self._max_per_host:
                    conn.mark_used()
                    conns.append(conn)
                    self._idle_count += 1
                    return

        async with self._lock:
            self._open_count = max(0, self._open_count - 1)

        await conn.close()
        self._semaphore.release()

    async def discard(self, conn: AsyncConnection) -> None:
        if conn is None or conn.closed:
            return

        async with self._lock:
            self._open_count = max(0, self._open_count - 1)

        await conn.close()
        self._semaphore.release()

    async def close_all(self) -> None:
        async with self._lock:
            conns = []
            for c in self._idle.values():
                conns.extend(c)

            idle_count = self._idle_count
            self._idle.clear()
            self._idle_count = 0
            self._open_count = max(0, self._open_count - idle_count)

        for conn in conns:
            await conn.close()
            self._semaphore.release()


class AsyncPoolManager:
    """Quản lý async pool + async DNS."""

    def __init__(
        self,
        default_timeout: float = 10.0,
        dns_ttl: float = 300.0,
        max_per_host: int = 10,
        max_connections: int = 128,
        idle_timeout: float = 60.0,
    ) -> None:
        self.default_timeout = float(default_timeout)
        self.dns_cache = DNSCache(ttl=dns_ttl)
        self.pool = AsyncConnectionPool(
            max_per_host=max_per_host,
            max_connections=max_connections,
            idle_timeout=idle_timeout,
        )

    async def acquire(
        self,
        host: str,
        port: int,
        use_tls: bool,
        timeout: Optional[float] = None,
    ) -> AsyncConnection:
        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )
        return await self.pool.acquire(
            host,
            port,
            use_tls,
            timeout=effective_timeout,
            connector=self._create_connection,
        )

    async def release(self, conn: AsyncConnection, reusable: bool = True) -> None:
        await self.pool.release(conn, reusable=reusable)

    async def discard(self, conn: AsyncConnection) -> None:
        await self.pool.discard(conn)

    async def close_all(self) -> None:
        await self.pool.close_all()
        self.dns_cache.clear()

    @staticmethod
    def _address_for(sockaddr: tuple, port: int) -> tuple:
        return PoolManager._address_for(sockaddr, port)

    async def _create_connection(
        self,
        host: str,
        port: int,
        use_tls: bool,
        timeout: Optional[float] = None,
    ) -> AsyncConnection:
        effective_timeout = (
            self.default_timeout if timeout is None else float(timeout)
        )

        async def _make() -> AsyncConnection:
            infos = await self.dns_cache.async_resolve(host)
            if not infos:
                raise RequestsError(f"Không có địa chỉ nào cho host {host}")

            last_error: Optional[Exception] = None
            loop = asyncio.get_running_loop()
            current_sock: Optional[socket.socket] = None

            try:
                for info in infos:
                    family, socktype, proto, _, sockaddr = info
                    sock: Optional[socket.socket] = None

                    try:
                        sock = socket.socket(family, socktype, proto)
                        current_sock = sock
                        sock.setblocking(False)
                        PoolManager.optimize_socket(sock)

                        address = self._address_for(sockaddr, port)
                        await loop.sock_connect(sock, address)

                        if use_tls:
                            ssl_context = TLSManager.get_context()
                            reader, writer = await asyncio.open_connection(
                                sock=sock,
                                ssl=ssl_context,
                                server_hostname=host,
                            )
                        else:
                            reader, writer = await asyncio.open_connection(
                                sock=sock
                            )

                        current_sock = None
                        return AsyncConnection(reader, writer, host, port, use_tls)

                    except RequestsError:
                        if sock is not None:
                            try:
                                sock.close()
                            except OSError:
                                pass
                        raise

                    except _ssl.SSLCertVerificationError as exc:
                        if sock is not None:
                            try:
                                sock.close()
                            except OSError:
                                pass
                        raise RequestsError(
                            f"Xác minh chứng chỉ thất bại cho {host}: {exc}"
                        )

                    except _ssl.SSLError as exc:
                        if sock is not None:
                            try:
                                sock.close()
                            except OSError:
                                pass
                        raise RequestsError(
                            f"Lỗi TLS khi kết nối async {host}: {exc}"
                        )

                    except (asyncio.TimeoutError, OSError) as exc:
                        last_error = exc
                        if sock is not None:
                            try:
                                sock.close()
                            except OSError:
                                pass
                        continue

                raise RequestsError(
                    f"Không thể kết nối async tới {host}:{port}: {last_error}"
                )

            except asyncio.CancelledError:
                if current_sock is not None:
                    try:
                        current_sock.close()
                    except OSError:
                        pass
                raise

        try:
            return await asyncio.wait_for(_make(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            raise RequestsError(
                f"Hết thời gian chờ khi kết nối async tới {host}:{port}"
            )