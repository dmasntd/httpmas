import asyncio
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import RequestsError


class DNSCache:
    """Cache kết quả DNS nội bộ."""

    def __init__(
        self,
        ttl: float = 300.0,
        max_entries: int = 1024,
    ) -> None:
        """Khởi tạo DNSCache.

        Tham số:
            ttl: Thời gian sống của cache (giây).
            max_entries: Số hostname tối đa được cache.
        """
        self._ttl = float(ttl)
        self._max_entries = int(max_entries)
        self._lock = threading.RLock()
        self._cache: Dict[str, Tuple[float, List]] = {}
        self._async_inflight: Dict[Tuple[Any, str], asyncio.Future] = {}

    @staticmethod
    def _strip_host(host: str) -> str:
        """Chuẩn hóa hostname, bỏ dấu ngoặc IPv6 nếu có."""
        host = host.strip()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host

    @staticmethod
    def _filter_addrinfos(infos: List, family: int = 0) -> List:
        """Lọc addrinfo, ưu tiên SOCK_STREAM và family nếu chỉ định."""
        out = []

        for info in infos:
            try:
                if info[1] != socket.SOCK_STREAM:
                    continue

                if family and info[0] != family:
                    continue

                out.append(info)
            except (IndexError, TypeError):
                continue

        return out if out else list(infos)

    @staticmethod
    def sort_ipv4_first(infos: List) -> List:
        """Ưu tiên IPv4 trước để hợp với mạng di động / NAT yếu."""
        return sorted(
            infos,
            key=lambda info: 0 if info[0] == socket.AF_INET else 1,
        )

    def _lookup_sync(self, host: str) -> List:
        """Resolve DNS đồng bộ."""
        try:
            return socket.getaddrinfo(
                host,
                None,
                family=0,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise RequestsError(
                f"Không phân giải được DNS cho {host}: {exc}"
            )
        except OSError as exc:
            raise RequestsError(
                f"Lỗi DNS khi phân giải {host}: {exc}"
            )

    def _get_cached(self, key: str) -> Optional[List]:
        """Lấy cache nếu còn hạn."""
        now = time.monotonic()

        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None

            expires_at, infos = item
            if expires_at < now:
                self._cache.pop(key, None)
                return None

            return infos

    def _expire_locked(self, now: float) -> None:
        """Xóa entry hết hạn. Phải gọi khi đang giữ self._lock."""
        expired_keys = [
            key for key, (expires_at, _) in self._cache.items()
            if expires_at < now
        ]

        for key in expired_keys:
            self._cache.pop(key, None)

    def _store_locked(self, key: str, infos: List) -> None:
        """Lưu cache. Phải gọi khi đang giữ self._lock."""
        now = time.monotonic()
        self._expire_locked(now)

        if len(self._cache) >= self._max_entries:
            try:
                oldest_key = min(
                    self._cache.items(),
                    key=lambda item: item[1][0],
                )[0]
                self._cache.pop(oldest_key, None)
            except ValueError:
                pass

        self._cache[key] = (now + self._ttl, infos)

    def _put_cached(self, key: str, infos: List) -> None:
        """Thread-safe put cache."""
        with self._lock:
            self._store_locked(key, infos)

    def resolve(self, host: str, family: int = 0) -> List:
        """Resolve DNS cho sync.

        Tham số:
            host: Hostname cần phân giải.
            family: Socket family tùy chọn, mặc định 0.

        Trả về:
            Danh sách addrinfo.
        """
        host = self._strip_host(host)

        if not host:
            raise RequestsError("DNSCache: hostname rỗng")

        if self._ttl <= 0:
            infos = self._lookup_sync(host)
            return self._filter_addrinfos(infos, family)

        key = host.lower()
        infos = self._get_cached(key)

        if infos is None:
            infos = self._lookup_sync(host)
            self._put_cached(key, infos)

        return self._filter_addrinfos(infos, family)

    async def async_resolve(self, host: str, family: int = 0) -> List:
        """Resolve DNS cho async, không block event loop.

        Single-flight:
        - Cache miss thì chỉ tạo 1 future cho host đó trên loop hiện tại.
        - Nhiều request cùng host sẽ await cùng future.
        """
        host = self._strip_host(host)

        if not host:
            raise RequestsError("DNSCache: hostname rỗng")

        loop = asyncio.get_running_loop()
        key = host.lower()

        if self._ttl <= 0:
            try:
                infos = await loop.getaddrinfo(
                    host,
                    None,
                    family=0,
                    type=socket.SOCK_STREAM,
                )
            except socket.gaierror as exc:
                raise RequestsError(
                    f"Không phân giải được DNS cho {host}: {exc}"
                )
            except OSError as exc:
                raise RequestsError(
                    f"Lỗi DNS khi phân giải {host}: {exc}"
                )

            return self._filter_addrinfos(infos, family)

        """Kiểm tra cache trước."""
        infos = self._get_cached(key)
        if infos is not None:
            return self._filter_addrinfos(infos, family)

        owner = False
        inflight_key = (loop, key)

        with self._lock:
            """Kiểm tra lại cache trong lock để tránh race."""
            infos = self._get_cached(key)
            if infos is not None:
                return self._filter_addrinfos(infos, family)

            future = self._async_inflight.get(inflight_key)

            if future is None:
                future = loop.create_future()
                self._async_inflight[inflight_key] = future
                owner = True

        if not owner:
            infos = await future
            return self._filter_addrinfos(infos, family)

        try:
            infos = await loop.getaddrinfo(
                host,
                None,
                family=0,
                type=socket.SOCK_STREAM,
            )

            self._put_cached(key, infos)

            if not future.done():
                future.set_result(infos)

            return self._filter_addrinfos(infos, family)

        except socket.gaierror as exc:
            err = RequestsError(
                f"Không phân giải được DNS cho {host}: {exc}"
            )

            if not future.done():
                future.set_exception(err)

            raise err

        except OSError as exc:
            err = RequestsError(
                f"Lỗi DNS khi phân giải {host}: {exc}"
            )

            if not future.done():
                future.set_exception(err)

            raise err

        finally:
            with self._lock:
                self._async_inflight.pop(inflight_key, None)

    def clear(self) -> None:
        """Xóa toàn bộ DNS cache."""
        with self._lock:
            self._cache.clear()

    def expire(self) -> int:
        """Xóa các entry đã hết hạn.

        Trả về:
            Số entry đã xóa.
        """
        now = time.monotonic()

        with self._lock:
            expired_keys = [
                key for key, (expires_at, _) in self._cache.items()
                if expires_at < now
            ]

            for key in expired_keys:
                self._cache.pop(key, None)

            return len(expired_keys)
