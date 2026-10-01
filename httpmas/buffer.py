"""
buffer.py - Pre-allocated zero-copy ring buffer.

Kỹ thuật C-Level:
- bytearray pre-allocated, không tạo object mới mỗi lần nối.
- sock.recv_into(memoryview) đổ dữ liệu thẳng từ C socket vào RAM.
- Native boundary search qua ctypes (memchr/memmem), fallback bytes.find().
"""

import ctypes
import ctypes.util
from typing import Optional

class _NativeSearch:
    """Load libc/bionic/msvcrt và expose memchr/memmem."""

    __slots__ = (
        "available", "memchr", "memmem",
        "_lib", "_needle_cache",
    )

    def __init__(self) -> None:
        self.available = False
        self.memchr = None
        self.memmem = None
        self._lib = None
        self._needle_cache = {}
        self._load()

    def _load(self) -> None:
        candidates = []
        try:
            name = ctypes.util.find_library("c")
            if name:
                candidates.append(name)
        except Exception:
            pass

        candidates.extend(
            [
                "libc.so.6",       # Linux / Android (Termux)
                "libc.so",         # Android
                "libSystem.B.dylib",  # macOS
                "msvcrt.dll",      # Windows
            ]
        )

        for cand in candidates:
            try:
                lib = ctypes.CDLL(cand)
            except OSError:
                continue

            memchr = None
            memmem = None

            try:
                memchr = lib.memchr
                memchr.restype = ctypes.c_void_p
                memchr.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_size_t,
                ]
            except AttributeError:
                memchr = None

            try:
                memmem = lib.memmem
                memmem.restype = ctypes.c_void_p
                memmem.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    ctypes.c_size_t,
                ]
            except AttributeError:
                memmem = None

            if memchr is not None:
                self.memchr = memchr
                self.memmem = memmem
                self._lib = lib
                self.available = True
                return

    def needle(self, sub: bytes):
        """Cache needle để tránh copy lại mỗi lần search."""
        cached = self._needle_cache.get(sub)
        if cached is None:
            cached = ctypes.create_string_buffer(sub, len(sub))
            self._needle_cache[sub] = cached
        return cached


_NATIVE = _NativeSearch()
USE_NATIVE = True
_MIN_NATIVE_LEN = 16


def c_find(buf: bytearray, sub: bytes, start: int, end: int) -> int:
    """Tìm sub trong buf[start:end]."""
    n = end - start
    m = len(sub)

    if n <= 0:
        return -1 if m else start
    if m == 0:
        return start
    if m > n:
        return -1

    if USE_NATIVE and _NATIVE.available and n >= _MIN_NATIVE_LEN:
        if m == 1 and _NATIVE.memchr is not None:
            idx = _memchr_find(buf, sub[0], start, n)
            if idx >= -1:
                return idx
        elif m > 1 and _NATIVE.memmem is not None:
            idx = _memmem_find(buf, sub, start, n)
            if idx >= -1:
                return idx
    return buf.find(sub, start, end)


def _memchr_find(buf: bytearray, ch: int, start: int, n: int) -> int:
    """memchr(buf + start, ch, n). Trả absolute index hoặc -1."""
    arr = None
    try:
        arr = (ctypes.c_char * n).from_buffer(buf, start)
        base = ctypes.addressof(arr)
        res = _NATIVE.memchr(base, ch, n)
        if not res:
            return -1
        return start + (res - base)
    except Exception:
        return -2
    finally:
        arr = None


def _memmem_find(buf: bytearray, sub: bytes, start: int, n: int) -> int:
    """memmem(buf + start, n, sub, len(sub))."""
    arr = None
    try:
        m = len(sub)
        arr = (ctypes.c_char * n).from_buffer(buf, start)
        base = ctypes.addressof(arr)
        needle = _NATIVE.needle(sub)
        res = _NATIVE.memmem(base, n, ctypes.addressof(needle), m)
        if not res:
            return -1
        return start + (res - base)
    except Exception:
        return -2
    finally:
        arr = None

class ByteBuffer:
    """Bộ đệm bytearray pre-allocated.

    - recv() dùng sock.recv_into(memoryview) zero-copy.
    - find() dùng memchr/memmem hoặc bytes.find().
    - read()/read_until() trả bytes, không giữ memoryview lâu.
    """

    __slots__ = ("_buf", "_start", "_end")

    def __init__(self, capacity: int = 1 << 20) -> None:
        self._buf = bytearray(capacity)
        self._start = 0
        self._end = 0

    def __len__(self) -> int:
        return self._end - self._start

    def _ensure(self, needed: int) -> None:
        if len(self._buf) - self._end >= needed:
            return

        self._compact()

        if len(self._buf) - self._end >= needed:
            return

        new_cap = max(len(self._buf) * 2, self._end + needed)
        new_buf = bytearray(new_cap)
        n = self._end - self._start
        if n:
            new_buf[:n] = self._buf[self._start:self._end]

        self._buf = new_buf
        self._end = n
        self._start = 0

    def _compact(self) -> None:
        if self._start == 0:
            return
        n = self._end - self._start
        if n:
            self._buf[:n] = self._buf[self._start:self._end]
        self._end = n
        self._start = 0

    def _maybe_compact(self) -> None:
        if self._start > 4096 and self._start * 2 > self._end:
            self._compact()

    def extend(self, data: bytes) -> None:
        """Append data (compat API)."""
        if not data:
            return
        self._ensure(len(data))
        n = len(data)
        self._buf[self._end:self._end + n] = data
        self._end += n

    def recv(self, sock, max_bytes: int = 65536) -> int:
        """Zero-copy recv: socket C đổ thẳng vào bytearray."""
        self._ensure(max_bytes)

        mv = memoryview(self._buf)
        view = mv[self._end:self._end + max_bytes]
        try:
            n = sock.recv_into(view)
        finally:
            view.release()
            mv.release()

        if n > 0:
            self._end += n
        return n

    def find(self, sub: bytes) -> int:
        """Tìm sub, trả index relative so với start, -1 nếu không thấy."""
        idx = c_find(self._buf, sub, self._start, self._end)
        if idx < 0:
            return -1
        return idx - self._start

    def read(self, count: Optional[int] = None) -> bytes:
        if count is None:
            end = self._end
        else:
            end = min(self._start + count, self._end)

        data = bytes(self._buf[self._start:end])
        self._start = end
        self._maybe_compact()
        return data

    def read_until(self, sep: bytes) -> Optional[bytes]:
        idx = self.find(sep)
        if idx < 0:
            return None

        start = self._start
        end = start + idx
        data = bytes(self._buf[start:end])
        self._start = end + len(sep)
        self._maybe_compact()
        return data

    def peek(self, count: Optional[int] = None) -> bytes:
        if count is None:
            end = self._end
        else:
            end = min(self._start + count, self._end)
        return bytes(self._buf[self._start:end])

    def skip(self, n: int) -> None:
        self._start = min(self._start + n, self._end)
        self._maybe_compact()
