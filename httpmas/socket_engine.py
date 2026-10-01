"""
Tầng socket TCP httpmas — Happy Eyeballs + Adaptive Backoff.
Không giảm timeout cơ học. Xử lý thông minh hơn.
"""
import errno
import socket
import time
import threading
import select
import ssl as _ssl
from typing import Optional, Dict, Tuple, List

from .exceptions import RequestsError
from .tls_manager import TLSManager
from .dns import DNSCache

_MSG_DONTWAIT = getattr(socket, "MSG_DONTWAIT", 0)


class NetworkStats:
    __slots__ = ("_lock", "_rtt", "_conn")

    ALPHA = 0.25

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rtt: Dict[str, float] = {}
        self._conn: Dict[str, float] = {}

    def _update(self, store, host, dt, lo, hi):
        if dt <= 0:
            return
        dt = max(lo, min(hi, dt))
        with self._lock:
            cur = store.get(host)
            store[host] = dt if cur is None else (self.ALPHA * dt + (1 - self.ALPHA) * cur)

    def record_connect(self, host: str, dt: float) -> None:
        self._update(self._conn, host, dt, 0.001, 30.0)

    def record_rtt(self, host: str, dt: float) -> None:
        self._update(self._rtt, host, dt, 0.001, 60.0)

    def connect_timeout(self, host: str, cap: float) -> float:
        with self._lock:
            est = self._conn.get(host)
        if est is None:
            return min(cap, 10.0)
        return max(2.0, min(cap, est * 3.0 + 0.5))

    def read_timeout(self, host: str, cap: float) -> float:
        with self._lock:
            est = self._rtt.get(host)
        if est is None:
            return cap
        return max(5.0, min(cap, est * 6.0 + 1.0))


NETWORK_STATS = NetworkStats()


