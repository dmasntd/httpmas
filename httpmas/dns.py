"""
DNS cache nội bộ cho httpmas.

Nâng cấp chống fail:
- Retry resolve khi fail.
- Single-flight async.
- IPv4 priority.
"""

import asyncio
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import RequestsError


class DNSCache:
    """Cache kết quả DNS nội bộ."""

    __slots__ = (
        "_ttl", "_max_entries", "_lock",
        "_cache", "_async_inflight",
    )

    LOOKUP_RETRIES = 2

    def __init__(
        self,
        ttl: float = 300.0,
        max_entries: int = 1024,
    ) -> None:
        self._ttl = float(ttl)
        self._max_entries = int(max_entries)
        self._lock = threading.RLock()
        self._cache: Dict[str, Tuple[float, List]] = {}
        self._async_inflight: Dict[Tuple[Any, str], asyncio.Future] = {}

    @staticmethod
    def _strip_host(host: str) -> str:
        host = host.strip()
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host

    @staticmethod
    def _filter_addrinfos(infos: List, family: int = 0) -> List:
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
        return sorted(
            infos,
            key=lambda info: 0 if info[0] == socket.AF_INET else 1,
        )

    def _lookup_sync(self, host: str) -> List:
        """Resolve DNS với retry."""
        last_error = None

        for _ in range(self.LOOKUP_RETRIES + 1):
            try:
                return socket.getaddrinfo(
                    host,
                    None,
                    family=0,
                    type=socket.SOCK_STREAM,
                )
            except socket.gaierror as exc:
                last_error = exc
            except OSError as exc:
                last_error = exc

        raise RequestsError(
            f"Không phân giải được DNS cho {host}: {last_error}",
            print_error=False,
        )

    def _get_cached(self, key: str) -> Optional[List]:
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
        expired_keys = [
            key for key, (expires_at, _) in self._cache.items()
            if expires_at < now
        ]
        for key in expired_keys:
            self._cache.pop(key, None)

    def _store_locked(self, key: str, infos: List) -> None:
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
        with self._lock:
            self._store_locked(key, infos)

    def resolve(self, host: str, family: int = 0) -> List:
        host = self._strip_host(host)

        if not host:
            raise RequestsError(
                "DNSCache: hostname rỗng",
                print_error=False,
            )

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
        """Resolve DNS async với single-flight + retry."""
        host = self._strip_host(host)

        if not host:
            raise RequestsError(
                "DNSCache: hostname rỗng",
                print_error=False,
            )

        loop = asyncio.get_running_loop()
        key = host.lower()

        if self._ttl <= 0:
            return await self._async_lookup_with_retry(
                loop, host, family
            )

        infos = self._get_cached(key)
        if infos is not None:
            return self._filter_addrinfos(infos, family)

        owner = False
        inflight_key = (loop, key)

        with self._lock:
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
            infos = await self._async_lookup_with_retry(
                loop, host, family
            )
            self._put_cached(key, infos)

            if not future.done():
                future.set_result(infos)

            return self._filter_addrinfos(infos, family)

        except Exception as exc:
            err = RequestsError(
                str(exc),
                print_error=False,
            )
            if not future.done():
                future.set_exception(err)
            raise err

        finally:
            with self._lock:
                self._async_inflight.pop(inflight_key, None)

    async def _async_lookup_with_retry(
        self,
        loop: asyncio.AbstractEventLoop,
        host: str,
        family: int = 0,
    ) -> List:
        """Async DNS lookup với retry."""
        last_error = None

        for _ in range(self.LOOKUP_RETRIES + 1):
            try:
                infos = await loop.getaddrinfo(
                    host,
                    None,
                    family=family,
                    type=socket.SOCK_STREAM,
                )
                if infos:
                    return infos
            except socket.gaierror as exc:
                last_error = exc
            except OSError as exc:
                last_error = exc

        raise RequestsError(
            f"Không phân giải được DNS cho {host}: {last_error}",
            print_error=False,
        )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def expire(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired_keys = [
                key for key, (expires_at, _) in self._cache.items()
                if expires_at < now
            ]
            for key in expired_keys:
                self._cache.pop(key, None)
            return len(expired_keys)