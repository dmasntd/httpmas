"""
HTTP/1.1 parser sync — C-accelerated khi có _httpmas_fast.
"""
import socket
import ssl as _ssl
import time
from typing import Dict, Optional, Tuple

from .exceptions import RequestsError
from ._fast import find_header_end, parse_response_head, parse_chunk_size

_MAX_HEAD = 131072
_MAX_CHUNK = 67108864
_MAX_BUFFER = 268435456
_READ_BLOCK = 65536


class HTTPParser:
    __slots__ = (
        "_sock", "_buf", "_offset", "_length",
        "_method", "_start_time", "header_elapsed",
    )

    def __init__(
        self,
        sock: socket.socket,
        buffer: Optional[bytearray] = None,
        method: str = "GET",
        start_time: Optional[float] = None,
    ) -> None:
        self._sock = sock
        self._method = method.upper()
        self._start_time = start_time if start_time is not None else time.monotonic()
        self.header_elapsed = 0.0
        if buffer is not None and isinstance(buffer, bytearray) and len(buffer) >= _READ_BLOCK:
            self._buf = buffer
        else:
            self._buf = bytearray(_READ_BLOCK)
        self._offset = 0
        self._length = 0

    @property
    def _available(self) -> int:
        return self._length - self._offset

    @property
    def has_pending(self) -> bool:
        if self._length > self._offset:
            remaining = bytes(self._buf[self._offset:self._length])
            if remaining.strip(b"\r\n \t") == b"":
                self._offset = self._length
                return False
            return True
        return False

    def take_buffer(self, max_size: int = 1 << 20) -> Optional[bytearray]:
        if self.has_pending:
            return None
        if len(self._buf) > max_size:
            return None
        buf = self._buf
        self._buf = bytearray(0)
        self._offset = 0
        self._length = 0
        return buf

    def _compact(self) -> None:
        if self._offset == 0:
            return
        n = self._length - self._offset
        if n:
            self._buf[:n] = self._buf[self._offset:self._length]
        self._length = n
        self._offset = 0

    def _maybe_compact(self) -> None:
        if self._offset > 8192 and self._offset * 2 > self._length:
            self._compact()

    def _ensure(self, needed: int) -> None:
        if len(self._buf) - self._length >= needed:
            return
        self._compact()
        if len(self._buf) - self._length >= needed:
            return
        new_cap = max(len(self._buf) * 2, self._length + needed)
        if new_cap > _MAX_BUFFER:
            raise RequestsError("Response vượt giới hạn bộ nhớ parser (256MB)", print_error=False)
        new_buf = bytearray(new_cap)
        n = self._length - self._offset
        if n:
            new_buf[:n] = self._buf[self._offset:self._length]
        self._buf = new_buf
        self._length = n
        self._offset = 0

    def _recv_more(self, min_bytes: int = 0) -> bool:
        want = max(_READ_BLOCK, min_bytes)
        self._ensure(want)
        mv = memoryview(self._buf)
        view = mv[self._length:self._length + want]
        try:
            n = self._sock.recv_into(view)
        except socket.timeout:
            raise RequestsError("Hết thời gian chờ khi đọc dữ liệu từ máy chủ", print_error=False)
        except _ssl.SSLWantReadError:
            return False
        except OSError as exc:
            raise RequestsError(f"Lỗi khi đọc dữ liệu: {exc}", print_error=False)
        finally:
            view.release()
            mv.release()
        if n > 0:
            self._length += n
            return True
        return False

    def _read_head(self) -> bytes:
        while True:
            idx = find_header_end(self._buf, self._offset, self._length)
            if idx >= 0:
                head = bytes(self._buf[self._offset:idx])
                self._offset = idx + 4
                self._maybe_compact()
                return head
            if self._length - self._offset > _MAX_HEAD:
                raise RequestsError("Header quá lớn (>128KB)", print_error=False)
            if not self._recv_more():
                raise RequestsError("Kết nối bị đóng khi đang đọc header", print_error=False)

    def _read_line(self) -> bytes:
        while True:
            idx = self._buf.find(b"\r\n", self._offset, self._length)
            if idx >= 0:
                line = bytes(self._buf[self._offset:idx])
                self._offset = idx + 2
                self._maybe_compact()
                return line
            if not self._recv_more():
                if self._available:
                    line = bytes(self._buf[self._offset:self._length])
                    self._offset = self._length
                    return line
                raise RequestsError("Kết nối bị đóng khi đang đọc header", print_error=False)

    def _read_bytes(self, count: int) -> bytes:
        if count < 0:
            raise RequestsError(f"Content-Length âm bất thường: {count}", print_error=False)
        if count > _MAX_BUFFER:
            raise RequestsError("Body vượt giới hạn bộ nhớ parser (256MB)", print_error=False)
        while self._available < count:
            if not self._recv_more(count - self._available):
                break
        if self._available < count:
            raise RequestsError(
                f"Kết nối bị đóng khi đang đọc body (cần {count} byte, nhận {self._available} byte)",
                print_error=False,
            )
        data = bytes(self._buf[self._offset:self._offset + count])
        self._offset += count
        self._maybe_compact()
        return data

    def parse(self) -> Tuple[int, str, Dict[str, str], bytes, bool]:
        head_bytes = self._read_head()
        self.header_elapsed = time.monotonic() - self._start_time

        # C-accelerated hoặc Python fallback — cùng API
        try:
            status_code, reason, http_version, headers = parse_response_head(head_bytes)
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
        if "close" in connection_header:
            should_close = True
        elif http_version == "HTTP/1.0" and "keep-alive" not in connection_header:
            should_close = True
        elif not no_body and "chunked" not in transfer_encoding and not content_length:
            should_close = True

        body = b"" if no_body else self._read_body(headers)
        return status_code, reason, headers, body, should_close

    def _read_body(self, headers: Dict[str, str]) -> bytes:
        transfer_encoding = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer_encoding:
            return self._read_chunked_body()
        content_length_str = headers.get("content-length", "")
        if content_length_str:
            try:
                length = int(content_length_str)
            except ValueError:
                raise RequestsError(f"Content-Length không hợp lệ: {content_length_str}", print_error=False)
            if length == 0:
                return b""
            return self._read_bytes(length)
        return self._read_until_close()

    def _read_chunked_body(self) -> bytes:
        chunks = []
        total = 0
        while True:
            size_line = self._read_line()
            try:
                chunk_size = parse_chunk_size(size_line)
            except (ValueError, Exception) as exc:
                raise RequestsError(f"Chunk size không hợp lệ: {exc}", print_error=False)
            if chunk_size == 0:
                while True:
                    trailer = self._read_line()
                    if not trailer:
                        break
                break
            if chunk_size > _MAX_CHUNK or total + chunk_size > _MAX_CHUNK:
                raise RequestsError("Chunk vượt giới hạn 64MB", print_error=False)
            chunk_data = self._read_bytes(chunk_size)
            if chunk_data:
                chunks.append(chunk_data)
                total += len(chunk_data)
            self._read_line()
        if not chunks:
            return b""
        if len(chunks) == 1:
            return chunks[0]
        return b"".join(chunks)

    def _read_until_close(self) -> bytes:
        while self._recv_more():
            pass
        data = bytes(self._buf[self._offset:self._length])
        self._offset = self._length
        return data