class SocketEngine:
    __slots__ = (
        "_timeout", "_max_retries", "_pool", "_lock",
        "_dns", "_sorted_cache", "_sorted_lock",
    )

    MAX_POOL_PER_HOST = 32
    RECV_BUFFER = 256 * 1024
    PROBE_IDLE_THRESHOLD = 30.0
    HAPPY_EYEBALLS_STAGGER = 0.250  # 250ms theo RFC 8305

    def __init__(self, default_timeout: float = 10.0, max_retries: int = 2) -> None:
        self._timeout = default_timeout
        self._max_retries = max_retries
        self._pool: Dict[Tuple[str, int, bool], List] = {}
        self._lock = threading.Lock()
        self._dns = DNSCache(ttl=300.0, max_entries=1024)
        self._sorted_cache: Dict[str, List] = {}
        self._sorted_lock = threading.Lock()

    # ---------- DNS ----------

    def _get_sorted_infos(self, host: str) -> List:
        with self._sorted_lock:
            cached = self._sorted_cache.get(host)
            if cached is not None:
                return cached
        infos = self._dns.resolve(host)
        sorted_infos = DNSCache.sort_ipv4_first(infos)
        with self._sorted_lock:
            if len(self._sorted_cache) >= 256:
                self._sorted_cache.clear()
            self._sorted_cache[host] = sorted_infos
        return sorted_infos

    # ---------- socket tuning ----------

    @classmethod
    def _tune_socket(cls, sock: socket.socket) -> None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        # Không hardcode SO_RCVBUF/SNDBUF. Để OS auto-tune.

    # ---------- Happy Eyeballs (RFC 8305) ----------

    def _happy_connect(self, infos: List, port: int, deadline: float) -> socket.socket:
        """
        Kết nối nhiều IP song song với stagger delay.
        IP nào connect xong trước → thắng.
        Không cần giảm timeout, không gây false timeout trên mạng chậm.
        """
        stagger = self.HAPPY_EYEBALLS_STAGGER
        pending: List[Tuple[socket.socket, tuple]] = []
        last_error: Optional[Exception] = None
        idx = 0
        n = len(infos)

        while True:
            # --- Khởi tạo connection tiếp theo nếu còn IP ---
            if idx < n:
                family, socktype, proto, _, sockaddr = infos[idx]
                idx += 1
                sock: Optional[socket.socket] = None
                try:
                    sock = socket.socket(family, socktype, proto)
                    self._tune_socket(sock)
                    sock.setblocking(False)
                    addr = self._address_for(sockaddr, port)
                    try:
                        sock.connect(addr)
                        # Connect xong ngay (localhost / very fast)
                        sock.setblocking(True)
                        for s, _ in pending:
                            self._close_socket(s)
                        return sock
                    except BlockingIOError:
                        # Non-blocking connect đang chạy
                        pending.append((sock, addr))
                    except OSError as exc:
                        last_error = exc
                        self._close_socket(sock)
                        continue
                except OSError as exc:
                    last_error = exc
                    if sock is not None:
                        self._close_socket(sock)
                    continue

            # --- Không còn socket nào đang chờ → thất bại ---
            if not pending:
                break

            # --- Tính thời gian chờ ---
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            if idx < n:
                # Còn IP để thử → chờ stagger delay
                wait_time = min(stagger, remaining)
            else:
                # Hết IP → chờ đến deadline
                wait_time = remaining

            # --- select: socket nào writable trước → kiểm tra kết quả ---
            socks = [s for s, _ in pending]
            try:
                _, writable, _ = select.select([], socks, [], wait_time)
            except (OSError, ValueError):
                break

            for s in writable:
                try:
                    err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                except OSError:
                    err = -1

                if err == 0:
                    # Thành công — đóng tất cả socket thua
                    s.setblocking(True)
                    for other, _ in pending:
                        if other is not s:
                            self._close_socket(other)
                    return s
                else:
                    # Thất bại — loại khỏi pending
                    err_name = errno.errorcode.get(err, str(err))
                    last_error = OSError(err, f"connect failed: {err_name}")
                    pending = [(ps, pa) for ps, pa in pending if ps is not s]
                    self._close_socket(s)

            if time.monotonic() >= deadline:
                break

        # --- Dọn dẹp ---
        for s, _ in pending:
            self._close_socket(s)

        if last_error is not None:
            raise last_error
        raise OSError("Không thể kết nối tới bất kỳ địa chỉ nào")

    # ---------- Adaptive Backoff ----------

    @staticmethod
    def _adaptive_backoff(attempt: int, host: str) -> float:
        """
        Backoff thông minh dựa trên EWMA RTT thực tế.
        Mạng nhanh (RTT 20ms) → backoff ~10ms.
        Mạng chậm (RTT 800ms) → backoff ~200ms.
        """
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

    # ---------- liveness probe ----------

    @staticmethod
    def _probe_alive(sock: socket.socket) -> bool:
        try:
            if isinstance(sock, _ssl.SSLSocket):
                try:
                    if sock.pending() > 0:
                        return True
                except (_ssl.SSLError, OSError):
                    return False
                try:
                    readable, _, _ = select.select([sock], [], [], 0)
                except (OSError, ValueError):
                    return False
                return not readable

            if _MSG_DONTWAIT:
                try:
                    sock.recv(1, socket.MSG_PEEK | _MSG_DONTWAIT)
                    return False
                except BlockingIOError:
                    return True
                except (OSError, ValueError):
                    return False

            old = sock.gettimeout()
            try:
                sock.settimeout(0.0)
                try:
                    sock.recv(1, socket.MSG_PEEK)
                    return False
                except BlockingIOError:
                    return True
                except (OSError, ValueError):
                    return False
            finally:
                sock.settimeout(old)
        except Exception:
            return False

    # ---------- connect ----------

    def connect(
        self,
        host: str,
        port: int,
        use_tls: bool = False,
        timeout: Optional[float] = None,
    ) -> socket.socket:
        effective_timeout = timeout if timeout is not None else self._timeout
        key = (host, port, use_tls)

        # 1. Thử pool trước
        pooled = self._get_from_pool(key)
        if pooled is not None:
            try:
                pooled.settimeout(effective_timeout)
                return pooled
            except OSError:
                self._close_socket(pooled)

        # 2. Fresh connect với Happy Eyeballs
        deadline = time.monotonic() + effective_timeout
        attempts = self._max_retries + 1
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0.1:
                break

            try:
                infos = self._get_sorted_infos(host)
            except RequestsError:
                raise

            if not infos:
                raise RequestsError(
                    f"Không có địa chỉ nào cho {host}",
                    print_error=False,
                )

            t0 = time.monotonic()
            try:
                sock = self._happy_connect(infos, port, deadline)
            except OSError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = self._adaptive_backoff(attempt, host)
                    sleep_time = min(delay, max(0, deadline - time.monotonic()))
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                continue

            NETWORK_STATS.record_connect(host, time.monotonic() - t0)

            remaining = deadline - time.monotonic()
            if remaining <= 0.1:
                self._close_socket(sock)
                break

            sock.settimeout(remaining)

            if use_tls:
                try:
                    sock = TLSManager.wrap_socket(sock, host)
                except RequestsError as exc:
                    msg = str(getattr(exc, "message", "")).lower()
                    if "chứng chỉ" in msg or "certificate" in msg:
                        raise
                    last_error = exc
                    if attempt < self._max_retries:
                        delay = self._adaptive_backoff(attempt, host)
                        sleep_time = min(delay, max(0, deadline - time.monotonic()))
                        if sleep_time > 0:
                            time.sleep(sleep_time)
                    continue

            sock.settimeout(effective_timeout)
            return sock

        raise RequestsError(
            f"Không thể kết nối tới {host}:{port} sau {attempts} lần thử: {last_error}",
            print_error=False,
        )

    # ---------- pool ----------

    def _get_from_pool(self, key) -> Optional[socket.socket]:
        with self._lock:
            conns = self._pool.get(key)
            if not conns:
                return None
            now = time.monotonic()
            while conns:
                sock, ts = conns.pop()
                idle = now - ts
                if idle > self.PROBE_IDLE_THRESHOLD:
                    if not self._probe_alive(sock):
                        self._close_socket(sock)
                        continue
                return sock
            self._pool.pop(key, None)
        return None

    def release(self, host, port, use_tls, sock, reusable: bool = True) -> None:
        if sock is None:
            return
        if not reusable:
            self._close_socket(sock)
            return
        try:
            if sock.fileno() < 0:
                self._close_socket(sock)
                return
        except (OSError, ValueError):
            self._close_socket(sock)
            return
        key = (host, port, use_tls)
        with self._lock:
            bucket = self._pool.get(key)
            if bucket is None:
                bucket = []
                self._pool[key] = bucket
            if len(bucket) < self.MAX_POOL_PER_HOST:
                bucket.append((sock, time.monotonic()))
            else:
                self._close_socket(sock)

    def discard(self, sock: socket.socket) -> None:
        self._close_socket(sock)

    @staticmethod
    def _close_socket(sock: Optional[socket.socket]) -> None:
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

    def close_all(self) -> None:
        with self._lock:
            for conns in self._pool.values():
                for item in conns:
                    self._close_socket(item[0])
            self._pool.clear()
        with self._sorted_lock:
            self._sorted_cache.clear()

    @staticmethod
    def _address_for(sockaddr: tuple, port: int) -> tuple:
        try:
            if len(sockaddr) == 2:
                return (sockaddr[0], port)
            if len(sockaddr) == 4:
                return (sockaddr[0], port, sockaddr[2], sockaddr[3])
        except (IndexError, TypeError):
            pass
        return sockaddr